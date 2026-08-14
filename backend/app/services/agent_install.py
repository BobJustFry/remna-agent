from __future__ import annotations

import shlex
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.services.agent_reachability import check_agent_reachable
from app.services.ssh_client import SshConnectError, probe_tcp, run_command, ssh_connect


class AgentInstallCancelled(Exception):
    """Raised when the client aborts the install stream."""

    message = "Установка отменена"

AGENT_PORT_DEFAULT = 7422
AGENT_DIR = Path(__file__).resolve().parents[3] / "agent"
# In Docker image layout: /app/agent next to /app/app
DOCKER_AGENT_DIR = Path("/app/agent")

# Cloud images often block root SSH; try these when configured user has no shell.
SSH_USER_FALLBACKS = (
    "ubuntu",
    "debian",
    "admin",
    "yc-user",
    "centos",
    "fedora",
    "ec2-user",
    "cloud-user",
)

PrivKind = Literal["root", "sudo", "sudo_pass", "su"]


class AgentInstallError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise AgentInstallCancelled()


@dataclass
class Privilege:
    kind: PrivKind
    password: str | None = None

    def wrap(self, command: str) -> str:
        if self.kind == "root":
            return command
        quoted = shlex.quote(command)
        if self.kind == "sudo":
            return f"sudo -n -- bash -lc {quoted}"
        if self.kind == "sudo_pass":
            return f"sudo -S -p '' -- bash -lc {quoted}"
        return f"su -c {quoted}"

    def stdin(self) -> str | None:
        if self.kind in ("sudo_pass", "su") and self.password:
            return self.password + "\n"
        return None

    def label(self) -> str:
        return {
            "root": "root (uid 0)",
            "sudo": "sudo (без пароля)",
            "sudo_pass": "sudo (с паролем)",
            "su": "su",
        }[self.kind]


def agent_files_dir() -> Path:
    if DOCKER_AGENT_DIR.is_dir():
        return DOCKER_AGENT_DIR
    if AGENT_DIR.is_dir():
        return AGENT_DIR
    raise AgentInstallError("Файлы агента не найдены в образе/репозитории")


def _run(
    client,
    command: str,
    *,
    timeout: float = 60.0,
    check: bool = False,
    input_data: str | None = None,
) -> tuple[int, str, str]:
    code, out, err = run_command(client, command, timeout=timeout, input_data=input_data)
    if check and code != 0:
        detail = (err or out or f"exit {code}").strip()
        raise AgentInstallError(f"Команда завершилась с ошибкой ({code}): {detail}")
    return code, out, err


def _run_priv(
    client,
    priv: Privilege,
    command: str,
    *,
    timeout: float = 60.0,
    check: bool = False,
) -> tuple[int, str, str]:
    return _run(
        client,
        priv.wrap(command),
        timeout=timeout,
        check=check,
        input_data=priv.stdin(),
    )


def _yield_output(out: str, err: str) -> Iterator[str]:
    if out.strip():
        for line in out.rstrip().splitlines():
            yield line
    if err.strip():
        for line in err.rstrip().splitlines():
            yield line


def _combined_text(out: str, err: str) -> str:
    return f"{out}\n{err}".strip()


def _looks_like_shell_denied(text: str) -> str | None:
    low = text.lower()
    if "please login as the user" in low:
        return (
            "SSH-сессия есть, но shell для этого пользователя запрещён "
            f"(сервер ответил: {text.splitlines()[0].strip()}). "
            "Укажите в ноде реального SSH-пользователя с доступом (не root, если он отключён)."
        )
    if "this account is currently not available" in low:
        return "Аккаунт недоступен (nologin). Укажите другого SSH-пользователя."
    return None


def _python3_bin(out: str) -> str | None:
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("/") or " " in line:
            continue
        name = line.rsplit("/", 1)[-1]
        if name == "python3" or name.startswith("python3."):
            return line
    return None


