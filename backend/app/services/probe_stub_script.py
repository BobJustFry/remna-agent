"""Деплой vpn-probe-stub на ноду по SSH + опциональный патч профиля Remnawave."""

from __future__ import annotations

import re
import textwrap
import threading
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
from app.services.probe_stub_profile import patch_profile_for_node
from app.services.remnanode_script import RemnaScriptError, _stream_priv_command
from app.services.remnawave_api import RemnawaveApiError
from app.services.ssh_client import SshConnectError, probe_tcp, ssh_connect
from app.services.ssh_passwd import looks_like_password_expired

DOCKER_SCRIPTS_DIR = Path("/app/scripts/probe_stub")
REPO_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts" / "probe_stub"

REMOTE_DIR = "/opt/vpn-probe-stub"
REMOTE_PY = f"{REMOTE_DIR}/probe_stub.py"
SERVICE = "vpn-probe-stub.service"
STUB_PORT = 19081


class ProbeStubScriptError(RemnaScriptError):
    pass


def stub_src_path() -> Path:
    for base in (DOCKER_SCRIPTS_DIR, REPO_SCRIPTS_DIR):
        path = base / "probe_stub.py"
        if path.is_file():
            return path
    raise ProbeStubScriptError("probe_stub.py не найден в образе/репозитории")


def _unit(listen: str) -> str:
    return textwrap.dedent(
        f"""\
        [Unit]
        Description=VPN probe generate_204 stub (gateway-gated)
        After=network-online.target docker.service
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart=/usr/bin/python3 {REMOTE_PY}
        Environment=GATEWAY=auto
        Environment=LISTEN={listen}
        Restart=on-failure
        RestartSec=3

        [Install]
        WantedBy=multi-user.target
        """
    )


def _docker_bridge_ip(client, priv: Privilege) -> str:
    mode = ""
    for line in _stream_priv_command(
        client,
        priv,
        "docker inspect remnanode --format '{{.HostConfig.NetworkMode}}' 2>/dev/null || true",
        timeout=30.0,
        cancel=None,
    ):
        mode = line.strip()
    if mode == "host":
        return "127.0.0.1"
    out_lines: list[str] = []
    for line in _stream_priv_command(
        client,
        priv,
        "ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1",
        timeout=30.0,
        cancel=None,
    ):
        out_lines.append(line)
    out = "\n".join(out_lines).strip()
    if out and re.match(r"^\d+\.\d+\.\d+\.\d+$", out):
        return out
    return "127.0.0.1"


def run_probe_stub_via_ssh(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    node_name: str,
    patch_profile: bool = True,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    _check_cancel(cancel)
    yield f"→ Заглушка cf_204: проверка TCP {host}:{ssh_port}…"
    try:
        probe_tcp(host, ssh_port, timeout=10.0)
    except SshConnectError as exc:
        yield f"✗ {exc.message}"
        raise ProbeStubScriptError(exc.message) from exc
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
            raise ProbeStubScriptError(exc.message) from exc

        try:
            with client_cm as client:
                yield "✓ SSH-сессия открыта"
                code, out, err = _run(client, "id -u; id -un")
                yield from _yield_output(out, err)
                text = _combined_text(out, err)
                if looks_like_password_expired(text):
                    raise ProbeStubScriptError(
                        "Пароль истёк (PAM требует смену через TTY). "
                        "Сначала установите агент — панель сменит пароль сама."
                    )
                if not _shell_usable(out, err, code):
                    denied = _looks_like_shell_denied(_combined_text(out, err))
                    last_error = denied or (out or err or "shell недоступен").strip()
                    yield f"✗ {last_error}"
                    if idx + 1 < len(_ssh_candidates(username)):
                        continue
                    raise ProbeStubScriptError(last_error)

                priv: Privilege | None = None
                try:
                    for item in _detect_privilege(client, password=password):
                        _check_cancel(cancel)
                        if isinstance(item, Privilege):
                            priv = item
                        else:
                            yield item
                except AgentInstallError as exc:
                    raise ProbeStubScriptError(str(exc)) from exc
                if priv is None:
                    raise ProbeStubScriptError("Не удалось определить права на ноде")

                yield "→ Установка vpn-probe-stub…"
                listen = ""
                bridge = _docker_bridge_ip(client, priv)
                listen = f"{bridge}:{STUB_PORT}"
                stub = stub_src_path().read_text(encoding="utf-8")
                unit = _unit(listen)
                heredoc_py = f"cat > {REMOTE_PY} <<'EOF'\n{stub}\nEOF"
                heredoc_unit = f"cat > /etc/systemd/system/{SERVICE} <<'EOF'\n{unit}EOF"
                cmds = [
                    f"mkdir -p {REMOTE_DIR}",
                    heredoc_py,
                    f"chmod +x {REMOTE_PY}",
                    heredoc_unit,
                    "systemctl daemon-reload",
                    f"systemctl enable --now {SERVICE}",
                    f"systemctl restart {SERVICE}",
                    "sleep 1",
                    f"curl -sS -m 2 -o /dev/null -w 'host GET %{{http_code}} %{{time_total}}\\n' "
                    f"http://{listen}/generate_204",
                    f"curl -sS -m 2 -I -o /dev/null -w 'host HEAD %{{http_code}} %{{time_total}}\\n' "
                    f"http://{listen}/generate_204",
                    f"systemctl is-active {SERVICE}",
                ]
                run_cmd = "\n".join(priv.wrap(c) for c in cmds)
                for line in _stream_priv_command(
                    client, priv, run_cmd, timeout=300.0, cancel=cancel
                ):
                    yield line
                yield f"✓ stub listen={listen}"

                if patch_profile:
                    try:
                        for line in patch_profile_for_node(node_name, listen):
                            yield line
                    except RemnawaveApiError as exc:
                        yield f"⚠ Профиль не пропатчен: {exc.message}"
                        yield "  (задайте REMNAWAVE_PANEL_URL и REMNAWAVE_API_TOKEN в .env)"
                else:
                    yield "→ Патч профиля пропущен"

                yield "✓ Заглушка cf_204 установлена"
                return
        except (ProbeStubScriptError, AgentInstallCancelled):
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc) or type(exc).__name__
            yield f"✗ {last_error}"
            if idx + 1 < len(_ssh_candidates(username)):
                continue
            raise ProbeStubScriptError(last_error) from exc

    raise ProbeStubScriptError(last_error or "Не удалось установить заглушку")
