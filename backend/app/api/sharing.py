from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_session_user, require_user
from app.models import Node
from app.services import sharing_ban, sharing_detect, sharing_report, sharing_watch, subwatch
from app.services.sharing_scanner import scan_once as scan_now

router = APIRouter(prefix="/sharing", tags=["sharing"])


class SharingPart(BaseModel):
    key: str
    points: float
    why: str


class SharingUserHit(BaseModel):
    user_id: int
    username: str = ""
    ips_5m: int
    ips_15m: int
    s16_5m: int
    conc_ips: int = 0
    conc_nets: int = 0
    own_ips: int = 0
    ips_on_node: int = 0
    reasons: list[str] = []
    rw_nodes: list[str] = []
    # Оценка и её расшифровка. Старые поля выше оставлены как были: их читает
    # карточка ноды, и ломать её ради нового раздела незачем.
    score: int = 0
    level: str = "green"
    device_limit: int = 0
    unlimited: bool = False
    parts: list[SharingPart] = []
    countries: list[str] = []
    platforms: list[str] = []
    hwid_active: int | None = None
    hwid_total: int | None = None
    night_busy_pct: float | None = None
    traffic_ratio: float | None = None


class SharingNodePeers(BaseModel):
    ips: int = 0
    users: int = 0


class SharingStatusOut(BaseModel):
    scanned_at: str | None = None
    error: str | None = None
    scanning: bool = False
    online_users: int = 0
    flagged: int = 0
    suspects: list[SharingUserHit] = []
    by_agent_id: dict[str, list[SharingUserHit]]
    peers_by_agent_id: dict[str, SharingNodePeers] = {}
    thresholds: dict[str, int]


class SharingDossierOut(BaseModel):
    filename: str
    text: str


class SharingConfigOut(BaseModel):
    enabled: bool
    red: int
    amber: int
    red_streak: int
    green_streak: int
    fallback_device_limit: int
    unlimited_as: int
    ip_per_device: float
    ip_slack_5m: float
    ip_slack_15m: float
    conc_ip_per_device: float
    conc_ip_slack: float
    net_per_device: float
    net_slack: float
    w_conc_ips: float
    w_conc_nets: float
    w_ips_5m: float
    w_ips_15m: float
    w_platforms: float
    w_travel: float
    w_hwid: float
    w_night: float
    w_traffic: float
    traffic_median_mult: float
    night_window_h: int
    night_min_days: int
    night_quiet_pct: float
    night_full_pct: float
    night_keep_days: int
    hwid_active_min: int
    users_refresh_min: int
    max_candidates: int
    notify: bool
    chat_id: str
    remind_hours: int
    auto_ban: bool = False
    auto_ban_score: int = 85
    auto_ban_scans: int = 5
    ban_exclude_ids: list[int] = []
    bot_ready: bool = False


class SharingConfigUpdate(BaseModel):
    enabled: bool | None = None
    red: int | None = None
    amber: int | None = None
    red_streak: int | None = None
    green_streak: int | None = None
    unlimited_as: int | None = None
    ip_per_device: float | None = None
    ip_slack_5m: float | None = None
    ip_slack_15m: float | None = None
    conc_ip_per_device: float | None = None
    conc_ip_slack: float | None = None
    net_per_device: float | None = None
    net_slack: float | None = None
    w_conc_ips: float | None = None
    w_conc_nets: float | None = None
    w_ips_5m: float | None = None
    w_ips_15m: float | None = None
    w_platforms: float | None = None
    w_travel: float | None = None
    w_hwid: float | None = None
    w_night: float | None = None
    w_traffic: float | None = None
    traffic_median_mult: float | None = None
    night_window_h: int | None = None
    night_min_days: int | None = None
    night_quiet_pct: float | None = None
    night_full_pct: float | None = None
    night_keep_days: int | None = None
    hwid_active_min: int | None = None
    users_refresh_min: int | None = None
    max_candidates: int | None = None
    notify: bool | None = None
    chat_id: str | None = None
    remind_hours: int | None = None
    auto_ban: bool | None = None
    auto_ban_score: int | None = None
    auto_ban_scans: int | None = None
    ban_exclude_ids: list[int] | None = None


