"""Probe agent HTTP endpoint from the panel (detect closed security groups / firewall)."""

from __future__ import annotations

import socket
from dataclasses import dataclass

import httpx


@dataclass
class Reachability:
    ok: bool
    tcp_ok: bool
    http_ok: bool
    message: str


def probe_tcp(host: str, port: int, timeout: float = 4.0) -> tuple[bool, str | None]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, None
    except TimeoutError:
        return False, "timeout"
    except ConnectionRefusedError:
        return False, "refused"
    except OSError as exc:
        return False, str(exc) or type(exc).__name__


def explain_unreachable(host: str, port: int, *, tcp_ok: bool, detail: str | None = None) -> str:
    if not tcp_ok:
        kind = detail or "timeout"
        if kind == "refused":
            return (
                f"Порт {port} на {host} отклоняет соединение (connection refused). "
                f"Агент не слушает или мешает firewall на ноде."
            )
        return (
            f"Порт {port} на {host} недоступен с панели (TCP {kind}). "
            f"Откройте входящий TCP {port} в security group / firewall хостинга "
            f"(для Yandex Cloud — группа безопасности ВМ)."
        )
    return (
        f"TCP {host}:{port} открывается, но HTTP-ответ агента не получен"
        + (f" ({detail})" if detail else "")
        + f". Проверьте security group / фильтрацию и что на порту именно remna-agent."
    )


def check_agent_reachable(host: str, port: int, timeout: float = 4.0) -> Reachability:
    """Sync check: TCP then GET /health. Used after install from worker thread."""
    tcp_ok, tcp_err = probe_tcp(host, port, timeout=timeout)
    if not tcp_ok:
        return Reachability(
            ok=False,
            tcp_ok=False,
            http_ok=False,
            message=explain_unreachable(host, port, tcp_ok=False, detail=tcp_err),
        )

    url = f"http://{host}:{port}/health"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            return Reachability(
                ok=False,
                tcp_ok=True,
                http_ok=False,
                message=explain_unreachable(host, port, tcp_ok=True, detail=f"HTTP {resp.status_code}"),
            )
        return Reachability(
            ok=True,
            tcp_ok=True,
            http_ok=True,
            message=f"Агент отвечает на http://{host}:{port}/health",
        )
    except Exception as exc:  # noqa: BLE001
        detail = str(exc).strip() or type(exc).__name__
        return Reachability(
            ok=False,
            tcp_ok=True,
            http_ok=False,
            message=explain_unreachable(host, port, tcp_ok=True, detail=detail),
        )


async def check_agent_reachable_async(host: str, port: int, timeout: float = 4.0) -> Reachability:
    import asyncio

    tcp_ok, tcp_err = await asyncio.to_thread(probe_tcp, host, port, timeout)
    if not tcp_ok:
        return Reachability(
            ok=False,
            tcp_ok=False,
            http_ok=False,
            message=explain_unreachable(host, port, tcp_ok=False, detail=tcp_err),
        )

    url = f"http://{host}:{port}/health"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            return Reachability(
                ok=False,
                tcp_ok=True,
                http_ok=False,
                message=explain_unreachable(host, port, tcp_ok=True, detail=f"HTTP {resp.status_code}"),
            )
        return Reachability(
            ok=True,
            tcp_ok=True,
            http_ok=True,
            message=f"Агент отвечает на http://{host}:{port}/health",
        )
    except Exception as exc:  # noqa: BLE001
        detail = str(exc).strip() or type(exc).__name__
        return Reachability(
            ok=False,
            tcp_ok=True,
            http_ok=False,
            message=explain_unreachable(host, port, tcp_ok=True, detail=detail),
        )


def format_httpx_error(exc: BaseException, *, host: str, port: int) -> str:
    name = type(exc).__name__
    text = str(exc).strip()
    low = f"{name} {text}".lower()
    if "connecttimeout" in low or "timed out" in low or name in {"TimeoutError", "ConnectTimeout"}:
        return explain_unreachable(host, port, tcp_ok=False, detail="timeout")
    if "connecterror" in low or "connection refused" in low:
        return explain_unreachable(host, port, tcp_ok=False, detail="refused")
    if "readtimeout" in low or "read error" in low:
        return explain_unreachable(host, port, tcp_ok=True, detail=name)
    return text or name or "Агент недоступен"
