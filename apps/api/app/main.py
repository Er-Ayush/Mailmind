from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import engine
from app.routers import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown hooks (like NestJS onModuleInit/onModuleDestroy)."""
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="AI email agent — chat with your Gmail inbox.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)

    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        return {"app": settings.app_name, "env": settings.app_env, "docs": "/docs"}

    return app


app = create_app()