class BanLogRow(BaseModel):
    ts: str
    action: str
    source: str
    user_id: int
    username: str = ""
    score: int = 0
    ok: bool = False
    skipped: str | None = None
    error: str | None = None
    warn: str | None = None


class BansOut(BaseModel):
    banned: list[BanLogRow] = []
    log: list[BanLogRow] = []


class BanActionOut(BaseModel):
    ok: bool
    skipped: str | None = None
    error: str | None = None
    warn: str | None = None
    steps: list[str] = []


def _status_out() -> SharingStatusOut:
    snap = sharing_detect.snapshot()
    return SharingStatusOut(
        scanned_at=snap.get("scanned_at"),
        error=snap.get("error"),
        scanning=bool(snap.get("scanning")),
        online_users=int(snap.get("online_users") or 0),
        flagged=int(snap.get("flagged") or 0),
        suspects=snap.get("suspects") or [],
        by_agent_id=snap.get("by_agent_id") or {},
        peers_by_agent_id=snap.get("peers_by_agent_id") or {},
        thresholds=snap.get("thresholds") or {},
    )


async def _config_out(db: AsyncSession, cfg: dict) -> SharingConfigOut:
    return SharingConfigOut(
        **{k: cfg.get(k, v) for k, v in sharing_watch.DEFAULTS.items()},
        bot_ready=bool(await subwatch.bot_token(db))
        and bool(await sharing_watch.effective_chat(db, cfg)),
    )


@router.get("/status", response_model=SharingStatusOut)
async def sharing_status(_: str = Depends(require_user)) -> SharingStatusOut:
    return _status_out()


@router.post("/scan", response_model=SharingStatusOut)
async def sharing_scan(_: str = Depends(require_user)) -> SharingStatusOut:
    await scan_now()
    return _status_out()


@router.get("/settings", response_model=SharingConfigOut)
async def sharing_settings(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> SharingConfigOut:
    return await _config_out(db, await sharing_watch.get_config(db))


@router.patch("/settings", response_model=SharingConfigOut)
async def update_sharing_settings(
    body: SharingConfigUpdate,
    _: str = Depends(require_session_user),   # пороги меняет человек, не скрипт
    db: AsyncSession = Depends(get_db),
) -> SharingConfigOut:
    cfg = await sharing_watch.get_config(db)
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if key == "auto_ban_score":
            value = max(1, min(100, int(value)))
        elif key == "auto_ban_scans":
            value = max(1, min(1000, int(value)))
        elif key == "ban_exclude_ids":
            value = sorted({int(v) for v in value if int(v) > 0})
        cfg[key] = value.strip() if isinstance(value, str) else value
    await sharing_watch.save_config(db, cfg)
    await db.commit()
    # Окно хранения профиля могли сузить — пусть пересчитается не через полчаса.
    sharing_watch.forget_profiles()
    return await _config_out(db, cfg)


async def _dossier_for_node(db: AsyncSession, node_id: uuid.UUID) -> SharingDossierOut:
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="нода не найдена")
    snap = sharing_detect.snapshot()
    hits = (snap.get("by_agent_id") or {}).get(str(node_id)) or []
    uids: list[int] = []
    seen: set[int] = set()
    for h in hits:
        uid = int(h.get("user_id"))
        if uid not in seen:
            seen.add(uid)
            uids.append(uid)
    if not uids:
        raise HTTPException(status_code=404, detail="на этой ноде шаринг не найден")
    text = sharing_detect.build_node_dossier(uids)
    names = "-".join(str(u) for u in uids[:4])
    return SharingDossierOut(filename=f"sharing-{node.name}-{names}.txt", text=text)


