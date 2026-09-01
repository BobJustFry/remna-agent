"""Run remnanode_runner.sh on a node over SSH with live log lines."""

from __future__ import annotations

import shlex
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.services.agent_install import (
    AgentInstallCancelled,
    AgentInstallError,
    Privilege,
    _check_cancel,
    _detect_privilege,
    _run,
    with_ready_ssh,
)
from app.services.ssh_client import SshConnectError, probe_tcp

DOCKER_SCRIPTS_DIR = Path("/app/scripts/remnanode")
REPO_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts" / "remnanode"


class RemnaScriptError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class RemnaScriptParams:
    action: str  # install | reinstall | tune | update
    node_port: int = 2222
    secret_key: str = ""
    additional_ports: str = ""
    mtu_ddos: bool = True
    gaming: bool = True
    swap: bool = True
    swap_size: str = "1G"
    cache_size: str = "1G"
    disable_ipv6: bool = True
    use_origin: bool = False
    origin_domain: str = ""
    tune_mtu: str = "skip"  # on|off|skip
    tune_gaming: str = "skip"
    tune_swap: str = "skip"
    tune_ports: bool = False
    tune_ipv6: str = "skip"  # disable|enable|skip
    skip_system_update: bool = True
    cf_204_stub: bool = False


def scripts_dir() -> Path:
    if DOCKER_SCRIPTS_DIR.is_dir():
        return DOCKER_SCRIPTS_DIR
    if REPO_SCRIPTS_DIR.is_dir():
        return REPO_SCRIPTS_DIR
    raise RemnaScriptError("Скрипты RemnaNode не найдены в образе/репозитории")


def runner_path() -> Path:
    path = scripts_dir() / "remnanode_runner.sh"
    if not path.is_file():
        raise RemnaScriptError(f"Не найден {path}")
    return path


def _env_exports(params: RemnaScriptParams) -> str:
    def b(v: bool) -> str:
        return "1" if v else "0"

    pairs = {
        "ACTION": params.action,
        "NODE_PORT": str(params.node_port),
        "SECRET_KEY": params.secret_key,
        "ADDITIONAL_PORTS": params.additional_ports,
        "MTU_DDOS": b(params.mtu_ddos),
        "GAMING": b(params.gaming),
        "SWAP": b(params.swap),
        "SWAP_SIZE": params.swap_size,
        "CACHE_SIZE": params.cache_size,
        "DISABLE_IPV6": b(params.disable_ipv6),
        "USE_ORIGIN": b(params.use_origin),
        "ORIGIN_DOMAIN": params.origin_domain or "",
        "TUNE_MTU": params.tune_mtu,
        "TUNE_GAMING": params.tune_gaming,
        "TUNE_SWAP": params.tune_swap,
        "TUNE_PORTS": b(params.tune_ports),
        "TUNE_IPV6": params.tune_ipv6,
        "SKIP_SYSTEM_UPDATE": b(params.skip_system_update),
    }
    parts: list[str] = []
    for key, value in pairs.items():
        parts.append(f"export {key}={shlex.quote(value)}")
    return "\n".join(parts)


def _stream_priv_command(
    client,
    priv: Privilege,
    command: str,
    *,
    timeout: float,
    cancel: threading.Event | None,
) -> Iterator[str]:
    wrapped = priv.wrap(command)
    stdin_data = priv.stdin()
    chan = client.get_transport().open_session()
    chan.settimeout(timeout)
    chan.get_pty()
    chan.exec_command(wrapped)
    if stdin_data:
        chan.send(stdin_data)
        try:
            chan.shutdown_write()
        except Exception:  # noqa: BLE001
            pass

    buf = ""
    while True:
        _check_cancel(cancel)
        if chan.recv_ready():
            chunk = chan.recv(4096).decode("utf-8", errors="replace")
            if not chunk:
                break
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                yield line.rstrip("\r")
        elif chan.exit_status_ready():
            break
        else:
            # small wait via status poll
            if chan.recv_stderr_ready():
                err = chan.recv_stderr(4096).decode("utf-8", errors="replace")
                buf += err
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    yield line.rstrip("\r")
            else:
                threading.Event().wait(0.15)

    while chan.recv_ready():
        buf += chan.recv(4096).decode("utf-8", errors="replace")
    while chan.recv_stderr_ready():
        buf += chan.recv_stderr(4096).decode("utf-8", errors="replace")
    if buf.strip():
        for line in buf.splitlines():
            yield line.rstrip("\r")

    code = chan.recv_exit_status()
    if code != 0:
        raise RemnaScriptError(f"Скрипт завершился с кодом {code}")


