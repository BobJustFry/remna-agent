"""Pull remna-agent token from the VPS over SSH when panel token is out of sync."""

from __future__ import annotations

import re
import time

import httpx

from app.services.agent_install import (
    AGENT_PORT_DEFAULT,
    SSH_USER_FALLBACKS,
    _looks_like_shell_denied,
    _shell_usable,
)
from app.services.ssh_client import SshConnectError, run_command, ssh_connect

_TOKEN_RE = re.compile(r"^REMNA_AGENT_TOKEN=(.+)$", re.MULTILINE)


class TokenSyncError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _parse_token(text: str) -> str | None:
    m = _TOKEN_RE.search(text or "")
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def _ssh_users(username: str) -> list[str]:
    out: list[str] = []
    for u in (username, *SSH_USER_FALLBACKS):
        if u and u not in out:
            out.append(u)
    return out


def _run_elevated(
    client,
    command: str,
    *,
    password: str | None,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    code, out, err = run_command(client, command, timeout=timeout)
    if code == 0:
        return code, out, err
    code, out, err = run_command(client, f"sudo -n -- {command}", timeout=timeout)
    if code == 0:
        return code, out, err
    if password:
        return run_command(
            client,
            f"sudo -S -p '' -- {command}",
            timeout=timeout,
            input_data=password + "\n",
        )
    return code, out, err


def _try_read_token(client, *, password: str | None) -> str | None:
    commands: list[tuple[str, str | None]] = [
        ("grep '^REMNA_AGENT_TOKEN=' /etc/remna-agent.env 2>/dev/null || true", None),
        ("sudo -n grep '^REMNA_AGENT_TOKEN=' /etc/remna-agent.env 2>/dev/null || true", None),
    ]
    if password:
        commands.append(
            (
                "sudo -S -p '' grep '^REMNA_AGENT_TOKEN=' /etc/remna-agent.env 2>/dev/null || true",
                password + "\n",
            )
        )

    for cmd, stdin in commands:
        code, out, _err = run_command(client, cmd, timeout=20, input_data=stdin)
        if code != 0:
            continue
        token = _parse_token(out)
        if token:
            return token
    return None


def _metrics_ok(host: str, port: int, token: str) -> bool:
    try:
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(
                f"http://{host}:{port}/metrics",
                headers={"Authorization": f"Bearer {token}"},
            )
        return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def repair_agent_auth_via_ssh(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    agent_port: int = AGENT_PORT_DEFAULT,
) -> str:
    """Read token from env, restart agent (picks up env), return working token.

    `systemctl enable --now` does not restart an already-running unit, so the
    process can keep an old in-memory token after reinstall rewrote the env file.
    """
    last_error = "не удалось прочитать /etc/remna-agent.env"
    for user in _ssh_users(username):
        try:
            with ssh_connect(
                host=host,
                port=ssh_port,
                username=user,
                auth_type=auth_type,
                password=password,
                private_key=private_key,
                timeout=15.0,
            ) as client:
                code, out, err = run_command(client, "id -u; id -un", timeout=10)
                text = f"{out}\n{err}"
                denied = _looks_like_shell_denied(text)
                if denied and not _shell_usable(out, err, code):
                    last_error = denied
                    continue
                if not _shell_usable(out, err, code):
                    last_error = (out or err or "shell недоступен").strip()
                    continue

                token = _try_read_token(client, password=password)
                if not token:
                    last_error = "файл /etc/remna-agent.env не найден или без REMNA_AGENT_TOKEN"
                    continue

                # Already works — no restart needed.
                if _metrics_ok(host, agent_port, token):
                    return token

                # Stale process: env has correct token, agent still serves old one.
                rcode, rout, rerr = _run_elevated(
                    client,
                    "systemctl restart remna-agent && systemctl is-active remna-agent",
                    password=password,
                    timeout=45,
                )
                active = (rout or "").strip().splitlines()[-1] if (rout or "").strip() else ""
                if rcode != 0 or active != "active":
                    detail = (rerr or rout or f"exit {rcode}").strip()
                    last_error = f"не удалось перезапустить remna-agent: {detail}"
                    continue

                # Give HTTP server a moment to bind.
                time.sleep(1.2)
                if _metrics_ok(host, agent_port, token):
                    return token

                last_error = (
                    "агент перезапущен, но /metrics всё ещё отклоняет токен из env — "
                    "переустановите агент"
                )
        except SshConnectError as exc:
            last_error = exc.message
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc) or type(exc).__name__
            continue

    raise TokenSyncError(last_error)


# Back-compat alias
def fetch_agent_token_via_ssh(**kwargs) -> str:
    return repair_agent_auth_via_ssh(**kwargs)
