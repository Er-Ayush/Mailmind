from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

engine: AsyncEngine = create_async_engine(get_settings().database_url, pool_pre_ping=True)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session per request (like a NestJS request-scoped provider)."""
    async with async_session() as session:
        yield session