def run_remnanode_script_via_ssh(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    params: RemnaScriptParams,
    cancel: threading.Event | None = None,
    resolved_password: list[str] | None = None,
) -> Iterator[str]:
    script_body = runner_path().read_text(encoding="utf-8")
    if params.action in ("install", "reinstall") and not params.secret_key.strip():
        raise RemnaScriptError("SECRET_KEY обязателен для установки RemnaNode")

    _check_cancel(cancel)
    yield f"→ Проверка TCP {host}:{ssh_port}…"
    try:
        probe_tcp(host, ssh_port, timeout=10.0)
    except SshConnectError as exc:
        yield f"✗ {exc.message}"
        raise RemnaScriptError(exc.message) from exc
    yield f"✓ Порт {ssh_port} открыт"

    def work(client, user: str, ssh_password: str | None) -> Iterator[str]:
        priv: Privilege | None = None
        try:
            for item in _detect_privilege(client, password=ssh_password):
                _check_cancel(cancel)
                if isinstance(item, Privilege):
                    priv = item
                else:
                    yield item
        except AgentInstallError as exc:
            raise RemnaScriptError(str(exc)) from exc
        if priv is None:
            raise RemnaScriptError("Не удалось определить права на ноде")

        staging = f"/tmp/remnanode-run-{uuid.uuid4().hex[:10]}"
        yield f"$ mkdir -p {staging}"
        try:
            _run(client, f"mkdir -p {shlex.quote(staging)}", check=True)
        except AgentInstallError as exc:
            raise RemnaScriptError(exc.message) from exc

        remote_script = f"{staging}/remnanode_runner.sh"
        yield "→ Загрузка remnanode_runner.sh…"
        sftp = client.open_sftp()
        try:
            with sftp.file(remote_script, "w") as f:
                f.write(script_body)
        finally:
            sftp.close()
        _run(client, f"chmod 700 {shlex.quote(remote_script)}", check=True)
        yield f"✓ {remote_script}"

        env_block = _env_exports(params)
        run_cmd = (
            f"{env_block}\n"
            f"bash {shlex.quote(remote_script)}\n"
            f"ec=$?\n"
            f"rm -rf {shlex.quote(staging)}\n"
            f"exit $ec"
        )
        yield f"$ ACTION={params.action} NODE_PORT={params.node_port} bash remnanode_runner.sh"
        try:
            for line in _stream_priv_command(
                client, priv, run_cmd, timeout=3600.0, cancel=cancel
            ):
                if "SECRET_KEY=" in line and "export" in line:
                    continue
                yield line
        except AgentInstallCancelled:
            raise
        except RemnaScriptError:
            raise
        except Exception as exc:  # noqa: BLE001
            if cancel and cancel.is_set():
                raise AgentInstallCancelled() from exc
            raise RemnaScriptError(str(exc) or type(exc).__name__) from exc

        yield "✓ Скрипт выполнен"

    try:
        yield from with_ready_ssh(
            host=host,
            ssh_port=ssh_port,
            username=username,
            auth_type=auth_type,
            password=password,
            private_key=private_key,
            work=work,
            cancel=cancel,
            resolved_password=resolved_password,
        )
    except AgentInstallError as exc:
        raise RemnaScriptError(exc.message) from exc
