from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, loaded from environment variables / .env file.

    Pydantic matches env vars case-insensitively: DATABASE_URL -> database_url.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "MailMind"
    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://mailmind:mailmind@localhost:5433/mailmind"
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — import this, not Settings() directly."""
    return Settings()
