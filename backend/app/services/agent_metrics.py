from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace

import httpx

from app.services.agent_install import AGENT_PORT_DEFAULT
from app.services.agent_reachability import (
    check_agent_reachable_async,
    format_httpx_error,
)

# Ignore transient blips: require several consecutive failures before "offline".
_FAIL_THRESHOLD = 3
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.2


@dataclass
class AgentStatus:
    present: bool
    configured: bool
    version: str | None = None
    remnanode_version: str | None = None
    remnanode_running: bool | None = None
    cpu_percent: float | None = None
    mem_percent: float | None = None
    disk_percent: float | None = None
    loadavg: list[float] | None = None
    error: str | None = None


@dataclass
class _ProbeCache:
    last_ok: AgentStatus | None = None
    consecutive_fails: int = 0
    updated_at: float = 0.0


_cache: dict[str, _ProbeCache] = {}


def _cache_key(host: str, port: int, node_id: str | None) -> str:
    if node_id:
        return f"node:{node_id}"
    return f"host:{host}:{port}"


TOKEN_MISMATCH_ERROR = "Токен не совпадает с агентом на ноде — переустановите"


def is_token_mismatch(status: AgentStatus) -> bool:
    err = (status.error or "").lower()
    return status.configured and not status.present and (
        "токен" in err or "авторизац" in err or "401" in err
    )


def clear_agent_status_cache(node_id: str | None = None) -> None:
    if node_id is None:
        _cache.clear()
        return
    _cache.pop(f"node:{node_id}", None)


def _is_hard_error(status: AgentStatus) -> bool:
    """Auth / clear misconfig — show immediately, no hysteresis."""
    if not status.configured:
        return True
    err = (status.error or "").lower()
    return (
        "токен" in err
        or "unauthorized" in err
        or TOKEN_MISMATCH_ERROR.lower() in err
    )


def _is_port_block_error(status: AgentStatus) -> bool:
    err = (status.error or "").lower()
    return "security group" in err or "порт " in err or "недоступен с панели" in err


async def _probe_once(
    *,
    host: str,
    port: int,
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> AgentStatus:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 401:
            # Agent is up but rejects panel token — common after reinstall/desync.
            agent_up = False
            try:
                health = await client.get(f"http://{host}:{port}/health")
                agent_up = health.status_code == 200
            except Exception:  # noqa: BLE001
                agent_up = False
            return AgentStatus(
                present=False,
                configured=True,
                error=(
                    TOKEN_MISMATCH_ERROR
                    if agent_up
                    else "Ошибка авторизации агента (HTTP 401) — переустановите"
                ),
            )
        if resp.status_code >= 400:
            return AgentStatus(
                present=False,
                configured=True,
                error=f"HTTP {resp.status_code}",
            )
        data = resp.json()
        remnanode_running = data.get("remnanode_running")
        if remnanode_running is not None and not isinstance(remnanode_running, bool):
            remnanode_running = None
        return AgentStatus(
            present=True,
            configured=True,
            version=data.get("version"),
            remnanode_version=data.get("remnanode_version"),
            remnanode_running=remnanode_running,
            cpu_percent=data.get("cpu_percent"),
            mem_percent=data.get("mem_percent"),
            disk_percent=data.get("disk_percent"),
            loadavg=data.get("loadavg"),
            error=None,
        )


async def fetch_agent_status(
    *,
    host: str,
    token: str | None,
    agent_port: int | None,
    timeout: float = 4.0,
    node_id: str | None = None,
) -> AgentStatus:
    if not token:
        return AgentStatus(present=False, configured=False)

    port = agent_port or AGENT_PORT_DEFAULT
    url = f"http://{host}:{port}/metrics"
    headers = {"Authorization": f"Bearer {token}"}
    key = _cache_key(host, port, node_id)
    entry = _cache.setdefault(key, _ProbeCache())

    last_error: str | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            status = await _probe_once(
                host=host, port=port, url=url, headers=headers, timeout=timeout
            )
            if status.present:
                entry.last_ok = status
                entry.consecutive_fails = 0
                entry.updated_at = time.monotonic()
                return status
            if _is_hard_error(status):
                entry.consecutive_fails = _FAIL_THRESHOLD
                entry.updated_at = time.monotonic()
                return status
            last_error = status.error
        except Exception as exc:  # noqa: BLE001
            last_error = format_httpx_error(exc, host=host, port=port)

        if attempt + 1 < _RETRY_ATTEMPTS:
            await asyncio.sleep(_RETRY_BASE_DELAY * (attempt + 1))

    # Enrich with dedicated TCP/health diagnosis when metrics failed.
    reach = await check_agent_reachable_async(host, port, timeout=min(timeout, 3.0))
    if not reach.ok:
        last_error = reach.message

    entry.consecutive_fails += 1
    entry.updated_at = time.monotonic()

    failed = AgentStatus(present=False, configured=True, error=last_error or "Агент недоступен")

    # Port/firewall blocks are stable — show soon (don't hide behind soft hysteresis forever).
    if _is_port_block_error(failed) and entry.consecutive_fails >= 2:
        return failed

    if entry.consecutive_fails < _FAIL_THRESHOLD and entry.last_ok is not None:
        return replace(entry.last_ok, error=None)

    return failed
