from dataclasses import dataclass

from app.models import AuthType
from app.services.ssh_client import ssh_connect


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
            client.exec_command("true", timeout=timeout)
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
