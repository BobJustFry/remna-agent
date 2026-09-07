from fastapi import Depends, HTTPException, Request, status

from app.config import settings
from app.db import SessionLocal
from app.services import api_tokens
from app.services.auth import verify_session_token


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


async def require_session_user(request: Request) -> str:
    """Только сессия из браузера (cookie).

    Для управления токенами: токен не должен уметь выпускать другие токены.
    """
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise _unauthorized()
    username = verify_session_token(token)
    if not username:
        raise _unauthorized()
    return username


async def require_user(request: Request) -> str:
    """Сессия из браузера или API-токен в ``Authorization: Bearer``.

    Токен приравнивается к админу — пользователь в панели один. Имя токена
    кладём в ``request.state``, чтобы в логах было видно, кто ходил.
    """
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        raw = auth[7:].strip()
        async with SessionLocal() as db:
            row = await api_tokens.authenticate(db, raw)
        if row is None:
            raise _unauthorized()
        request.state.api_token = row.name
        return settings.admin_username
    return await require_session_user(request)


RequireUser = Depends(require_user)