@router.get("/nodes/{node_id}/dossier", response_model=SharingDossierOut)
async def node_dossier(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> SharingDossierOut:
    return await _dossier_for_node(db, node_id)


@router.get("/nodes/{node_id}/dossier.txt")
async def node_dossier_txt(
    node_id: uuid.UUID,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    data = await _dossier_for_node(db, node_id)
    return PlainTextResponse(
        data.text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{data.filename}"'},
    )


@router.get("/users/{user_id}/dossier", response_model=SharingDossierOut)
async def user_dossier(
    user_id: int,
    _: str = Depends(require_user),
) -> SharingDossierOut:
    return SharingDossierOut(
        filename=f"sharing-user-{user_id}.txt",
        text=sharing_detect.build_dossier(user_id),
    )


@router.get("/users/{user_id}/dossier.txt")
async def user_dossier_txt(
    user_id: int,
    _: str = Depends(require_user),
) -> PlainTextResponse:
    text = sharing_detect.build_dossier(user_id)
    return PlainTextResponse(
        text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="sharing-user-{user_id}.txt"'},
    )


@router.get("/users/{user_id}/report")
async def user_report(
    user_id: int,
    saved: bool = False,
    fmt: str = "md",
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    """Отчёт-доказательство (markdown). Пользователь в жёлтой/красной зоне — свежий отчёт;
    иначе (например, после бана) — копия, сохранённая в момент бана. saved=true — всегда копия.
    fmt=md — технический отчёт, fmt=txt — клиентский текст (простым языком, для пересылки)."""
    txt = fmt == "txt"
    text = filename = None
    if not saved and sharing_report.in_zone(user_id):
        data = await sharing_report.collect(db, user_id)
        if data is not None:
            await db.commit()   # кеш геоданных
            text = sharing_report.build_client_text(data) if txt else sharing_report.build_report(data)
            filename = sharing_report.report_filename(
                user_id, str(data.card.get("username") or ""), ext="txt" if txt else "md")
    if text is None:
        copy = await sharing_report.get_saved(db, user_id)
        if copy is None:
            raise HTTPException(
                status_code=404,
                detail="пользователь не в жёлтой/красной зоне и сохранённого отчёта нет")
        ts = sharing_detect._parse_ts(copy.get("ts"))
        text = str((copy.get("client") if txt else copy.get("text")) or "")
        if not text:
            raise HTTPException(status_code=404, detail="в сохранённой копии нет клиентского текста")
        filename = sharing_report.report_filename(
            user_id, str(copy.get("username") or ""), ts, "txt" if txt else "md")
    return PlainTextResponse(
        text,
        media_type="text/plain; charset=utf-8" if txt else "text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _hit_for(user_id: int) -> dict:
    """Строка подозреваемого из последнего скана; нет её — только id и имя из кэша."""
    for h in sharing_detect.snapshot().get("suspects") or []:
        if int(h.get("user_id")) == user_id:
            return dict(h)
    card = (sharing_detect.users_cache().get("by_id") or {}).get(user_id) or {}
    return {"user_id": user_id, "username": card.get("username", ""), "score": 0, "parts": []}


def _action_out(res: dict) -> BanActionOut:
    return BanActionOut(ok=bool(res.get("ok")), skipped=res.get("skipped"),
                        error=res.get("error"), warn=res.get("warn"),
                        steps=list(res.get("steps") or []))


@router.get("/bans", response_model=BansOut)
async def sharing_bans(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> BansOut:
    rows = await sharing_ban.get_log(db)
    return BansOut(banned=sharing_ban.banned_now(rows)[::-1], log=rows[-50:][::-1])


@router.post("/users/{user_id}/ban", response_model=BanActionOut)
async def sharing_ban_user(
    user_id: int,
    _: str = Depends(require_session_user),   # банит человек из браузера, не скрипт
    db: AsyncSession = Depends(get_db),
) -> BanActionOut:
    cfg = await sharing_watch.get_config(db)
    res = await sharing_ban.ban(db, cfg, _hit_for(user_id), "manual")
    await db.commit()
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=res.get("error") or "панель не ответила")
    return _action_out(res)


@router.post("/users/{user_id}/unban", response_model=BanActionOut)
async def sharing_unban_user(
    user_id: int,
    _: str = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> BanActionOut:
    cfg = await sharing_watch.get_config(db)
    hit = _hit_for(user_id)
    res = await sharing_ban.unban(db, cfg, user_id, str(hit.get("username") or ""))
    await db.commit()
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=res.get("error") or "панель не ответила")
    return _action_out(res)
