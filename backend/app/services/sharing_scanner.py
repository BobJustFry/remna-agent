"""Периодический скан шаринга через API Remnawave."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Node
from app.services import sharing_detect, sharing_watch

log = logging.getLogger("remna.sharing")

SCAN_INTERVAL_SEC = 120.0
# Старое чистим раз в сутки: 720 сканов по две минуты.
PRUNE_EVERY = 720


async def scan_once() -> None:
    if not settings.remnawave_panel_url.strip() or not settings.remnawave_api_token.strip():
        return
    async with SessionLocal() as db:
        result = await db.execute(select(Node))
        nodes = [(str(n.id), n.name, n.host) for n in result.scalars().all()]
        cfg = await sharing_watch.get_config(db)
        if not cfg.get("enabled", True):
            return
        profiles = await sharing_watch.profiles(db, cfg)
    try:
        out = await asyncio.to_thread(sharing_detect.scan_once, nodes, cfg, profiles)
    except Exception:
        log.exception("sharing scan failed")
        sharing_detect.mark_error("ошибка скана, смотри лог api")
        return
    if "online_ids" not in out:          # панель не ответила, снимок не обновился
        return
    async with SessionLocal() as db:
        await sharing_watch.record_activity(db, out["online_ids"])
        events = await sharing_watch.handle_scan(db, cfg, out.get("suspects") or [])
        await db.commit()
        if events:
            log.info("sharing: события %s", [(k, h.get("username")) for k, h in events])
            await sharing_watch.notify(db, cfg, events)


async def run_sharing_scanner() -> None:
    await asyncio.sleep(8)
    ticks = 0
    while True:
        try:
            await scan_once()
            ticks += 1
            if ticks % PRUNE_EVERY == 0:
                async with SessionLocal() as db:
                    cfg = await sharing_watch.get_config(db)
                    removed = await sharing_watch.prune_activity(db, cfg)
                    await db.commit()
                    log.info("sharing: вычищено строк профиля: %s", removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("sharing scanner loop")
        await asyncio.sleep(SCAN_INTERVAL_SEC)
