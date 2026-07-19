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

    # Google OAuth (console.cloud.google.com)
    google_client_id: str = ""
    google_client_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # Gemini (aistudio.google.com/apikey)
    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "models/text-embedding-004"

    # Sessions / crypto — override both in .env for anything beyond local dev.
    # FERNET_KEY generate: python -c
    #   "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    session_secret: str = "dev-session-secret-change-me"
    fernet_key: str = ""

    # Sync behavior
    sync_days: int = 30
    embed_batch_size: int = 64

    frontend_origin: str = "http://localhost:3000"

    @property
    def sync_database_url(self) -> str:
        """Sync (psycopg) URL for alembic + celery workers + langgraph checkpointer."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — import this, not Settings() directly."""
    return Settings()
