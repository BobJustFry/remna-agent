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
    warp_present: bool | None = None
    warp_up: bool | None = None
    warp_healthy: bool | None = None
    warp_handshake_sec: int | None = None
    warp_egress_ok: bool | None = None
    warp_interface: str | None = None
    warp_method: str | None = None
    warp_version: str | None = None
    warp_ipv4: str | None = None
    haproxy_present: bool | None = None
    haproxy_up: bool | None = None
    haproxy_version: str | None = None
    haproxy_listen: str | None = None
    proxy_peers: int | None = None
    proxy_conns: int | None = None
    cpu_percent: float | None = None
    mem_percent: float | None = None
    disk_percent: float | None = None
    loadavg: list[float] | None = None
    cf204_ok: bool | None = None
    cf204_ms: float | None = None
    # How many concurrent tunnels the box carries on its current config.
    # Agents older than 0.1.16 do not report it — stays None, UI shows "—".
    capacity_comfort: int | None = None
    capacity_ceiling: int | None = None
    capacity_limiter: str | None = None
    # Throughput on the default-route interface, bits/s. Null on agents < 0.1.17
    # and on the very first poll after an agent restart (no delta yet).
    net_rx_bps: int | None = None
    net_tx_bps: int | None = None
    net_iface: str | None = None
    net_link_mbps: int | None = None
    error: str | None = None


def _capacity(data: dict) -> dict:
    """`capacity` block from /metrics; empty for agents older than 0.1.16."""
    block = data.get("capacity")
    return block if isinstance(block, dict) else {}


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


def peek_cached_status(node_id: str) -> AgentStatus | None:
    entry = _cache.get(f"node:{node_id}")
    if entry is None:
        return None
    return entry.last_ok


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

        def _as_bool(v: object) -> bool | None:
            return v if isinstance(v, bool) else None

        def _as_str(v: object) -> str | None:
            return v if isinstance(v, str) and v else None

        def _as_int(v: object) -> int | None:
            if isinstance(v, bool) or v is None:
                return None
            if isinstance(v, int):
                return v
            if isinstance(v, float):
                return int(v)
            return None

        def _as_float(v: object) -> float | None:
            if isinstance(v, bool) or v is None:
                return None
            if isinstance(v, (int, float)):
                return round(float(v), 1)
            return None

        return AgentStatus(
            present=True,
            configured=True,
            version=data.get("version"),
            remnanode_version=data.get("remnanode_version"),
            remnanode_running=remnanode_running,
            warp_present=_as_bool(data.get("warp_present")),
            warp_up=_as_bool(data.get("warp_up")),
            warp_healthy=_as_bool(data.get("warp_healthy")),
            warp_handshake_sec=_as_int(data.get("warp_handshake_sec")),
            warp_egress_ok=_as_bool(data.get("warp_egress_ok")),
            warp_interface=_as_str(data.get("warp_interface")),
            warp_method=_as_str(data.get("warp_method")),
            warp_version=_as_str(data.get("warp_version")),
            warp_ipv4=_as_str(data.get("warp_ipv4")),
            haproxy_present=_as_bool(data.get("haproxy_present")),
            haproxy_up=_as_bool(data.get("haproxy_up")),
            haproxy_version=_as_str(data.get("haproxy_version")),
            haproxy_listen=_as_str(data.get("haproxy_listen")),
            proxy_peers=_as_int(data.get("proxy_peers")),
            proxy_conns=_as_int(data.get("proxy_conns")),
            cpu_percent=data.get("cpu_percent"),
            mem_percent=data.get("mem_percent"),
            disk_percent=data.get("disk_percent"),
            loadavg=data.get("loadavg"),
            cf204_ok=_as_bool(data.get("cf204_ok")),
            cf204_ms=_as_float(data.get("cf204_ms")),
            capacity_comfort=_as_int(_capacity(data).get("comfort")),
            capacity_ceiling=_as_int(_capacity(data).get("ceiling")),
            capacity_limiter=_as_str(_capacity(data).get("limiter")),
            net_rx_bps=_as_int(data.get("net_rx_bps")),
            net_tx_bps=_as_int(data.get("net_tx_bps")),
            net_iface=_as_str(data.get("net_iface")),
            net_link_mbps=_as_int(data.get("net_link_mbps")),
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
