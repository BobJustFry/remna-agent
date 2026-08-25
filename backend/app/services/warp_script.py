"""Install WARP (wgcf + wg-quick iface `warp`) on a node over SSH."""

from __future__ import annotations

import shlex
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

from app.services.agent_install import (
    AgentInstallCancelled,
    AgentInstallError,
    Privilege,
    _check_cancel,
    _combined_text,
    _detect_privilege,
    _looks_like_shell_denied,
    _run,
    _shell_usable,
    _ssh_candidates,
    _yield_output,
)
from app.services.remnanode_script import RemnaScriptError, _stream_priv_command
from app.services.ssh_client import SshConnectError, probe_tcp, ssh_connect
from app.services.wgcf_releases import (
    WGCF_FALLBACK_VERSION,
    get_wgcf_binary,
    goarch_from_uname,
    register_wgcf_account,
)

DOCKER_SCRIPTS_DIR = Path("/app/scripts/warp")
REPO_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts" / "warp"


class WarpScriptError(RemnaScriptError):
    pass


def runner_path() -> Path:
    for base in (DOCKER_SCRIPTS_DIR, REPO_SCRIPTS_DIR):
        path = base / "warp_runner.sh"
        if path.is_file():
            return path
    raise WarpScriptError("Скрипт WARP не найден в образе/репозитории")


