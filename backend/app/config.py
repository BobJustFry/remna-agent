from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    admin_username: str = "admin"
    admin_password: str = "change-me"
    session_secret: str = "change-me-session-secret-min-32-chars"
    credentials_encryption_key: str = (
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )
    database_url: str = "postgresql+asyncpg://remna:remna@db:5432/remna_agent"
    cors_origins: str = "http://localhost:8080,http://localhost:5173"
    cookie_secure: bool = False
    cookie_name: str = "remna_session"
    session_max_age: int = 60 * 60 * 24 * 7


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
