import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.config import settings
from app.deps import require_user
from app.schemas import LoginRequest, UserOut
from app.services.auth import check_credentials, create_session_token
from app.services.client_ip import client_ip
from app.services.login_guard import (
    assert_login_allowed,
    register_login_failure,
    register_login_success,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_KW = dict(
    httponly=True,
    samesite="lax",
    secure=settings.cookie_secure,
    path="/",
)


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, request: Request, response: Response) -> UserOut:
    ip = client_ip(request)
    assert_login_allowed(ip, body.username)
    ok = check_credentials(body.username, body.password)
    if not ok:
        register_login_failure(ip, body.username)
        await asyncio.sleep(0.4)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )
    register_login_success(ip, body.username)
    token = create_session_token(settings.admin_username)
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_max_age,
        **_COOKIE_KW,
    )
    return UserOut(username=settings.admin_username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(settings.cookie_name, **_COOKIE_KW)


@router.get("/me", response_model=UserOut)
async def me(username: str = Depends(require_user)) -> UserOut:
    return UserOut(username=username)
