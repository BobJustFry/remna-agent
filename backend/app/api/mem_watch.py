"""Памятьсторож: состояние нод по памяти, журнал, настройки, ручной перезапуск ядра."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_session_user, require_user
from app.models import Node
from app.services import mem_watch
from app.services.agent_metrics import peek_cached_status
from app.services.remnawave_api import rw_configured, rw_fleet

router = APIRouter(prefix="/mem-watch", tags=["mem-watch"])


class MemConfigOut(BaseModel):
    enabled: bool
    dry_run: bool
    auto_restart: bool
    notify: bool
    chat_id: str
    rss_min_mb: int
    rss_pct: int
    avail_pct: int
    swap_pct: int
    critical_avail_pct: int
    oom_window_min: int
    confirm_ticks: int
    clear_ticks: int
    remind_hours: int
    cooldown_h: int
    min_core_uptime_h: int
    max_per_node_24h: int
    max_per_node_7d: int
    fleet_gap_min: int
    fleet_per_hour: int
    window_start: int
    window_end: int
    utc_offset: int
    post_check_after_s: int
    post_check_deadline_s: int
    relapse_h: int
    exclude: list[str]
    exclude_contains: list[str]
    bot_ready: bool = False


class MemConfigUpdate(BaseModel):
    enabled: bool | None = None
    dry_run: bool | None = None
    auto_restart: bool | None = None
    notify: bool | None = None
    chat_id: str | None = None
    rss_min_mb: int | None = None
    rss_pct: int | None = None
    avail_pct: int | None = None
    swap_pct: int | None = None
    critical_avail_pct: int | None = None
    oom_window_min: int | None = None
    confirm_ticks: int | None = None
    clear_ticks: int | None = None
    remind_hours: int | None = None
    cooldown_h: int | None = None
    min_core_uptime_h: int | None = None
    max_per_node_24h: int | None = None
    max_per_node_7d: int | None = None
    fleet_gap_min: int | None = None
    fleet_per_hour: int | None = None
    window_start: int | None = None
    window_end: int | None = None
    utc_offset: int | None = None
    post_check_after_s: int | None = None
    post_check_deadline_s: int | None = None
    relapse_h: int | None = None
    exclude: list[str] | None = None
    exclude_contains: list[str] | None = None


class MemNodeOut(BaseModel):
    node_id: str
    name: str
    host: str
    checked_at: str | None = None
    rss_mb: float | None = None
    rss_source: str | None = None
    rss_limit_mb: float | None = None
    mem_total_mb: float | None = None
    avail_pct: float | None = None
    swap_percent: float | None = None
    core_uptime_s: int | None = None
    kinds: list[str] = []
    reasons: list[str] = []
    alarm: bool = False
    critical: bool = False
    alarm_since: str | None = None
    action: str = "none"
    action_reason: str = ""
    blocked: str | None = None
    pending: bool = False
    last_restart_at: str | None = None
    excluded: bool = False


class MemLogRow(BaseModel):
    ts: str
    kind: str
    node_id: str | None = None
    name: str | None = None
    text: str
    dry_run: bool = False
    notified: bool = False


class MemStatusOut(BaseModel):
    config: MemConfigOut
    nodes: list[MemNodeOut]
    log: list[MemLogRow]
    alarms: int = 0


async def _config_out(db: AsyncSession, cfg: dict) -> MemConfigOut:
    return MemConfigOut(**{k: cfg[k] for k in mem_watch.DEFAULTS}, bot_ready=await mem_watch.bot_ready(db, cfg))


async def _status(db: AsyncSession) -> MemStatusOut:
    cfg = await mem_watch.get_config(db)
    state = await mem_watch.get_state(db)
    rows = (await db.execute(select(Node).where(Node.monitoring_enabled.is_(True)).order_by(Node.name))).scalars().all()
    out: list[MemNodeOut] = []
    for n in rows:
        st = state["nodes"].get(str(n.id))
        if not st:
            continue
        last = st.get("last") or {}
        alarm = st.get("alarm") or {}
        dec = st.get("decision") or {}
        out.append(MemNodeOut(
            node_id=str(n.id), name=n.name, host=n.host, checked_at=last.get("at"),
            rss_mb=last.get("rss_mb"), rss_source=last.get("rss_source"),
            rss_limit_mb=last.get("rss_limit_mb"), mem_total_mb=last.get("mem_total_mb"),
            avail_pct=last.get("avail_pct"), swap_percent=last.get("swap_percent"),
            core_uptime_s=last.get("core_uptime_s"), kinds=last.get("kinds") or [],
            reasons=last.get("reasons") or [], alarm=bool(alarm),
            critical=bool(alarm.get("critical")), alarm_since=alarm.get("since"),
            action=dec.get("action") or "none", action_reason=dec.get("reason") or "",
            blocked=(st.get("blocked") or {}).get("reason"), pending=bool(st.get("pending")),
            last_restart_at=st.get("last_restart_at"),
            excluded=mem_watch.is_excluded(n.name, cfg),
        ))
    log = await mem_watch.get_log(db)
    return MemStatusOut(
        config=await _config_out(db, cfg), nodes=out,
        log=[MemLogRow(**{k: r.get(k) for k in MemLogRow.model_fields if r.get(k) is not None})
             for r in log[-150:][::-1]],
        alarms=sum(1 for x in out if x.alarm),
    )


@router.get("/status", response_model=MemStatusOut)
async def get_status(_: str = Depends(require_user), db: AsyncSession = Depends(get_db)) -> MemStatusOut:
    return await _status(db)


@router.patch("/settings", response_model=MemStatusOut)
async def update_settings(
    body: MemConfigUpdate,
    _: str = Depends(require_session_user),   # настройки и боевой режим меняет человек, не скрипт
    db: AsyncSession = Depends(get_db),
) -> MemStatusOut:
    old = await mem_watch.get_config(db)
    cfg = dict(old)
    for name, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        cfg[name] = value
    cfg = mem_watch.clean_config(cfg)
    await mem_watch.save_config(db, cfg)
    if old.get("dry_run") != cfg["dry_run"]:
        await mem_watch._append_log(db, [mem_watch._row(
            mem_watch._now(), None, "mode",
            "боевой режим ВКЛЮЧЁН: автоперезапуск будет выполняться" if not cfg["dry_run"]
            else "включён сухой прогон: перезапуски не выполняются", cfg, dry_run=cfg["dry_run"])])
    await db.commit()
    return await _status(db)


class RestartBody(BaseModel):
    confirm: bool = False


@router.post("/nodes/{node_id}/restart")
async def restart_core(
    node_id: uuid.UUID,
    body: RestartBody,
    _: str = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Ручной перезапуск ядра: только из браузера, только с подтверждением."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Нужно подтверждение")
    node = await db.get(Node, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")
    if not rw_configured():
        raise HTTPException(status_code=400, detail="Панель Remnawave не настроена")
    fleet = await asyncio.to_thread(rw_fleet)
    if fleet is None:
        raise HTTPException(status_code=502, detail="Панель не ответила")
    ag = peek_cached_status(str(node.id))
    obs = mem_watch.build_obs(node, ag, fleet.get(node.host), None, answered=ag is not None)
    res = await mem_watch.manual_restart(db, node, obs)
    if not res.get("ok"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=res.get("error") or "не удалось")
    return res


@router.post("/nodes/{node_id}/reset-block")
async def reset_block(
    node_id: uuid.UUID,
    _: str = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    done = await mem_watch.reset_block(db, str(node_id))
    return {"reset": done}
