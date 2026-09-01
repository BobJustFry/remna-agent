"""Периодический скан шаринга через API Remnawave."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Node
from app.services import sharing_detect

log = logging.getLogger("remna.sharing")

SCAN_INTERVAL_SEC = 120.0


async def scan_once() -> None:
    if not settings.remnawave_panel_url.strip() or not settings.remnawave_api_token.strip():
        return
    async with SessionLocal() as db:
        result = await db.execute(select(Node))
        nodes = [(str(n.id), n.name, n.host) for n in result.scalars().all()]
    try:
        await asyncio.to_thread(sharing_detect.scan_once, nodes)
    except Exception:
        log.exception("sharing scan failed")
        sharing_detect.mark_error("ошибка скана, смотри лог api")


async def run_sharing_scanner() -> None:
    await asyncio.sleep(8)
    while True:
        try:
            await scan_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("sharing scanner loop")
        await asyncio.sleep(SCAN_INTERVAL_SEC)
