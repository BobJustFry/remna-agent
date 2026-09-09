"""Следилка за подпиской: состояние, настройки, проверка по требованию."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_session_user, require_user
from app.models import AppSetting
from app.schemas import (
    SubCheckOut,
    SubwatchConfigOut,
    SubwatchConfigUpdate,
    SubwatchStatusOut,
)
from app.services import subwatch
from app.services.crypto import encrypt_secret
from app.services.telegram import send_message

router = APIRouter(prefix="/subwatch", tags=["subwatch"])


def _row(c) -> SubCheckOut:
    return SubCheckOut(
        checked_at=c.checked_at, via=c.via, ok=c.ok, http_status=c.http_status,
        duration_ms=c.duration_ms, hosts=c.hosts, cert_days=c.cert_days, error=c.error,
    )


async def _config_out(db: AsyncSession, cfg: dict) -> SubwatchConfigOut:
    token = await subwatch.bot_token(db)
    return SubwatchConfigOut(**{k: cfg.get(k, v) for k, v in subwatch.DEFAULTS.items()},
                             bot_token_set=bool(token))


async def _status(db: AsyncSession) -> SubwatchStatusOut:
    cfg = await subwatch.get_config(db)
    state = await subwatch.get_state(db)
    rows = await subwatch.recent(db, limit=60)
    cdn = [r for r in rows if r.via == "cdn"]

    # Доля удачных попыток за сутки — короткая память о том, как оно жило.
    uptime = None
    if cdn:
        cutoff = subwatch.now() - timedelta(hours=24)
        day = [r for r in cdn if r.checked_at >= cutoff]
        if day:
            uptime = round(100.0 * sum(1 for r in day if r.ok) / len(day), 1)

    since = state.get("since")
    return SubwatchStatusOut(
        config=await _config_out(db, cfg),
        ok=state.get("ok"),
        since=since,
        fails=int(state.get("fails") or 0),
        last=_row(cdn[0]) if cdn else None,
        history=[_row(r) for r in cdn[:40]],
        uptime_24h=uptime,
    )


@router.get("/status", response_model=SubwatchStatusOut)
async def get_status(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> SubwatchStatusOut:
    return await _status(db)


@router.post("/check", response_model=SubwatchStatusOut)
async def check_now(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> SubwatchStatusOut:
    cfg = await subwatch.get_config(db)
    if not (cfg.get("url") or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Не задан адрес подписки")
    await subwatch.check_once(db, cfg)
    return await _status(db)


@router.patch("/settings", response_model=SubwatchStatusOut)
async def update_settings(
    body: SubwatchConfigUpdate,
    _: str = Depends(require_session_user),   # настройки меняет человек, не скрипт
    db: AsyncSession = Depends(get_db),
) -> SubwatchStatusOut:
    cfg = await subwatch.get_config(db)
    data = body.model_dump(exclude_unset=True)
    token = data.pop("bot_token", None)

    for key, value in data.items():
        if value is None:
            continue
        cfg[key] = value.strip() if isinstance(value, str) else value

    if token is not None:
        row = await db.get(AppSetting, subwatch.TG_TOKEN_KEY)
        if row is None:
            row = AppSetting(key=subwatch.TG_TOKEN_KEY, value_enc=None)
            db.add(row)
            await db.flush()
        # Токен бота — секрет: в базе лежит зашифрованным и наружу не отдаётся.
        row.value_enc = encrypt_secret(token.strip()) if token.strip() else None

    await subwatch.save_config(db, cfg)
    await db.commit()
    return await _status(db)


@router.post("/telegram/test")
async def telegram_test(
    _: str = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    cfg = await subwatch.get_config(db)
    token = await subwatch.bot_token(db)
    chat = (cfg.get("chat_id") or "").strip()
    if not token or not chat:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Сначала задайте токен бота и id чата")
    err = await asyncio.to_thread(
        send_message, token, chat,
        "✅ Проверка связи от рагента. Уведомления настроены.")
    if err:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=err)
    return {"sent": True}
