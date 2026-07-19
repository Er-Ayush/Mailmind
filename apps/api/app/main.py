import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import engine
from app.routers import auth, health, sync

if get_settings().app_env == "development":
    # Allow Google OAuth over http://localhost and tolerate Google's scope reordering
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(sync.router)

    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        return {"app": settings.app_name, "env": settings.app_env, "docs": "/docs"}

    return app


app = create_app()
