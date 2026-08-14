import io
import socket
from contextlib import contextmanager
from typing import Iterator

import paramiko

from app.models import AuthType


class SshConnectError(Exception):
    """Human-readable SSH connection failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def format_ssh_connect_error(exc: BaseException, *, host: str, port: int) -> str:
    name = type(exc).__name__
    text = str(exc).strip() or name
    low = text.lower()
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in low or name == "timeout":
        return (
            f"Таймаут TCP {host}:{port}. Порт SSH недоступен с сервера панели "
            f"(firewall / security group / sshd не слушает)."
        )
    if isinstance(exc, ConnectionRefusedError) or "connection refused" in low:
        return f"Соединение отклонено {host}:{port}. Проверьте, что sshd запущен и порт верный."
    if isinstance(exc, socket.gaierror) or "name or service not known" in low:
        return f"Не удалось разрешить хост «{host}»."
    if "authentication" in low or "auth" in low:
        return f"Ошибка аутентификации SSH: {text}"
    return f"SSH ({host}:{port}): {text}"


def probe_tcp(host: str, port: int, timeout: float = 8.0) -> None:
    """Raise SshConnectError if TCP port is not reachable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return
    except OSError as exc:
        raise SshConnectError(format_ssh_connect_error(exc, host=host, port=port)) from exc


def load_pkey(private_key: str) -> paramiko.PKey:
    data = private_key.strip() + "\n"
    last_error: Exception | None = None
    for loader in (
        paramiko.Ed25519Key,
        paramiko.RSAKey,
        paramiko.ECDSAKey,
        paramiko.DSSKey,
    ):
        try:
            return loader.from_private_key(io.StringIO(data))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    raise ValueError(str(last_error) if last_error else "unsupported key")


@contextmanager
def ssh_connect(
    *,
    host: str,
    port: int,
    username: str,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    timeout: float = 20.0,
) -> Iterator[paramiko.SSHClient]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict = {
        "hostname": host,
        "port": port,
        "username": username,
        "timeout": timeout,
        "allow_agent": False,
        "look_for_keys": False,
        "banner_timeout": timeout,
        "auth_timeout": timeout,
    }
    if auth_type == AuthType.password.value:
        if not password:
            raise ValueError("Пароль не задан")
        kwargs["password"] = password
    else:
        if not private_key:
            raise ValueError("Приватный ключ не задан")
        kwargs["pkey"] = load_pkey(private_key)
    try:
        client.connect(**kwargs)
    except Exception as exc:  # noqa: BLE001
        client.close()
        raise SshConnectError(format_ssh_connect_error(exc, host=host, port=port)) from exc
    try:
        yield client
    finally:
        client.close()


def run_command(
    client: paramiko.SSHClient,
    command: str,
    timeout: float = 60.0,
    input_data: str | None = None,
) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if input_data is not None:
        stdin.write(input_data)
        stdin.flush()
        stdin.channel.shutdown_write()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err
