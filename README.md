# MailMind 📬🤖

> An AI agent that turns your Gmail inbox into a queryable database — chat with your emails, extract transactions into structured data, and let the agent draft/forward emails with human-in-the-loop approval.

## Stack

| Layer | Tech |
|---|---|
| API | FastAPI (Python 3.12, uv) |
| Agent | LangChain + LangGraph *(Phase 2)* |
| LLM / Embeddings | Gemini *(Phase 1+)* |
| Storage | Postgres + pgvector |
| Queue | Celery + Redis *(Phase 1)* |
| Frontend | Next.js *(Phase 2)* |
| Observability | LangSmith *(Phase 2)* |

Full design doc: [`docs/SPEC.md`](docs/SPEC.md)

## Quickstart

```bash
# 1. Infra (Postgres + pgvector, Redis)
docker compose up -d

# 2. API
cd apps/api
cp ../../.env.example .env        # then fill in keys as phases require
uv sync                            # creates .venv with Python 3.12
uv run uvicorn app.main:app --reload

# 3. Check
curl localhost:8000/health         # {"status":"ok","db":"ok","redis":"ok"}
open http://localhost:8000/docs    # Swagger UI
```

## Development

```bash
cd apps/api
uv run pytest          # tests (needs docker compose up)
uv run ruff check .    # lint
uv run ruff format .   # format
```

## Status

- [x] Phase 0 — scaffold: FastAPI + Docker infra + health checks
- [ ] Phase 1 — Google OAuth + Gmail incremental sync + embeddings
- [ ] Phase 2 — LangGraph agent + hybrid search + chat UI
- [ ] Phase 3 — transaction extraction
- [ ] Phase 4 — send/forward with human approval
- [ ] Phase 5 — evals + deploy