def _detect_privilege(client, *, password: str | None) -> Iterator[str | Privilege]:
    yield "$ id -u && id -un"
    code, out, err = _run(client, "id -u; id -un")
    yield from _yield_output(out, err)
    denied = _looks_like_shell_denied(_combined_text(out, err))
    if denied:
        raise AgentInstallError(denied)
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    uid = lines[0] if lines else ""
    uname = lines[1] if len(lines) > 1 else "?"
    if uid == "0":
        yield f"✓ Права: root (пользователь {uname})"
        yield Privilege("root")
        return

    yield f"→ Пользователь {uname} (uid {uid or '?'}) — не root, ищем sudo/su…"

    yield "$ sudo -n true"
    code, out, err = _run(client, "sudo -n true")
    yield from _yield_output(out, err)
    if code == 0:
        yield "✓ Права: sudo без пароля"
        yield Privilege("sudo")
        return

    if password:
        yield "$ sudo -S -p '' true  # пароль SSH-пользователя"
        code, out, err = _run(
            client,
            "sudo -S -p '' true",
            input_data=password + "\n",
        )
        # hide password prompts / noise
        cleaned_err = "\n".join(
            ln for ln in (err or "").splitlines() if "password" not in ln.lower()
        )
        yield from _yield_output(out, cleaned_err)
        if code == 0:
            yield "✓ Права: sudo с паролем пользователя"
            yield Privilege("sudo_pass", password=password)
            return

        yield "$ su -c true  # пароль как у root (если совпадает)"
        code, out, err = _run(client, "su -c true", input_data=password + "\n")
        cleaned_err = "\n".join(
            ln for ln in (err or "").splitlines() if "password" not in ln.lower()
        )
        yield from _yield_output(out, cleaned_err)
        if code == 0:
            yield "✓ Права: su"
            yield Privilege("su", password=password)
            return

    raise AgentInstallError(
        "Нет прав root и не удалось повысить привилегии (sudo/su). "
        "Нужен passwordless sudo, либо вход по паролю с правом sudo, "
        "либо SSH под root."
    )


def _ensure_python3(client, priv: Privilege, *, install_deps: bool) -> Iterator[str]:
    yield "$ command -v python3"
    code, out, err = _run(client, "command -v python3")
    yield from _yield_output(out, err)

    denied = _looks_like_shell_denied(_combined_text(out, err))
    if denied and _python3_bin(out) is None:
        raise AgentInstallError(denied)

    py = _python3_bin(out)
    if code == 0 and py:
        yield f"✓ python3: {py}"
        return

    yield "→ python3 не найден"
    if not install_deps:
        raise AgentInstallError(
            "На ноде нет python3. Включите «Установить зависимости» и повторите, "
            "либо установите python3 вручную."
        )

    yield "→ Устанавливаю зависимости (python3)…"
    detect = (
        "if command -v apt-get >/dev/null 2>&1; then echo apt; "
        "elif command -v dnf >/dev/null 2>&1; then echo dnf; "
        "elif command -v yum >/dev/null 2>&1; then echo yum; "
        "elif command -v apk >/dev/null 2>&1; then echo apk; "
        "else echo none; fi"
    )
    yield "$ <detect package manager>"
    _c, mgr_out, mgr_err = _run(client, detect)
    yield from _yield_output(mgr_out, mgr_err)
    mgr = (mgr_out or "").strip().splitlines()[-1] if mgr_out.strip() else "none"

    if mgr == "apt":
        cmd = (
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update -y && apt-get install -y python3"
        )
    elif mgr == "dnf":
        cmd = "dnf install -y python3"
    elif mgr == "yum":
        cmd = "yum install -y python3"
    elif mgr == "apk":
        cmd = "apk add --no-cache python3"
    else:
        raise AgentInstallError(
            "Неизвестный пакетный менеджер (нужен apt/dnf/yum/apk). Установите python3 вручную."
        )

    shown = priv.wrap(cmd) if priv.kind != "root" else cmd
    yield f"$ {shown}"
    code, out, err = _run_priv(client, priv, cmd, timeout=300)
    yield from _yield_output(out, err)
    denied = _looks_like_shell_denied(_combined_text(out, err))
    if denied:
        raise AgentInstallError(denied)
    if code != 0:
        detail = (err or out or f"exit {code}").strip()
        raise AgentInstallError(f"Не удалось установить python3: {detail}")

    yield "$ command -v python3"
    code, out, err = _run(client, "command -v python3")
    yield from _yield_output(out, err)
    py = _python3_bin(out)
    if code != 0 or not py:
        raise AgentInstallError("python3 установили, но бинарник не найден в PATH")
    yield f"✓ python3: {py}"


def _ssh_candidates(username: str) -> list[str]:
    out: list[str] = []
    for u in (username, *SSH_USER_FALLBACKS):
        if u and u not in out:
            out.append(u)
    return out


