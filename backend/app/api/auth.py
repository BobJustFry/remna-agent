from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.config import settings
from app.deps import require_user
from app.schemas import LoginRequest, UserOut
from app.services.auth import check_credentials, create_session_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, response: Response) -> UserOut:
    if not check_credentials(body.username, body.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль")
    token = create_session_token(body.username)
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_max_age,
        path="/",
    )
    return UserOut(username=body.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(settings.cookie_name, path="/")


@router.get("/me", response_model=UserOut)
async def me(username: str = Depends(require_user)) -> UserOut:
    return UserOut(username=username)
