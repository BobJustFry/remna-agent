"""Доступность приложений (ИИ и TikTok) через хосты: матрица, история, запуск
и отмена проверок, настройки.

Префикс /ai-access остался со времён, когда проверялся только ИИ: на него
завязаны скрипты, поэтому не меняем.
"""
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
    AiRunHostOut,
    AiRunOut,
    AiRunRequest,
    AiServiceOut,
)
from app.services import ai_access, subwatch
from app.services.crypto import encrypt_secret

router = APIRouter(prefix="/ai-access", tags=["ai-access"])


# Хост ещё в работе: в очереди, проверяется или выходит из проверки.
_BUSY = (ai_access.QUEUED, ai_access.RUNNING, ai_access.CANCELLING)


def _hosts(targets: list | None, finished: bool) -> list[AiRunHostOut]:
    out: list[AiRunHostOut] = []
    for t in targets or []:
        if not isinstance(t, dict) or not t.get("key"):
            continue
        state = t.get("state") or ai_access.QUEUED
        # Прогон кончился, а хост всё «ждёт» — значит, до него не дошли.
        if finished and state in _BUSY:
            state = ai_access.ABORTED
        out.append(AiRunHostOut(key=t["key"], host=t.get("host") or t["key"], state=state,
                                error=t.get("error")))
    return out


def _run(r: AiRun | None) -> AiRunOut | None:
    if r is None:
        return None
    return AiRunOut(
        id=r.id, started_at=r.started_at, finished_at=r.finished_at, status=r.status,
        trigger=r.trigger, scope=r.scope or ai_access.FULL, hosts_total=r.hosts_total,
        hosts_done=r.hosts_done or 0, error=r.error, changes=r.changes or [],
        hosts=_hosts(r.targets, finished=r.status != "running"),
    )


