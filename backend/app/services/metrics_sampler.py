"""Background loop: agent CPU/disk + cf_204 RTT into node_metric_samples."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Node
from app.services import node_watch
from app.services.agent_metrics import answered_now, fetch_agent_status, is_token_mismatch
from app.services.counter_delta import CounterSnapshot, CounterTracker
from app.services.crypto import decrypt_secret
from app.services.metrics_store import SAMPLE_INTERVAL_SEC, insert_samples, prune_old

log = logging.getLogger("remna.metrics")

_PRUNE_EVERY = 40
# Прошлые значения счётчиков TCP/OOM по нодам — для дельт между тиками.
_counters = CounterTracker()


def _snapshot(ag: object | None) -> CounterSnapshot | None:
    if ag is None or not getattr(ag, "present", False):
        return None
    return CounterSnapshot(
        boot_id=getattr(ag, "boot_id", None),
        tcp_out=getattr(ag, "tcp_out_segs_total", None),
        tcp_retrans=getattr(ag, "tcp_retrans_segs_total", None),
        oom_kill=getattr(ag, "oom_kill_total", None),
        tcp_syn_retrans=getattr(ag, "tcp_syn_retrans_total", None),
        tcp_orig_data=getattr(ag, "tcp_orig_data_sent_total", None),
    )


def _answered(node: Node, ag: object | None) -> tuple[bool | None, str | None]:
    """Ответил ли агент на этот опрос — для следилки за нодами.

    В rows ниже идёт сглаженный ответ (первые осечки прикрыты прошлым удачным),
    а тут нужен честный: по нему считается, сколько нода молчит.
    """
    if not node.agent_token_enc:
        return None, None
    if ag is None:
        return False, None
    if is_token_mismatch(ag):  # type: ignore[arg-type]
        return False, "token"
    return answered_now(str(node.id)), None


async def sample_once() -> int:
    async with SessionLocal() as db:
        # Хостер нужен следилке за нодами: он идёт в текст тревоги.
        result = await db.execute(select(Node).options(selectinload(Node.hosting)))
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
        from app.services.remnawave_api import rw_fleet_by_address, users_online

        online_task = asyncio.to_thread(rw_fleet_by_address)
        agent_pairs = await asyncio.gather(*(one_agent(n) for n in nodes), return_exceptions=True)
        try:
            panel = await online_task
        except Exception:
            log.warning("xray online unavailable this tick")
            panel = {}
        by_address = users_online(panel)
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
            swap = getattr(ag, "swap_percent", None) if ag is not None else None
            ru_ms = getattr(ag, "ru_ping_ms", None) if ag is not None else None
            ru_loss = getattr(ag, "ru_loss_pct", None) if ag is not None else None
            deltas = _counters.update(nid, _snapshot(ag))
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
                    **deltas,
                    "swap_percent": swap if present else None,
                    "ru_ping_ms": ru_ms if present else None,
                    "ru_loss_pct": ru_loss if present else None,
                }
            )
        written = await insert_samples(db, rows)

        # Те же ответы агентов — следилке за нодами. Её сбой не должен стоить
        # графикам отсчёта, поэтому после записи и в своём try.
        try:
            await node_watch.observe(
                db, nodes, {str(n.id): _answered(n, agents.get(str(n.id))) for n in nodes}, panel)
        except Exception:
            log.exception("node watch failed")
            await db.rollback()
        return written


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
