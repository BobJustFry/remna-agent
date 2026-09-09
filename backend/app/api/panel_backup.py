"""Бэкап панели: состояние, настройки, снятие по требованию, выдача файла."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_session_user, require_user
from app.models import AppSetting
from app.schemas import (
    BackupFileOut,
    PanelBackupConfigOut,
    PanelBackupConfigUpdate,
    PanelBackupStatusOut,
)
from app.services import panel_backup
from app.services.crypto import encrypt_secret

router = APIRouter(prefix="/panel-backup", tags=["panel-backup"])


async def _status(db: AsyncSession) -> PanelBackupStatusOut:
    cfg = await panel_backup.get_config(db)
    state = await panel_backup.get_state(db)
    files = panel_backup.listing()
    free = None
    try:
        panel_backup.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(panel_backup.BACKUP_DIR).free
    except OSError:
        pass

    return PanelBackupStatusOut(
        config=PanelBackupConfigOut(
            **{k: cfg.get(k, v) for k, v in panel_backup.DEFAULTS.items()},
            ssh_key_set=bool(await panel_backup.ssh_key(db)),
        ),
        last_run_at=state.get("last_run_at"),
        last_ok_at=state.get("last_ok_at"),
        last_error=state.get("last_error"),
        last_size=state.get("last_size"),
        last_duration_ms=state.get("last_duration_ms"),
        files=[BackupFileOut(**f.as_dict()) for f in files[:40]],
        total_bytes=sum(f.size for f in files),
        disk_free_bytes=free,
    )


@router.get("/status", response_model=PanelBackupStatusOut)
async def get_status(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> PanelBackupStatusOut:
    return await _status(db)


@router.post("/run", response_model=PanelBackupStatusOut)
async def run_now(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> PanelBackupStatusOut:
    cfg = await panel_backup.get_config(db)
    if not (cfg.get("host") or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Не задан адрес панели")
    await panel_backup.run_once(db, cfg)
    return await _status(db)


@router.patch("/settings", response_model=PanelBackupStatusOut)
async def update_settings(
    body: PanelBackupConfigUpdate,
    _: str = Depends(require_session_user),   # ключ задаёт человек, не скрипт
    db: AsyncSession = Depends(get_db),
) -> PanelBackupStatusOut:
    cfg = await panel_backup.get_config(db)
    data = body.model_dump(exclude_unset=True)
    key = data.pop("ssh_key", None)

    for name, value in data.items():
        if value is None:
            continue
        cfg[name] = value.strip() if isinstance(value, str) else value

    if key is not None:
        row = await db.get(AppSetting, panel_backup.SSH_KEY_KEY)
        if row is None:
            row = AppSetting(key=panel_backup.SSH_KEY_KEY, value_enc=None)
            db.add(row)
            await db.flush()
        # Ключ — секрет: в базе шифрованный, наружу не отдаётся никогда.
        row.value_enc = encrypt_secret(key) if key.strip() else None

    await panel_backup.save_config(db, cfg)
    await db.commit()
    return await _status(db)


@router.get("/files/{name}")
async def download(
    name: str,
    _: str = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    # Имя сверяем со списком, а не склеиваем путь из того, что прислали.
    if not any(f.name == name for f in panel_backup.listing()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл не найден")
    return FileResponse(panel_backup.BACKUP_DIR / name, filename=name,
                        media_type="application/octet-stream")
