import asyncio
import time
from dataclasses import dataclass

from icmplib import async_ping

# Online ping leaves the Docker VM via the Windows routing table. VupenVPN
# (Wintun) answers ICMP for captured public IPs locally (~0–2 ms, TTL 64).
# Host /32 bypasses: scripts/windows/sync-direct-routes.ps1


@dataclass
class PingResult:
    online: bool
    latency_ms: float | None
    method: str | None


async def _tcp_check(host: str, port: int, timeout: float = 1.5) -> PingResult:
    started = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        latency = (time.perf_counter() - started) * 1000
        return PingResult(online=True, latency_ms=round(latency, 1), method="tcp")
    except Exception:
        return PingResult(online=False, latency_ms=None, method="tcp")


async def _icmp_check(host: str, timeout: float = 1.5) -> PingResult | None:
    for privileged in (True, False):
        try:
            result = await async_ping(host, count=1, timeout=timeout, privileged=privileged)
            if result.is_alive:
                return PingResult(
                    online=True,
                    latency_ms=round(float(result.avg_rtt), 1) if result.avg_rtt is not None else None,
                    method="icmp",
                )
            return PingResult(online=False, latency_ms=None, method="icmp")
        except Exception:
            continue
    return None


async def check_host(host: str, ssh_port: int) -> PingResult:
    icmp = await _icmp_check(host)
    if icmp is not None and icmp.online:
        return icmp
    tcp = await _tcp_check(host, ssh_port)
    if tcp.online:
        return tcp
    if icmp is not None:
        return icmp
    return tcp


async def check_many(items: list[tuple[str, str, int]]) -> dict[str, PingResult]:
    """items: list of (id, host, ssh_port)"""

    async def one(node_id: str, host: str, port: int) -> tuple[str, PingResult]:
        return node_id, await check_host(host, port)

    results = await asyncio.gather(*(one(i, h, p) for i, h, p in items))
    return dict(results)
