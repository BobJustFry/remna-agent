"""Следилка за нодами: кто молчит сейчас, журнал простоев, настройки."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_session_user, require_user
from app.schemas import (
    FleetBlindOut,
    NodeDownOut,
    NodeOutageOut,
    NodeWatchConfigOut,
    NodeWatchConfigUpdate,
    NodeWatchStatusOut,
)
from app.services import node_watch, subwatch
from app.services.telegram import send_message

router = APIRouter(prefix="/node-watch", tags=["node-watch"])


async def _config_out(db: AsyncSession, cfg: dict) -> NodeWatchConfigOut:
    return NodeWatchConfigOut(
        **{k: cfg.get(k, v) for k, v in node_watch.DEFAULTS.items()},
        bot_ready=bool(await subwatch.bot_token(db))
        and bool(await node_watch.effective_chat(db, cfg)),
    )


async def _status(db: AsyncSession) -> NodeWatchStatusOut:
    cfg = await node_watch.get_config(db)
    down, fleet = await node_watch.current(db)
    return NodeWatchStatusOut(
        config=await _config_out(db, cfg),
        down=[NodeDownOut(**d) for d in down],
        fleet=FleetBlindOut(**fleet) if fleet else None,
    )


@router.get("/status", response_model=NodeWatchStatusOut)
async def get_status(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> NodeWatchStatusOut:
    return await _status(db)


@router.get("/events", response_model=list[NodeOutageOut])
async def list_events(
    days: int = Query(default=30, ge=1, le=node_watch.KEEP_DAYS),
    limit: int = Query(default=200, ge=1, le=1000),
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[NodeOutageOut]:
    """Журнал «упала/поднялась»: свежие сверху, открытые тревоги — с пустым ended_at."""
    rows = await node_watch.events(db, days=days, limit=limit)
    return [
        NodeOutageOut(
            id=r.id, kind=r.kind, node_id=r.node_id, name=r.node_name, host=r.host,
            hosting=r.hosting, started_at=r.started_at, alerted_at=r.alerted_at,
            ended_at=r.ended_at, resolution=r.resolution, notified=r.notified_at is not None,
            signals=r.signals or {},
        )
        for r in rows
    ]


@router.patch("/settings", response_model=NodeWatchStatusOut)
async def update_settings(
    body: NodeWatchConfigUpdate,
    _: str = Depends(require_session_user),   # настройки меняет человек, не скрипт
    db: AsyncSession = Depends(get_db),
) -> NodeWatchStatusOut:
    cfg = await node_watch.get_config(db)
    data = body.model_dump(exclude_unset=True)

    for name, value in data.items():
        if value is None:
            continue
        if name == "exclude":
            value = sorted({str(x) for x in value})
        cfg[name] = value.strip() if isinstance(value, str) else value

    await node_watch.save_config(db, cfg)
    await db.commit()
    return await _status(db)


@router.post("/telegram/test")
async def telegram_test(
    _: str = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    cfg = await node_watch.get_config(db)
    token = await subwatch.bot_token(db)
    chat = await node_watch.effective_chat(db, cfg)
    if not token or not chat:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Сначала задайте токен бота и id чата — в следилке за подпиской")
    err = await asyncio.to_thread(
        send_message, token, chat,
        "✅ Проверка связи от рагента. Сюда придут сообщения о недоступных нодах.")
    if err:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=err)
    return {"sent": True}
