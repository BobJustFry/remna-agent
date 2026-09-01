from dataclasses import dataclass

from app.services.ssh_client import run_command, ssh_connect
from app.services.ssh_passwd import looks_like_password_expired


@dataclass
class SshCheckResult:
    ok: bool
    message: str


def check_ssh_auth(
    *,
    host: str,
    port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    timeout: float = 8.0,
) -> SshCheckResult:
    try:
        with ssh_connect(
            host=host,
            port=port,
            username=username,
            auth_type=auth_type,
            password=password,
            private_key=private_key,
            timeout=timeout,
        ) as client:
            _code, out, err = run_command(client, "id -u; id -un", timeout=timeout)
            text = f"{out}\n{err}"
            if looks_like_password_expired(text) or "no tty available" in text.lower():
                return SshCheckResult(
                    ok=True,
                    message=(
                        "SSH-OK, но пароль истёк — PAM требует смену через TTY. "
                        "Установка агента сменит его сама."
                    ),
                )
        return SshCheckResult(ok=True, message="SSH-OK")
    except ValueError as exc:
        return SshCheckResult(ok=False, message=str(exc))
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "Authentication" in name:
            return SshCheckResult(ok=False, message="Ошибка авторизации: неверный логин, пароль или ключ")
        if "Timeout" in name or "timed out" in str(exc).lower():
            return SshCheckResult(ok=False, message="Таймаут подключения")
        if isinstance(exc, OSError):
            return SshCheckResult(ok=False, message=f"Сеть: {exc}")
        return SshCheckResult(ok=False, message=str(exc) or "Неизвестная ошибка SSH")
