"""Fleet load report: settings, status, preview and manual send."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_session_user, require_user
from app.services import fleet_report as fr
from app.services import subwatch

router = APIRouter(prefix="/fleet-report", tags=["fleet-report"])


class FleetConfigOut(BaseModel):
    enabled: bool
    commands: bool
    chat_id: str
    full_hours: str
    short_hours: str
    utc_offset: int
    window_days: int
    mem_red_p95: int
    red_days: int
    mem_orange_p95: int
    orange_days: int
    trend_pp: int
    chan_pct: int
    chan_days: int
    save_mem_pct: int
    save_cpu_pct: int
    save_min_gb: int
    headroom_pct: int


class FleetConfigUpdate(BaseModel):
    enabled: bool | None = None
    commands: bool | None = None
    chat_id: str | None = Field(default=None, max_length=64)
    full_hours: str | None = Field(default=None, max_length=64)
    short_hours: str | None = Field(default=None, max_length=64)
    utc_offset: int | None = None
    window_days: int | None = None
    mem_red_p95: int | None = None
    red_days: int | None = None
    mem_orange_p95: int | None = None
    orange_days: int | None = None
    trend_pp: int | None = None
    chan_pct: int | None = None
    chan_days: int | None = None
    save_mem_pct: int | None = None
    save_cpu_pct: int | None = None
    save_min_gb: int | None = None
    headroom_pct: int | None = None


class FleetStatusOut(BaseModel):
    config: FleetConfigOut
    bot_ready: bool
    last_sent_at: str | None = None
    last_kind: str | None = None
    next_report: str | None = None


class PreviewOut(BaseModel):
    text: str


def _cfg_out(cfg: dict) -> FleetConfigOut:
    data = dict(cfg)
    data["full_hours"] = ",".join(str(h) for h in cfg["full_hours"])
    data["short_hours"] = ",".join(str(h) for h in cfg["short_hours"])
    return FleetConfigOut(**{k: data[k] for k in FleetConfigOut.model_fields})


async def _status(db: AsyncSession) -> FleetStatusOut:
    cfg = await fr.get_config(db)
    st = await fr.get_state(db)
    nxt = fr.next_report(datetime.now(timezone.utc), cfg)
    return FleetStatusOut(
        config=_cfg_out(cfg),
        bot_ready=bool(await subwatch.bot_token(db)) and bool(await fr.effective_chat(db, cfg)),
        last_sent_at=st.get("last_sent_at"), last_kind=st.get("last_kind"),
        next_report=nxt.strftime("%d.%m %H:%M МСК") if nxt else None,
    )


@router.get("/status", response_model=FleetStatusOut)
async def get_status(_: str = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await _status(db)


@router.patch("/settings", response_model=FleetStatusOut)
async def update_settings(
    body: FleetConfigUpdate,
    _: str = Depends(require_session_user),     # only a human from the browser may switch it on
    db: AsyncSession = Depends(get_db),
):
    cfg = await fr.get_config(db)
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            cfg[key] = value.strip() if isinstance(value, str) else value
    await fr.save_config(db, cfg)
    await db.commit()
    return await _status(db)


@router.post("/preview", response_model=PreviewOut)
async def preview(kind: str = "full", _: str = Depends(require_user),
                  db: AsyncSession = Depends(get_db)):
    if kind not in ("full", "short"):
        raise HTTPException(status_code=400, detail="kind: full или short")
    cfg = await fr.get_config(db)
    state = await fr.get_state(db)
    text_, _snap = await fr.compose(db, cfg, kind, state, datetime.now(timezone.utc), with_day=False)
    return PreviewOut(text=text_)


@router.post("/send", response_model=FleetStatusOut)
async def send_now(_: str = Depends(require_session_user), db: AsyncSession = Depends(get_db)):
    cfg = await fr.get_config(db)
    if not cfg["enabled"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Отчёт выключен в настройках")
    _text, err = await fr.run_report(db, cfg, "full", "manual")
    if err:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=err)
    return await _status(db)
