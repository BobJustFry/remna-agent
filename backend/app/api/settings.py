import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_session_user, require_user
from app.models import AppSetting
from app.schemas import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenOut,
    AppSettingsOut,
    AppSettingsUpdate,
    RemnaScriptDefaults,
)
from app.services import api_tokens
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.metrics_store import clear_all_samples

router = APIRouter(prefix="/settings", tags=["settings"])

REMNA_SECRET_KEY = "remna_secret_key"
REMNA_DEFAULTS_KEY = "remnanode_defaults"


async def _get_or_create(db: AsyncSession, key: str) -> AppSetting:
    row = await db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value_enc=None)
        db.add(row)
        await db.flush()
    return row


def _load_defaults(raw: str | None) -> RemnaScriptDefaults:
    if not raw:
        return RemnaScriptDefaults()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return RemnaScriptDefaults.model_validate(data)
    except Exception:  # noqa: BLE001
        pass
    return RemnaScriptDefaults()


async def _read_settings(db: AsyncSession) -> AppSettingsOut:
    secret_row = await db.get(AppSetting, REMNA_SECRET_KEY)
    defaults_row = await db.get(AppSetting, REMNA_DEFAULTS_KEY)
    secret = ""
    if secret_row and secret_row.value_enc:
        try:
            secret = decrypt_secret(secret_row.value_enc)
        except Exception:  # noqa: BLE001
            secret = ""
    defaults = _load_defaults(defaults_row.value_enc if defaults_row else None)
    return AppSettingsOut(remna_secret_key=secret, defaults=defaults)


@router.get("", response_model=AppSettingsOut)
async def get_settings(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AppSettingsOut:
    return await _read_settings(db)


@router.patch("", response_model=AppSettingsOut)
async def update_settings(
    body: AppSettingsUpdate,
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AppSettingsOut:
    if body.clear_remna_secret_key:
        row = await db.get(AppSetting, REMNA_SECRET_KEY)
        if row:
            await db.delete(row)

    if body.remna_secret_key is not None:
        row = await _get_or_create(db, REMNA_SECRET_KEY)
        value = body.remna_secret_key.strip()
        row.value_enc = encrypt_secret(value) if value else None

    if body.defaults is not None:
        row = await _get_or_create(db, REMNA_DEFAULTS_KEY)
        row.value_enc = json.dumps(body.defaults.model_dump(mode="json"), ensure_ascii=False)

    await db.commit()
    return await _read_settings(db)


@router.post("/reset-metrics")
async def reset_metrics(
    _: str = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    deleted = await clear_all_samples(db)
    return {"deleted": deleted}


# --- API-токены -------------------------------------------------------------
# Управление только из браузерной сессии: токен не должен плодить токены.


@router.get("/api-tokens", response_model=list[ApiTokenOut])
async def list_api_tokens(
    _: str = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiTokenOut]:
    rows = await api_tokens.list_tokens(db)
    return [ApiTokenOut.model_validate(r) for r in rows]


@router.post("/api-tokens", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_api_token(
    body: ApiTokenCreate,
    _: str = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> ApiTokenCreated:
    row, token = await api_tokens.create_token(db, body.name)
    return ApiTokenCreated(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        token=token,
    )


@router.delete("/api-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(
    token_id: uuid.UUID,
    _: str = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if not await api_tokens.revoke_token(db, token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Токен не найден")
