import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="remna-agent-session")


def create_session_token(username: str) -> str:
    return _serializer().dumps({"u": username})


def verify_session_token(token: str) -> str | None:
    try:
        data = _serializer().loads(token, max_age=settings.session_max_age)
    except (BadSignature, SignatureExpired):
        return None
    username = data.get("u")
    if not isinstance(username, str):
        return None
    if username != settings.admin_username:
        return None
    return username


def check_credentials(username: str, password: str) -> bool:
    # FastAPI HTTP Basic: secrets.compare_digest against timing attacks
    # https://fastapi.tiangolo.com/advanced/security/http-basic-auth/
    user_ok = secrets.compare_digest(
        username.encode("utf-8"),
        settings.admin_username.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        password.encode("utf-8"),
        settings.admin_password.encode("utf-8"),
    )
    return user_ok and pass_ok