def run_warp_script_via_ssh(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    force: bool = False,
    wgcf_version: str | None = None,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    script_body = runner_path().read_text(encoding="utf-8").replace("\r\n", "\n")

    _check_cancel(cancel)
    yield f"→ Проверка TCP {host}:{ssh_port}…"
    try:
        probe_tcp(host, ssh_port, timeout=10.0)
    except SshConnectError as exc:
        yield f"✗ {exc.message}"
        raise WarpScriptError(exc.message) from exc
    yield f"✓ Порт {ssh_port} открыт"

    last_error: str | None = None
    for idx, user in enumerate(_ssh_candidates(username)):
        _check_cancel(cancel)
        yield f"→ SSH {user}@{host}:{ssh_port}…"
        try:
            client_cm = ssh_connect(
                host=host,
                port=ssh_port,
                username=user,
                auth_type=auth_type,
                password=password,
                private_key=private_key,
            )
        except SshConnectError as exc:
            last_error = exc.message
            yield f"✗ {exc.message}"
            if idx + 1 < len(_ssh_candidates(username)):
                continue
            raise WarpScriptError(exc.message) from exc

        try:
            with client_cm as client:
                yield "✓ SSH-сессия открыта"
                code, out, err = _run(client, "id -u; id -un")
                yield from _yield_output(out, err)
                if not _shell_usable(out, err, code):
                    denied = _looks_like_shell_denied(_combined_text(out, err))
                    last_error = denied or (out or err or "shell недоступен").strip()
                    yield f"✗ {last_error}"
                    if idx + 1 < len(_ssh_candidates(username)):
                        continue
                    raise WarpScriptError(last_error)

                priv: Privilege | None = None
                try:
                    for item in _detect_privilege(client, password=password):
                        _check_cancel(cancel)
                        if isinstance(item, Privilege):
                            priv = item
                        else:
                            yield item
                except AgentInstallError as exc:
                    raise WarpScriptError(str(exc)) from exc
                if priv is None:
                    raise WarpScriptError("Не удалось определить права на ноде")

                staging = f"/tmp/warp-run-{uuid.uuid4().hex[:10]}"
                yield f"$ mkdir -p {staging}"
                try:
                    _run(client, f"mkdir -p {shlex.quote(staging)}", check=True)
                except AgentInstallError as exc:
                    raise WarpScriptError(exc.message) from exc

                remote_script = f"{staging}/warp_runner.sh"
                remote_wgcf = f"{staging}/wgcf"
                remote_account = f"{staging}/wgcf-account.toml"
                remote_profile = f"{staging}/wgcf-profile.conf"
                yield "→ Загрузка warp_runner.sh…"
                sftp = client.open_sftp()
                try:
                    with sftp.file(remote_script, "w") as f:
                        f.write(script_body)
                finally:
                    sftp.close()
                _run(client, f"chmod 700 {shlex.quote(remote_script)}", check=True)
                yield f"✓ {remote_script}"

                code, uname_out, _err = _run(client, "uname -m")
                goarch = goarch_from_uname(uname_out or "")
                bundled = False
                account_bundled = False
                profile_bundled = False
                ver = (wgcf_version or WGCF_FALLBACK_VERSION).lstrip("v")
                if goarch:
                    try:
                        blob, cached = get_wgcf_binary(ver, goarch)
                        if cached:
                            yield f"→ wgcf v{ver} ({goarch}) из кэша панели"
                        else:
                            yield f"→ Скачиваю wgcf v{ver} ({goarch}) на панели (сохраняю в кэш)…"
                        sftp = client.open_sftp()
                        try:
                            with sftp.file(remote_wgcf, "wb") as f:
                                f.write(blob)
                        finally:
                            sftp.close()
                        _run(client, f"chmod 755 {shlex.quote(remote_wgcf)}", check=True)
                        bundled = True
                        yield f"✓ wgcf {len(blob)} байт → {remote_wgcf}"
                    except Exception as exc:  # noqa: BLE001
                        yield f"⚠ Не удалось взять wgcf на панели: {exc}"
                        yield "→ Нода попробует GitHub сама"

                try:
                    yield "→ Регистрация WARP на панели (не на ноде — api.cloudflareclient.com с РФ часто недоступен)…"
                    acc_blob, prof_blob = register_wgcf_account(ver)
                    sftp = client.open_sftp()
                    try:
                        with sftp.file(remote_account, "w") as f:
                            f.write(acc_blob.decode("utf-8", "replace"))
                        with sftp.file(remote_profile, "w") as f:
                            f.write(prof_blob.decode("utf-8", "replace"))
                    finally:
                        sftp.close()
                    account_bundled = True
                    profile_bundled = True
                    yield "✓ аккаунт и профиль wgcf созданы на панели"
                except Exception as exc:  # noqa: BLE001
                    yield f"⚠ Регистрация на панели не вышла: {exc}"
                    yield "→ Нода попробует register сама"

                force_v = "1" if force else "0"
                env_parts = [
                    f"export FORCE={shlex.quote(force_v)}",
                    f"export WGCF_VERSION={shlex.quote(ver)}",
                    "export NEEDRESTART_SUSPEND=1",
                    "export NEEDRESTART_MODE=l",
                ]
                if bundled:
                    env_parts.append(f"export WGCF_BUNDLE={shlex.quote(remote_wgcf)}")
                if account_bundled:
                    env_parts.append(f"export WGCF_ACCOUNT={shlex.quote(remote_account)}")
                if profile_bundled:
                    env_parts.append(f"export WGCF_PROFILE={shlex.quote(remote_profile)}")
                run_cmd = (
                    "\n".join(env_parts)
                    + "\n"
                    + f"bash {shlex.quote(remote_script)}\n"
                    + "ec=$?\n"
                    + f"rm -rf {shlex.quote(staging)}\n"
                    + "exit $ec"
                )
                yield f"$ FORCE={force_v} WGCF_VERSION={ver} bash warp_runner.sh"
                try:
                    for line in _stream_priv_command(
                        client, priv, run_cmd, timeout=3600.0, cancel=cancel
                    ):
                        yield line
                except AgentInstallCancelled:
                    raise
                except RemnaScriptError as exc:
                    raise WarpScriptError(exc.message) from exc
                except Exception as exc:  # noqa: BLE001
                    if cancel and cancel.is_set():
                        raise AgentInstallCancelled() from exc
                    raise WarpScriptError(str(exc) or type(exc).__name__) from exc

                yield "✓ WARP установлен"
                return
        except (WarpScriptError, AgentInstallCancelled, AgentInstallError):
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc) or type(exc).__name__
            yield f"✗ {last_error}"
            if idx + 1 < len(_ssh_candidates(username)):
                continue
            raise WarpScriptError(last_error) from exc

    raise WarpScriptError(last_error or "Не удалось установить WARP")
