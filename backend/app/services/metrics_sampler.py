"""Background loop: agent CPU/disk + cf_204 RTT into node_metric_samples."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Node
from app.services.agent_metrics import fetch_agent_status
from app.services.crypto import decrypt_secret
from app.services.metrics_store import insert_samples, prune_old

log = logging.getLogger("remna.metrics")

SAMPLE_INTERVAL_SEC = 30.0
_PRUNE_EVERY = 40


async def sample_once() -> int:
    async with SessionLocal() as db:
        result = await db.execute(select(Node))
        nodes = list(result.scalars().all())
        if not nodes:
            return 0

        async def one_agent(node: Node) -> tuple[str, object]:
            token = decrypt_secret(node.agent_token_enc) if node.agent_token_enc else None
            st = await fetch_agent_status(
                host=node.host,
                token=token,
                agent_port=node.agent_port,
                node_id=str(node.id),
            )
            return str(node.id), st

        # Онлайн ядра Xray приходит от панели Remnawave — один запрос на весь тик,
        # а не по ноде. Панель может быть недоступна: тогда пишем None, а не ноль,
        # иначе на графике появится провал, которого не было.
        from app.services.remnawave_api import rw_users_online_by_address

        online_task = asyncio.to_thread(rw_users_online_by_address)
        agent_pairs = await asyncio.gather(*(one_agent(n) for n in nodes), return_exceptions=True)
        try:
            by_address = await online_task
        except Exception:
            log.warning("xray online unavailable this tick")
            by_address = {}
        agents: dict[str, object] = {}
        for item in agent_pairs:
            if isinstance(item, BaseException):
                log.warning("agent sample failed: %s", item)
                continue
            nid, st = item
            agents[nid] = st

        rows = []
        for node in nodes:
            nid = str(node.id)
            ag = agents.get(nid)
            cpu = getattr(ag, "cpu_percent", None) if ag is not None else None
            mem = getattr(ag, "mem_percent", None) if ag is not None else None
            disk = getattr(ag, "disk_percent", None) if ag is not None else None
            present = bool(getattr(ag, "present", False)) if ag is not None else False
            cf_ok = getattr(ag, "cf204_ok", None) if ag is not None else None
            cf_ms = getattr(ag, "cf204_ms", None) if ag is not None else None
            rx = getattr(ag, "net_rx_bps", None) if ag is not None else None
            tx = getattr(ag, "net_tx_bps", None) if ag is not None else None
            cap = getattr(ag, "capacity_comfort", None) if ag is not None else None
            rows.append(
                {
                    "node_id": uuid.UUID(nid),
                    "online": bool(cf_ok),
                    "ping_ms": cf_ms if cf_ok else None,
                    "cpu_percent": cpu if present else None,
                    "mem_percent": mem if present else None,
                    "disk_percent": disk if present else None,
                    "net_rx_bps": rx if present else None,
                    "net_tx_bps": tx if present else None,
                    "users_online": by_address.get(node.host),
                    "capacity": cap if present else None,
                }
            )
        return await insert_samples(db, rows)


async def run_metrics_sampler() -> None:
    await asyncio.sleep(3)
    ticks = 0
    while True:
        try:
            n = await sample_once()
            ticks += 1
            if n:
                log.info("metrics sample: %s nodes", n)
            if ticks % _PRUNE_EVERY == 0:
                async with SessionLocal() as db:
                    deleted = await prune_old(db)
                    if deleted:
                        log.info("metrics prune: dropped %s old rows", deleted)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("metrics sample failed")
        await asyncio.sleep(SAMPLE_INTERVAL_SEC)
