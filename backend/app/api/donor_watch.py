"""Сторож доноров ИИ: состояние, журнал, настройки, снятие остановки, внеочередной цикл."""
from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_session_user, require_user
from app.services import donor_watch as dw
from app.services import donor_watch_runner as runner

router = APIRouter(prefix="/donor-watch", tags=["donor-watch"])


class DonorConfigUpdate(BaseModel):
    enabled: bool | None = None
    dry_run: bool | None = None
    notify: bool | None = None
    chat_id: str | None = None
    interval_min: int | None = None
    spread_s: int | None = None
    gap_min_s: int | None = None
    gap_max_s: int | None = None
    recheck_delay_s: int | None = None
    req_timeout_s: int | None = None
    transport: str | None = None
    donor_ports: list[int] | None = None
    donor_inbound_tags: list[str] | None = None
    services: list[str] | None = None
    donors: list[dict] | None = None
    priority: dict[str, list[str]] | None = None
    probe_hops: dict[str, list[str]] | None = None
    probe_hops_only: bool | None = None
    probe_hops_exclude: list[str] | None = None
    cycle_budget_s: int | None = None
    egress_overrides: dict[str, str] | None = None
    back_after_cycles: int | None = None
    back_min_gap_min: int | None = None
    quarantine_min: int | None = None
    max_patches_24h: int | None = None
    max_switches_binding_24h: int | None = None
    post_check_s: int | None = None
    allow_unverified_allowlist: bool | None = None
    auto_candidates: bool | None = None
    allowlist_every_h: int | None = None
    remind_hours: int | None = None
    utc_offset: int | None = None


async def _status(db: AsyncSession) -> dict:
    cfg = await dw.get_config(db)
    st = await dw.get_state(db)
    log = await dw.get_log(db)
    donors = []
    reg = st.get("registry") or {}
    for d in reg.get("donors") or []:
        ds = (st.get("donors") or {}).get(d["key"]) or {}
        fw = ds.get("allowlist") or {}
        donors.append({**d, "services": ds.get("svc") or {}, "good_hops": ds.get("good_hops") or [],
                       "allowlist": {"checked_at": fw.get("checked_at"), "open": fw.get("open", False),
                                     "count": len(fw["ips"]) if fw.get("ips") is not None else None,
                                     "error": fw.get("error")}})
    bindings = []
    for b in reg.get("bindings") or []:
        bs = (st.get("bindings") or {}).get(b["id"]) or {}
        bindings.append({**b, "home": bs.get("home"), "switched_at": bs.get("switched_at")})
    now = dw._now()
    return {
        "config": cfg,
        "bot_ready": await dw.bot_ready(db, cfg),
        "last_cycle": st.get("last_cycle"),
        "halted": st.get("halted"),
        "pending": st.get("pending"),
        "patches_24h": dw.patches_24h(st, now),
        "alerts": [{"key": k, **v} for k, v in (st.get("alerts") or {}).items()],
        "issues": reg.get("issues") or [],
        "donors": donors,
        "bindings": bindings,
        "log": log[-150:][::-1],
    }


@router.get("/status")
async def get_status(_: str = Depends(require_user), db: AsyncSession = Depends(get_db)) -> dict:
    return await _status(db)


@router.patch("/settings")
async def update_settings(
    body: DonorConfigUpdate,
    _: str = Depends(require_session_user),   # боевой режим и реестр меняет человек, не скрипт
    db: AsyncSession = Depends(get_db),
) -> dict:
    old = await dw.get_config(db)
    cfg = dict(old)
    for name, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            cfg[name] = value
    cfg = dw.clean_config(cfg)
    await dw.save_config(db, cfg)
    if old["dry_run"] != cfg["dry_run"]:
        now = dw._now()
        text = ("боевой режим ВКЛЮЧЁН: при падении донора профили будут меняться"
                if not cfg["dry_run"] else "включён сухой прогон: профили не меняются")
        await dw.append_log(db, [dw._row(now, "mode", text, cfg)])
        await dw.notify(db, cfg, text)
    await db.commit()
    return await _status(db)


@router.post("/reset-halt")
async def reset_halt(_: str = Depends(require_session_user), db: AsyncSession = Depends(get_db)) -> dict:
    """Снять остановку автоматики после разбора сбоя. Во время цикла — подождать."""
    try:
        await asyncio.wait_for(dw._lock.acquire(), timeout=5)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=409, detail="Идёт цикл проверки — повторите через несколько минут")
    try:
        st = await dw.get_state(db)
        if st.get("pending"):
            raise HTTPException(status_code=409, detail="Есть незавершённое переключение — сначала его сверка")
        was = st.get("halted")
        st["halted"] = None
        cfg = await dw.get_config(db)
        await dw.save_state(db, st)
        if was:
            await dw.append_log(db, [dw._row(dw._now(), "reset", f"остановка снята вручную (была: {was.get('reason')})", cfg)])
        await db.commit()
    finally:
        dw._lock.release()
    return {"reset": bool(was)}


@router.get("/backups")
async def get_backups(_: str = Depends(require_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Бэкапы профилей, снятые сторожем: номер, время, профиль, причина. Без содержимого."""
    return await dw.list_backups(db)


class RestoreBody(BaseModel):
    backup_id: str
    dry_run: bool = True


@router.post("/restore")
async def restore(body: RestoreBody, _: str = Depends(require_session_user),
                  db: AsyncSession = Depends(get_db)) -> dict:
    """Вернуть профиль из бэкапа. По умолчанию dry_run — только показать разницу аутбаундов."""
    try:
        await asyncio.wait_for(dw._lock.acquire(), timeout=5)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=409, detail="Идёт цикл проверки — повторите через несколько минут")
    try:
        return await runner.restore_backup(db, body.backup_id.strip(), body.dry_run)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — панель/расшифровка: текст без секретов
        raise HTTPException(status_code=502, detail="Не вышло: " + runner._scrub_err(exc))
    finally:
        dw._lock.release()


@router.post("/check")
async def check_now(_: str = Depends(require_session_user), db: AsyncSession = Depends(get_db)) -> dict:
    """Внеочередной цикл — но не чаще, чем раз в interval_min: сервисы ИИ не долбим."""
    cfg = await dw.get_config(db)
    st = await dw.get_state(db)
    last = dw.last_cycle_at(st)
    if last and dw._now() - last < timedelta(minutes=cfg["interval_min"]):
        raise HTTPException(status_code=429, detail=f"Прошлый цикл был меньше {cfg['interval_min']} мин назад")
    runner.force_next = True          # состояние не пишем: его держит цикл под блокировкой
    runner.run_now.set()
    return {"queued": True}
