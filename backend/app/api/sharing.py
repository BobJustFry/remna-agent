from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_user
from app.models import Node
from app.services import sharing_detect
from app.services.sharing_scanner import scan_once as scan_now

router = APIRouter(prefix="/sharing", tags=["sharing"])


class SharingUserHit(BaseModel):
    user_id: int
    username: str = ""
    ips_5m: int
    ips_15m: int
    s16_5m: int
    ips_on_node: int = 0
    reasons: list[str] = []
    rw_nodes: list[str] = []


class SharingNodePeers(BaseModel):
    ips: int = 0
    users: int = 0


class SharingStatusOut(BaseModel):
    scanned_at: str | None = None
    error: str | None = None
    scanning: bool = False
    online_users: int = 0
    flagged: int = 0
    by_agent_id: dict[str, list[SharingUserHit]]
    peers_by_agent_id: dict[str, SharingNodePeers] = {}
    thresholds: dict[str, int]


class SharingDossierOut(BaseModel):
    filename: str
    text: str


def _status_out() -> SharingStatusOut:
    snap = sharing_detect.snapshot()
    return SharingStatusOut(
        scanned_at=snap.get("scanned_at"),
        error=snap.get("error"),
        scanning=bool(snap.get("scanning")),
        online_users=int(snap.get("online_users") or 0),
        flagged=int(snap.get("flagged") or 0),
        by_agent_id=snap.get("by_agent_id") or {},
        peers_by_agent_id=snap.get("peers_by_agent_id") or {},
        thresholds=snap.get("thresholds") or {},
    )


@router.get("/status", response_model=SharingStatusOut)
async def sharing_status(_: str = Depends(require_user)) -> SharingStatusOut:
    return _status_out()


@router.post("/scan", response_model=SharingStatusOut)
async def sharing_scan(_: str = Depends(require_user)) -> SharingStatusOut:
    await scan_now()
    return _status_out()


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
