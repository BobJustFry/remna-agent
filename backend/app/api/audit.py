"""Раздел «Автоматизация»: проверки состояния парка.

Фонового расписания здесь намеренно нет — проверки запускаются только кнопкой.
Когда расписание понадобится, его достаточно будет повесить на `audit.run`.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_user
from app.models import AppSetting
from app.schemas import AuditSettingsOut, AuditSettingsUpdate, AuditStatusOut
from app.services import audit

router = APIRouter(prefix="/audit", tags=["audit"])

CERT_DOMAINS_KEY = "audit_cert_domains"


async def _cert_domains(db: AsyncSession) -> list[str]:
    row = await db.get(AppSetting, CERT_DOMAINS_KEY)
    if not row or not row.value_enc:
        return []
    try:
        data = json.loads(row.value_enc)
    except ValueError:
        return []
    return [str(d).strip() for d in data if str(d).strip()] if isinstance(data, list) else []


@router.get("/checks", response_model=AuditStatusOut)
async def list_checks(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AuditStatusOut:
    snap = audit.snapshot()
    return AuditStatusOut(
        checks=snap["checks"],
        any_running=snap["any_running"],
        scheduler_enabled=False,  # расписания нет: раздел работает только по кнопке
        cert_domains=await _cert_domains(db),
    )


@router.post("/checks/{check_id}/run", response_model=AuditStatusOut)
async def run_check(
    check_id: str,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AuditStatusOut:
    if check_id not in audit.CHECKS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проверка не найдена")
    kwargs: dict = {}
    if check_id == "certs":
        kwargs["extra_domains"] = await _cert_domains(db)

    # Проверка ходит по сети и живёт десятки секунд — не держим на ней запрос.
    asyncio.create_task(asyncio.to_thread(audit.run, check_id, **kwargs))
    await asyncio.sleep(0.15)  # дать задаче отметиться как запущенная
    snap = audit.snapshot()
    return AuditStatusOut(
        checks=snap["checks"], any_running=snap["any_running"],
        scheduler_enabled=False, cert_domains=await _cert_domains(db),
    )


@router.get("/settings", response_model=AuditSettingsOut)
async def get_audit_settings(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AuditSettingsOut:
    return AuditSettingsOut(cert_domains=await _cert_domains(db))


@router.patch("/settings", response_model=AuditSettingsOut)
async def update_audit_settings(
    body: AuditSettingsUpdate,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AuditSettingsOut:
    if body.cert_domains is not None:
        cleaned = sorted({d.strip().lower() for d in body.cert_domains if d.strip()})
        row = await db.get(AppSetting, CERT_DOMAINS_KEY)
        if row is None:
            row = AppSetting(key=CERT_DOMAINS_KEY, value_enc=None)
            db.add(row)
            await db.flush()
        # Домены не секрет, поэтому лежат как обычный JSON, а не шифрованными.
        row.value_enc = json.dumps(cleaned, ensure_ascii=False)
        await db.commit()
    return AuditSettingsOut(cert_domains=await _cert_domains(db))
