from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_EXAMPLE_SESSION = "change-me-session-secret-min-32-chars"
_EXAMPLE_ENC_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    admin_username: str = "admin"
    admin_password: str = "change-me"
    session_secret: str = _EXAMPLE_SESSION
    credentials_encryption_key: str = _EXAMPLE_ENC_KEY
    database_url: str = "postgresql+asyncpg://remna:remna@db:5432/remna_agent"
    cors_origins: str = "http://localhost:8080,http://localhost:5173"
    cookie_secure: bool = False
    cookie_name: str = "remna_session"
    session_max_age: int = 60 * 60 * 24 * 7
    remnawave_panel_url: str = ""
    remnawave_api_token: str = ""
    # FastAPI conditional OpenAPI: empty string disables /docs /redoc /openapi.json
    # https://fastapi.tiangolo.com/how-to/conditional-openapi/
    openapi_url: str = "/openapi.json"
    allowed_hosts: str = "*"
    trust_proxy: bool = False

    @model_validator(mode="after")
    def apply_production_defaults(self) -> "Settings":
        if self.environment.strip().lower() != "production":
            return self
        self.cookie_secure = True
        self.openapi_url = ""
        if not self.trust_proxy:
            self.trust_proxy = True
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def is_production() -> bool:
    return settings.environment.strip().lower() == "production"


def assert_secure_settings() -> None:
    if not is_production():
        return
    hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
    if not hosts or hosts == ["*"]:
        raise RuntimeError("ALLOWED_HOSTS must be a concrete hostname in production")
    if settings.admin_password in {"change-me", "admin", "password"} or len(settings.admin_password) < 12:
        raise RuntimeError("ADMIN_PASSWORD is too weak for production")
    if settings.session_secret == _EXAMPLE_SESSION or len(settings.session_secret) < 32:
        raise RuntimeError("SESSION_SECRET is too weak for production")
    if settings.credentials_encryption_key == _EXAMPLE_ENC_KEY:
        raise RuntimeError("CREDENTIALS_ENCRYPTION_KEY is still the example value")
    if "localhost" in settings.cors_origins and "https://" not in settings.cors_origins:
        raise RuntimeError("CORS_ORIGINS must be the public HTTPS origin in production")
