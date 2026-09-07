"""Токены доступа к API для скриптов и интеграций.

Токен показывается ровно один раз — при создании. В базе остаётся SHA-256,
поэтому восстановить утерянный токен нельзя, только выпустить новый.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.models import ApiToken

# Префикс нужен, чтобы токен нельзя было спутать ни с чем другим в логах и
# чтобы Bearer-заголовок с чужим форматом отбрасывался без похода в базу.
TOKEN_PREFIX = "rga_"
# Сколько символов показываем в списке, чтобы человек узнал свой токен.
VISIBLE_CHARS = 12
# last_used_at обновляем не чаще раза в минуту: скрипты бьют API десятками
# запросов в секунду, писать в базу на каждый — лишняя нагрузка ради ничего.
LAST_USED_STEP = timedelta(minutes=1)


def generate_token() -> str:
    # 32 байта из системного ГСЧ — перебор невозможен, rate-limit не нужен.
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_prefix(token: str) -> str:
    return token[:VISIBLE_CHARS]


def looks_like_token(value: str) -> bool:
    return value.startswith(TOKEN_PREFIX) and len(value) > len(TOKEN_PREFIX) + 16


async def create_token(db: AsyncSession, name: str) -> tuple[ApiToken, str]:
    """Создать токен. Возвращает запись и сам токен — единственный раз."""
    from app.models import ApiToken

    token = generate_token()
    row = ApiToken(
        id=uuid.uuid4(),
        name=name.strip(),
        prefix=token_prefix(token),
        token_hash=hash_token(token),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, token


async def authenticate(db: AsyncSession, token: str) -> ApiToken | None:
    """Найти токен по значению из Bearer-заголовка; None — доступа нет."""
    from app.models import ApiToken

    if not looks_like_token(token):
        return None
    row = await db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    if row.last_used_at is None or now - row.last_used_at >= LAST_USED_STEP:
        row.last_used_at = now
        await db.commit()
    return row


async def list_tokens(db: AsyncSession) -> list[ApiToken]:
    from app.models import ApiToken

    result = await db.scalars(select(ApiToken).order_by(ApiToken.created_at.desc()))
    return list(result)


async def revoke_token(db: AsyncSession, token_id: uuid.UUID) -> bool:
    from app.models import ApiToken

    row = await db.get(ApiToken, token_id)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    return True