async def _running(db: AsyncSession, live: dict | None) -> AiRunOut | None:
    """Идущий прогон. Состояние хостов — из памяти исполнителя, а не из базы:
    после рестарта рагента там пусто, и спиннерам взяться неоткуда."""
    if live is None:
        return None
    hosts = _hosts(live["hosts"], finished=False)
    row = await db.get(AiRun, live["id"])
    return AiRunOut(
        id=live["id"], started_at=row.started_at if row else ai_access.now(), status="running",
        trigger=live["trigger"], scope=live["scope"],
        # У полного прогона до подписки хостов ещё нет — «забираю подписку».
        hosts_total=len(hosts) or None,
        hosts_done=sum(1 for h in hosts if h.state not in _BUSY),
        hosts=hosts, stopping=live["stopping"],
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


def _row(m: ai_access.MatrixRow) -> AiHostRowOut:
    c, old = m.check, m.before
    cells = _cells(c.results, old.results if old else None)
    prev_cc = old.ai_country if old and old.ai_country != c.ai_country else None
    return AiHostRowOut(
        host=c.host, host_key=c.host_key, checked_at=c.checked_at, tunnel=c.tunnel,
        exit_ip=c.exit_ip, exit_country=c.exit_country, ai_ip=c.ai_ip,
        ai_country=c.ai_country, ai_warp=c.ai_warp, prev_ai_country=prev_cc,
        duration_ms=c.duration_ms or 0, error=c.error, results=cells,
        changed=any(x.prev for x in cells.values()) or bool(prev_cc),
        is_new=m.is_new, partial=m.partial,
    )


async def _status(db: AsyncSession) -> AiAccessStatusOut:
    cfg = await ai_access.get_config(db)
    live = ai_access.live()
    last = await ai_access.last_full(db)
    lp = await db.execute(
        select(AiRun).where(AiRun.scope == ai_access.PARTIAL, AiRun.status != "running")
        .order_by(AiRun.started_at.desc()).limit(1))
    last_partial = lp.scalars().first()

    # Матрица — последний результат каждого хоста; состав и порядок задаёт
    # последний удачный полный прогон (shown).
    shown, prev, matrix = await ai_access.load_matrix(db)
    rows = [_row(m) for m in matrix]

    # Что последний выборочный прогон не смог проверить — к строке хоста.
    if last_partial is not None:
        errs = {t["key"]: t.get("error") for t in last_partial.targets or []
                if isinstance(t, dict) and t.get("state") == ai_access.MISSING}
        for r in rows:
            if r.host_key in errs and r.checked_at and r.checked_at < last_partial.started_at:
                r.run_error = errs[r.host_key]

    if live is not None:
        states = {h["key"]: h["state"] for h in live["hosts"]}
        # Полный прогон ещё забирает подписку: его хосты неизвестны, ждут все.
        whole = live["scope"] == ai_access.FULL and not live["resolved"] and not live["stopping"]
        for r in rows:
            r.state = states.get(r.host_key) or (
                ai_access.QUEUED if whole and r.host_key not in live["skipped"] else None)
        # Хост в очереди, которого в матрице ещё нет: новый в подписке или
        # самый первый прогон. Строка без результата, но со спиннером.
        known = {r.host_key for r in rows}
        for h in sorted(live["hosts"], key=lambda h: h.get("pos") or 0):
            if h["key"] not in known and h["state"] in _BUSY:
                rows.append(AiHostRowOut(host=h.get("host") or h["key"], host_key=h["key"],
                                         state=h["state"], is_new=shown is not None))

    has_gemini_api = bool(await ai_access.gemini_key(db)) or any("gemini_api" in r.results for r in rows)
    services = [AiServiceOut(**s) for s in ai_access.service_list(has_gemini_api)]

    # Расписание считается по полным прогонам: выборочные его не сдвигают.
    pending_full = live["pending_full"] if live else None
    next_at = None
    if cfg.get("enabled") and not pending_full:
        hours = max(1.0, float(cfg.get("interval_hours") or 12))
        if last is None:
            next_at = ai_access.now()
        elif last.status != "running":
            step = timedelta(hours=hours)
            if last.status in ("failed", "aborted"):
                step = min(step, ai_access.FAILED_RETRY_AFTER)
            next_at = max(last.started_at + step, ai_access.now())

    return AiAccessStatusOut(
        config=await _config_out(db, cfg), services=services, running=await _running(db, live),
        last=_run(last), shown=_run(shown), prev=_run(prev), last_partial=_run(last_partial),
        pending_full=pending_full, rows=rows, next_run_at=next_at,
    )


def _keys(body: AiRunRequest | None) -> list[str] | None:
    """Ключи хостов из запроса; None — речь про весь прогон."""
    if body is None or body.hosts is None:
        return None
    keys = list(dict.fromkeys(k.strip()[:255] for k in body.hosts if k and k.strip()))
    if not keys:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Не выбрано ни одного хоста")
    return keys


@router.get("/status", response_model=AiAccessStatusOut)
async def get_status(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AiAccessStatusOut:
    return await _status(db)


@router.post("/run", response_model=AiAccessStatusOut)
async def run_now(
    body: AiRunRequest | None = None,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AiAccessStatusOut:
    """Проверить весь парк (без тела) или хосты из hosts.

    Если прогон уже идёт, хосты встают в его очередь. Полный прогон поверх
    выборочного стартует следом за ним; поверх полного — 409.
    """
    keys = _keys(body)
    cfg = await ai_access.get_config(db)
    if not await ai_access.effective_sub_url(db, cfg):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Не задан адрес подписки ни здесь, ни в следилке")
    try:
        await ai_access.start_run("manual", keys)
    except ai_access.AlreadyRunning:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Полный прогон уже идёт или стоит в очереди") from None
    return await _status(db)


@router.post("/cancel", response_model=AiAccessStatusOut)
async def cancel(
    body: AiRunRequest | None = None,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AiAccessStatusOut:
    """Отменить весь прогон (без тела) или проверку хостов из hosts.

    Хост в очереди снимается сразу, идущая проверка прерывается. Отменять
    нечего — не ошибка: прогон мог закончиться, пока нажимали.
    """
    await ai_access.cancel_run(_keys(body))
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
