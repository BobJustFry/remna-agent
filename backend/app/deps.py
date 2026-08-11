from fastapi import Depends, HTTPException, Request, status

from app.config import settings
from app.services.auth import verify_session_token


async def require_user(request: Request) -> str:
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    username = verify_session_token(token)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return username


RequireUser = Depends(require_user)
