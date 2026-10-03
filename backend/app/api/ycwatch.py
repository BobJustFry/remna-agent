"""Следилка за Yandex Cloud: баланс и ВМ страницы подписки, настройки, опрос по требованию."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_session_user, require_user
from app.models import YcCheck
from app.schemas import (
    YcCheckOut,
    YcVmOut,
    YcwatchConfigOut,
    YcwatchConfigUpdate,
    YcwatchStatusOut,
)
from app.services import subwatch, ycwatch

router = APIRouter(prefix="/ycwatch", tags=["ycwatch"])


def _row(c: YcCheck) -> YcCheckOut:
    return YcCheckOut(
        checked_at=c.checked_at, ok=c.ok, balance=c.balance, currency=c.currency,
        account_active=c.account_active, vms=[YcVmOut(**v) for v in (c.vms or [])],
        duration_ms=c.duration_ms, error=c.error,
    )


async def _config_out(db: AsyncSession, cfg: dict) -> YcwatchConfigOut:
    key = await ycwatch.get_key(db)
    return YcwatchConfigOut(
        **{k: cfg.get(k, v) for k, v in ycwatch.DEFAULTS.items()},
        sa_key_set=key is not None,
        sa_key_id=key["id"] if key else None,
        sa_id=key["service_account_id"] if key else None,
        bot_ready=bool(await subwatch.bot_token(db))
        and bool(await ycwatch.effective_chat(db, cfg)),
    )


async def _status(db: AsyncSession) -> YcwatchStatusOut:
    cfg = await ycwatch.get_config(db)
    state = await ycwatch.get_state(db)
    rows = await ycwatch.recent(db, limit=48)
    last_ok = next((r for r in rows if r.ok), None)
    rate, days = await ycwatch.forecast(db, last_ok.balance if last_ok else None)
    return YcwatchStatusOut(
        config=await _config_out(db, cfg),
        level=state.get("level"),
        since=state.get("since"),
        fails=int(state.get("fails") or 0),
        reasons=list(state.get("reasons") or []),
        last=_row(rows[0]) if rows else None,
        last_ok=_row(last_ok) if last_ok else None,
        burn_per_day=rate,
        days_left=days,
        history=[_row(r) for r in rows],
    )


@router.get("/status", response_model=YcwatchStatusOut)
async def get_status(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> YcwatchStatusOut:
    return await _status(db)


@router.post("/check", response_model=YcwatchStatusOut)
async def check_now(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> YcwatchStatusOut:
    if await ycwatch.get_key(db) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Не задан ключ сервисного аккаунта Yandex Cloud")
    await ycwatch.check_once(db)
    return await _status(db)


@router.patch("/settings", response_model=YcwatchStatusOut)
async def update_settings(
    body: YcwatchConfigUpdate,
    _: str = Depends(require_session_user),   # настройки меняет человек, не скрипт
    db: AsyncSession = Depends(get_db),
) -> YcwatchStatusOut:
    cfg = await ycwatch.get_config(db)
    data = body.model_dump(exclude_unset=True)
    raw_key = data.pop("sa_key", None)

    for key, value in data.items():
        if value is None:
            continue
        cfg[key] = value.strip() if isinstance(value, str) else value

    if raw_key is not None:
        # Ключ СА — секрет: в базе лежит зашифрованным и наружу не отдаётся.
        try:
            await ycwatch.set_key(db, raw_key)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Ключ не принят: {exc}") from None

    await ycwatch.save_config(db, cfg)
    await db.commit()
    return await _status(db)
