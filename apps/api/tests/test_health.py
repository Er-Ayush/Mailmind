from httpx import ASGITransport, AsyncClient

from app.main import app


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_root() -> None:
    async with await _client() as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json()["app"] == "MailMind"


async def test_health() -> None:
    """Requires `docker compose up -d` — checks real Postgres/Redis connectivity."""
    async with await _client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "db": "ok", "redis": "ok"}
