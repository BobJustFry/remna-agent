"""Reboot a node over SSH with the same privilege elevation as agent install."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.agent_install import (
    AgentInstallError,
    Privilege,
    _detect_privilege,
    _looks_like_shell_denied,
    _run,
    _run_priv,
    _shell_usable,
    _ssh_candidates,
    _combined_text,
)
from app.services.ssh_client import SshConnectError, probe_tcp, ssh_connect
from app.services.ssh_passwd import looks_like_password_expired


@dataclass
class RebootResult:
    ok: bool
    message: str


class NodeRebootError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def reboot_node_via_ssh(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
) -> RebootResult:
    try:
        probe_tcp(host, ssh_port, timeout=10.0)
    except SshConnectError as exc:
        raise NodeRebootError(exc.message) from exc

    candidates = _ssh_candidates(username)
    last_error: str | None = None

    for idx, user in enumerate(candidates):
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
            if idx + 1 < len(candidates):
                continue
            raise NodeRebootError(exc.message) from exc

        try:
            with client_cm as client:
                code, out, err = _run(client, "id -u; id -un")
                if looks_like_password_expired(_combined_text(out, err)):
                    raise NodeRebootError(
                        "Пароль истёк (PAM требует смену через TTY). "
                        "Сначала установите агент — панель сменит пароль сама."
                    )
                if not _shell_usable(out, err, code):
                    denied = _looks_like_shell_denied(_combined_text(out, err))
                    last_error = denied or (out or err or "shell недоступен").strip()
                    if idx + 1 < len(candidates):
                        continue
                    raise NodeRebootError(last_error)

                priv: Privilege | None = None
                try:
                    for item in _detect_privilege(client, password=password):
                        if isinstance(item, Privilege):
                            priv = item
                except AgentInstallError as exc:
                    raise NodeRebootError(str(exc)) from exc
                if priv is None:
                    raise NodeRebootError("Не удалось определить права на ноде")

                # Detach reboot so SSH can return before the host goes down.
                reboot_cmd = (
                    "nohup sh -c 'sleep 1; "
                    "systemctl reboot || reboot || shutdown -r now' "
                    ">/dev/null 2>&1 &"
                )
                try:
                    _run_priv(client, priv, reboot_cmd, timeout=20, check=False)
                except Exception:  # noqa: BLE001 — connection may drop mid-reboot
                    pass

                who = f"{user}@{host}" if user != username else host
                return RebootResult(
                    ok=True,
                    message=f"Команда перезагрузки отправлена ({who}, {priv.label()})",
                )
        except NodeRebootError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc) or type(exc).__name__
            if idx + 1 < len(candidates):
                continue
            raise NodeRebootError(last_error) from exc

    raise NodeRebootError(last_error or "Не удалось перезагрузить ноду")