def _shell_usable(out: str, err: str, code: int) -> bool:
    text = _combined_text(out, err)
    if _looks_like_shell_denied(text):
        return False
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    return code == 0 and bool(lines) and lines[0].isdigit()


def install_agent_via_ssh(
    *,
    host: str,
    ssh_port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    token: str,
    agent_port: int = AGENT_PORT_DEFAULT,
    install_deps: bool = False,
    resolved_username: list[str] | None = None,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    """Yield human-readable install log lines. Raises AgentInstallError on failure.

    If resolved_username is provided, appends the SSH user that actually worked
    (may differ from configured username after cloud root→ubuntu fallback).
    """
    files_dir = agent_files_dir()
    agent_py = (files_dir / "remna_node_agent.py").read_text(encoding="utf-8")
    unit = (files_dir / "remna-agent.service").read_text(encoding="utf-8")

    env_file = (
        f"REMNA_AGENT_TOKEN={token}\n"
        f"REMNA_AGENT_PORT={agent_port}\n"
        f"REMNA_AGENT_HOST=0.0.0.0\n"
    )

    _check_cancel(cancel)
    yield f"→ Проверка TCP {host}:{ssh_port}…"
    try:
        probe_tcp(host, ssh_port, timeout=10.0)
    except SshConnectError as exc:
        yield f"✗ {exc.message}"
        raise AgentInstallError(exc.message) from exc
    yield f"✓ Порт {ssh_port} открыт"

    candidates = _ssh_candidates(username)
    last_error: str | None = None

    for idx, user in enumerate(candidates):
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
            if idx + 1 < len(candidates):
                yield "→ Пробую другого SSH-пользователя…"
                continue
            raise AgentInstallError(exc.message) from exc

        try:
            with client_cm as client:
                yield "✓ SSH-сессия открыта"
                _check_cancel(cancel)

                yield "$ id -u; id -un"
                code, out, err = _run(client, "id -u; id -un")
                yield from _yield_output(out, err)
                if not _shell_usable(out, err, code):
                    denied = _looks_like_shell_denied(_combined_text(out, err))
                    last_error = denied or (out or err or "shell недоступен").strip()
                    yield f"✗ {last_error}"
                    if idx + 1 < len(candidates):
                        yield (
                            "→ У этого пользователя нет shell (часто root на cloud-образах). "
                            "Пробую другого…"
                        )
                        continue
                    raise AgentInstallError(
                        last_error
                        or "Не удалось найти SSH-пользователя с рабочим shell"
                    )

                if user != username:
                    yield f"✓ Рабочий SSH-пользователь: {user} (вместо {username})"
                if resolved_username is not None:
                    resolved_username.clear()
                    resolved_username.append(user)

                _check_cancel(cancel)
                # Password for sudo: prefer SSH password; for key-only ubuntu often has NOPASSWD.
                priv: Privilege | None = None
                for item in _detect_privilege(client, password=password):
                    _check_cancel(cancel)
                    if isinstance(item, Privilege):
                        priv = item
                    else:
                        yield item
                if priv is None:
                    raise AgentInstallError("Не удалось определить права на ноде")

                if install_deps:
                    yield "→ Режим: зависимости можно установить автоматически"
                _check_cancel(cancel)
                yield from _ensure_python3(client, priv, install_deps=install_deps)

                _check_cancel(cancel)
                yield "$ command -v systemctl"
                code, out, err = _run(client, "command -v systemctl")
                yield from _yield_output(out, err)
                if code != 0 or not (out or "").strip().startswith("/"):
                    raise AgentInstallError(
                        "На ноде нет systemctl/systemd — агент сейчас ставится только как systemd unit."
                    )

                _check_cancel(cancel)
                staging = f"/tmp/remna-agent-install-{uuid.uuid4().hex[:10]}"
                yield f"$ mkdir -p {staging}"
                _run(client, f"mkdir -p {shlex.quote(staging)}", check=True)

                _check_cancel(cancel)
                yield "→ Загрузка файлов во временную папку…"
                sftp = client.open_sftp()
                try:
                    with sftp.file(f"{staging}/remna_node_agent.py", "w") as f:
                        f.write(agent_py)
                    yield f"✓ {staging}/remna_node_agent.py"
                    with sftp.file(f"{staging}/remna-agent.env", "w") as f:
                        f.write(env_file)
                    yield f"✓ {staging}/remna-agent.env"
                    with sftp.file(f"{staging}/remna-agent.service", "w") as f:
                        f.write(unit)
                    yield f"✓ {staging}/remna-agent.service"
                finally:
                    sftp.close()

                install_cmd = (
                    f"mkdir -p /opt/remna-agent && "
                    f"cp {shlex.quote(staging + '/remna_node_agent.py')} /opt/remna-agent/remna_node_agent.py && "
                    f"cp {shlex.quote(staging + '/remna-agent.env')} /etc/remna-agent.env && "
                    f"cp {shlex.quote(staging + '/remna-agent.service')} /etc/systemd/system/remna-agent.service && "
                    f"chmod 755 /opt/remna-agent/remna_node_agent.py && "
                    f"chmod 600 /etc/remna-agent.env && "
                    f"rm -rf {shlex.quote(staging)}"
                )
                _check_cancel(cancel)
                shown = priv.wrap(install_cmd) if priv.kind != "root" else install_cmd
                yield f"$ {shown}"
                code, out, err = _run_priv(client, priv, install_cmd, timeout=60)
                yield from _yield_output(out, err)
                if code != 0:
                    detail = (err or out or f"exit {code}").strip()
                    raise AgentInstallError(f"Не удалось скопировать файлы агента: {detail}")
                yield "✓ Файлы установлены в /opt/remna-agent и systemd"

                _check_cancel(cancel)
                # enable --now does NOT restart an already-running unit — must restart
                # so the process reloads REMNA_AGENT_TOKEN from the env file.
                start_cmd = (
                    "systemctl daemon-reload && "
                    "systemctl enable remna-agent && "
                    "systemctl restart remna-agent && "
                    "systemctl is-active remna-agent"
                )
                shown = priv.wrap(start_cmd) if priv.kind != "root" else start_cmd
                yield f"$ {shown}"
                code, out, err = _run_priv(client, priv, start_cmd, timeout=90)
                yield from _yield_output(out, err)
                active = (out or "").strip().splitlines()[-1] if out.strip() else ""
                if code != 0 or active != "active":
                    detail = (err or out or "не удалось запустить remna-agent").strip()
                    jcmd = "journalctl -u remna-agent -n 30 --no-pager"
                    yield f"$ {priv.wrap(jcmd) if priv.kind != 'root' else jcmd}"
                    _jcode, jout, jerr = _run_priv(client, priv, jcmd, timeout=30)
                    yield from _yield_output(jout, jerr)
                    raise AgentInstallError(f"Установка агента: {detail}")

                yield f"✓ remna-agent active (порт {agent_port}, права: {priv.label()}, user: {user})"

                yield f"$ curl -sS -m 3 http://127.0.0.1:{agent_port}/health"
                hcode, hout, herr = _run(
                    client, f"curl -sS -m 3 http://127.0.0.1:{agent_port}/health || true"
                )
                yield from _yield_output(hout, herr)
                if "ok" in (hout or "").lower():
                    yield "✓ Агент отвечает локально на ноде"
                else:
                    yield "✗ Локальный /health не ответил — смотрите journalctl -u remna-agent"

                # Best-effort: open local firewall if ufw is active (won't replace cloud SG).
                yield "$ (ufw status || true)"
                _uc, uout, _ue = _run_priv(
                    client,
                    priv,
                    "bash -lc 'if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -qi active; "
                    f"then ufw allow {agent_port}/tcp comment remna-agent || true; ufw status | head -20; "
                    "else echo ufw_inactive_or_missing; fi'",
                    timeout=30,
                )
                yield from _yield_output(uout, "")

                yield f"→ Проверка доступности с панели: {host}:{agent_port}…"
                reach = check_agent_reachable(host, agent_port, timeout=5.0)
                if reach.ok:
                    yield f"✓ {reach.message}"
                else:
                    yield f"✗ {reach.message}"
                    yield (
                        "→ Агент на VPS запущен, но панель не достучалась снаружи. "
                        "Откройте входящий TCP "
                        f"{agent_port} в security group / firewall хостинга, затем подождите опрос."
                    )

                yield f"Готово: агент установлен и запущен на порту {agent_port}"
                return
        except AgentInstallError:
            raise
        except SshConnectError as exc:
            last_error = exc.message
            yield f"✗ {exc.message}"
            if idx + 1 < len(candidates):
                yield "→ Пробую другого SSH-пользователя…"
                continue
            raise AgentInstallError(exc.message) from exc

    raise AgentInstallError(
        last_error or "Не удалось подключиться ни под одним из типичных SSH-пользователей"
    )
