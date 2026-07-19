import redis.asyncio as aioredis
from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.db import engine

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness + dependency check: verifies Postgres and Redis are reachable."""
    db_status = "ok"
    redis_status = "ok"

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    try:
        client = aioredis.from_url(get_settings().redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()
    except Exception:
        redis_status = "error"

    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
    return {"status": overall, "db": db_status, "redis": redis_status}
