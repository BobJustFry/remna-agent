"""Доступ к ИИ через хосты: матрица последнего прогона, история, запуск, настройки."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_session_user, require_user
from app.models import AiCheck, AiRun, AppSetting
from app.schemas import (
    AiAccessConfigOut,
    AiAccessConfigUpdate,
    AiAccessStatusOut,
    AiCellOut,
    AiHistoryPointOut,
    AiHostHistoryOut,
    AiHostRowOut,
    AiRunOut,
    AiServiceOut,
)
from app.services import ai_access, subwatch
from app.services.crypto import encrypt_secret

router = APIRouter(prefix="/ai-access", tags=["ai-access"])


def _run(r: AiRun | None) -> AiRunOut | None:
    if r is None:
        return None
    return AiRunOut(
        id=r.id, started_at=r.started_at, finished_at=r.finished_at, status=r.status,
        trigger=r.trigger, hosts_total=r.hosts_total, hosts_done=r.hosts_done or 0,
        error=r.error, changes=r.changes or [],
    )


def _cells(results: dict, prev: dict | None = None) -> dict[str, AiCellOut]:
    out: dict[str, AiCellOut] = {}
    for key, cell in (results or {}).items():
        if not isinstance(cell, dict):
            continue
        before = ((prev or {}).get(key) or {}).get("v") if prev is not None else None
        v = cell.get("v") or ai_access.ERROR
        out[key] = AiCellOut(verdict=v, note=cell.get("note") or "", tries=int(cell.get("tries") or 0),
                             prev=before if before and before != v else None)
    return out


async def _config_out(db: AsyncSession, cfg: dict) -> AiAccessConfigOut:
    sw = await subwatch.get_config(db)
    own = (cfg.get("sub_url") or "").strip()
    return AiAccessConfigOut(
        **{k: cfg.get(k, v) for k, v in ai_access.DEFAULTS.items()},
        gemini_key_set=bool(await ai_access.gemini_key(db)),
        sub_from_subwatch=not own,
        sub_configured=bool(own or (sw.get("url") or "").strip()),
        bot_ready=bool(await subwatch.bot_token(db))
        and bool((cfg.get("chat_id") or "").strip() or (sw.get("chat_id") or "").strip()),
    )


async def _status(db: AsyncSession) -> AiAccessStatusOut:
    cfg = await ai_access.get_config(db)
    last_row = await db.execute(select(AiRun).order_by(AiRun.started_at.desc()).limit(1))
    last = last_row.scalars().first()
    running = last if last is not None and last.status == "running" else None

    shown = await ai_access.previous_done(db)
    prev = await ai_access.previous_done(db, before=shown.started_at) if shown else None

    rows: list[AiHostRowOut] = []
    if shown is not None:
        prev_by_key: dict[str, AiCheck] = {}
        if prev is not None:
            pr = await db.execute(select(AiCheck).where(AiCheck.run_id == prev.id))
            prev_by_key = {c.host_key: c for c in pr.scalars()}
        cur = await db.execute(select(AiCheck).where(AiCheck.run_id == shown.id))
        # Порядок — как в подписке, то есть как хосты видит клиент. Пишутся
        # строки в порядке завершения, поэтому сортируем по сохранённой позиции.
        for c in sorted(cur.scalars(), key=lambda x: (x.position, x.host_key)):
            old = prev_by_key.get(c.host_key)
            cells = _cells(c.results, old.results if old else None)
            prev_cc = old.ai_country if old and old.ai_country != c.ai_country else None
            rows.append(AiHostRowOut(
                host=c.host, host_key=c.host_key, checked_at=c.checked_at, tunnel=c.tunnel,
                exit_ip=c.exit_ip, exit_country=c.exit_country, ai_ip=c.ai_ip,
                ai_country=c.ai_country, ai_warp=c.ai_warp, prev_ai_country=prev_cc,
                duration_ms=c.duration_ms or 0, error=c.error, results=cells,
                changed=any(x.prev for x in cells.values()) or bool(prev_cc),
                is_new=prev is not None and old is None,
            ))

    has_gemini_api = bool(await ai_access.gemini_key(db)) or any("gemini_api" in r.results for r in rows)
    services = [AiServiceOut(key=k, name=n) for k, n in ai_access.SERVICES
                if k != "gemini_api" or has_gemini_api]

    next_at = None
    if cfg.get("enabled"):
        hours = max(1.0, float(cfg.get("interval_hours") or 12))
        if last is None:
            next_at = ai_access.now()
        elif last.status != "running":
            step = timedelta(hours=hours)
            if last.status in ("failed", "aborted"):
                step = min(step, ai_access.FAILED_RETRY_AFTER)
            next_at = max(last.started_at + step, ai_access.now())

    return AiAccessStatusOut(
        config=await _config_out(db, cfg), services=services, running=_run(running),
        last=_run(last), shown=_run(shown), prev=_run(prev), rows=rows, next_run_at=next_at,
    )


@router.get("/status", response_model=AiAccessStatusOut)
async def get_status(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AiAccessStatusOut:
    return await _status(db)


@router.post("/run", response_model=AiAccessStatusOut)
async def run_now(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AiAccessStatusOut:
    cfg = await ai_access.get_config(db)
    if not await ai_access.effective_sub_url(db, cfg):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Не задан адрес подписки ни здесь, ни в следилке")
    try:
        await ai_access.start_run("manual")
    except ai_access.AlreadyRunning:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Прогон уже идёт") from None
    return await _status(db)


@router.get("/runs", response_model=list[AiRunOut])
async def list_runs(
    limit: int = Query(default=30, ge=1, le=200),
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[AiRunOut]:
    rows = await db.execute(select(AiRun).order_by(AiRun.started_at.desc()).limit(limit))
    return [r for r in (_run(x) for x in rows.scalars()) if r is not None]


@router.get("/history", response_model=AiHostHistoryOut)
async def host_history(
    key: str = Query(min_length=1, max_length=255),
    days: int = Query(default=30, ge=1, le=365),
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AiHostHistoryOut:
    since = ai_access.now() - timedelta(days=days)
    rows = await db.execute(
        select(AiCheck).where(AiCheck.host_key == key, AiCheck.checked_at >= since)
        .order_by(AiCheck.checked_at.asc())
    )
    points = [
        AiHistoryPointOut(
            run_id=c.run_id, checked_at=c.checked_at, host=c.host, tunnel=c.tunnel,
            exit_ip=c.exit_ip, exit_country=c.exit_country, ai_ip=c.ai_ip,
            ai_country=c.ai_country, ai_warp=c.ai_warp, error=c.error, results=_cells(c.results),
        )
        for c in rows.scalars()
    ]
    return AiHostHistoryOut(host_key=key, days=days, points=points)


@router.patch("/settings", response_model=AiAccessStatusOut)
async def update_settings(
    body: AiAccessConfigUpdate,
    _: str = Depends(require_session_user),   # настройки меняет человек, не скрипт
    db: AsyncSession = Depends(get_db),
) -> AiAccessStatusOut:
    cfg = await ai_access.get_config(db)
    data = body.model_dump(exclude_unset=True)
    key = data.pop("gemini_api_key", None)

    for name, value in data.items():
        if value is None:
            continue
        cfg[name] = value.strip() if isinstance(value, str) else value

    if key is not None:
        row = await db.get(AppSetting, ai_access.GEMINI_KEY_KEY)
        if row is None:
            row = AppSetting(key=ai_access.GEMINI_KEY_KEY, value_enc=None)
            db.add(row)
            await db.flush()
        # Ключ — секрет: в базе шифрованный, наружу не отдаётся.
        row.value_enc = encrypt_secret(key.strip()) if key.strip() else None

    await ai_access.save_config(db, cfg)
    await db.commit()
    return await _status(db)
