# MailMind — AI Email Agent (Personal Project Spec)

> **One-liner:** An AI agent that turns your Gmail inbox into a queryable database — chat with your emails, extract transactions into structured data, and let the agent draft/forward emails with human-in-the-loop approval.

**Author:** Ayush Chaurasia · **Started:** July 2026 · **Goal:** Resume-grade AI project + learn Python

---

## 1. Finalized Tech Stack

| Layer | Choice | Why (demand & rationale) |
|---|---|---|
| Backend API | **FastAPI** (Python) | Highest-demand framework for AI/ML backends; async-first, auto OpenAPI docs; closest feel to NestJS |
| Agent orchestration | **LangChain + LangGraph** | Most in-demand AI framework keywords; production use at Uber, LinkedIn, Klarna; native human-in-the-loop support |
| Vector store | **pgvector** (Postgres extension) | Industry's pragmatic default ("just use Postgres"); one DB for emails + metadata + vectors; enables hybrid search |
| LLM | **Gemini** (gemini-2.x via API) | Generous free tier (₹0 budget), 1M context for long threads, solid tool-calling |
| Embeddings | **Gemini `text-embedding-004`** | Free tier, same provider/key as LLM — one ecosystem |
| Task queue | **Celery + Redis** | THE standard Python task-queue keyword; retries, scheduling (beat), monitoring (Flower) |
| Email sync | **Gmail API — polling + `historyId`** (incremental) | Simple, reliable, no public webhook needed; upgrade path to Pub/Sub push in V2 |
| Frontend | **Next.js + Vercel AI SDK** (streaming chat) | Industry-standard AI product frontend; you already know TS → low risk, high polish |
| App auth | **Google Sign-In (OAuth)** | One flow = login + Gmail scopes (how Superhuman/Shortwave onboard); JWT session after |
| Observability/Evals | **LangSmith** | Official LangChain tracing/evals; most recognized keyword; free tier (5k traces/mo) is enough for a personal project |
| Deployment | **Docker Compose (local-first)** + optional free-tier cloud (§13) | Interviews are demoed from your laptop — 100% free, zero cold-start risk. Optional live link via Render/Neon/Upstash free tiers |

---

## 2. Architecture

```
┌─────────────┐     OAuth + REST      ┌──────────────────────────────┐
│  Next.js UI  │ ◄──────SSE──────────► │        FastAPI (api)         │
│ (Vercel)     │                       │  /auth /chat /emails /txns   │
└─────────────┘                       └────────┬─────────────────────┘
                                               │
                     ┌─────────────────────────┼───────────────────────┐
                     ▼                         ▼                       ▼
              ┌────────────┐          ┌────────────────┐      ┌──────────────┐
              │  Postgres   │          │ LangGraph agent │      │ Celery worker │
              │  + pgvector │◄────────►│  (Gemini LLM)   │      │ (sync, embed, │
              │             │          │  tools + HITL   │      │  extract)     │
              └────────────┘          └────────────────┘      └──────┬───────┘
                     ▲                                                │
                     │            Redis (broker + result backend)     │
                     └──────────────────┬─────────────────────────────┘
                                        ▼
                                ┌──────────────┐        ┌───────────┐
                                │ Celery beat   │──poll──► Gmail API │
                                │ (every 5 min) │        └───────────┘
                                └──────────────┘

              LangSmith ← traces from every agent run / LLM call
```

### Request flows

**Sync flow (background):**
1. Celery beat triggers `sync_mailbox` every 5 min per connected account
2. Worker calls Gmail `history.list` with stored `historyId` → only new/changed messages
3. New emails → parse (headers, body, attachments metadata) → store in Postgres
4. Chunk + embed body via Gemini `text-embedding-004` → store vectors in pgvector
5. If email matches transaction patterns (bank/UPI/receipt senders) → queue `extract_transaction` task

**Chat flow (foreground):**
1. User asks: *"When did my Amazon refund arrive?"*
2. FastAPI streams to LangGraph agent (SSE)
3. Agent plans → calls tools → composes answer **with citations** (links to actual emails)
4. Every step traced in LangSmith

**Action flow (human-in-the-loop):**
1. User: *"Forward all Uber receipts from June to my CA"*
2. Agent finds emails, drafts the forward → **LangGraph interrupt()** pauses the graph
3. UI shows draft + recipient → user clicks **Approve** / **Reject**
4. On approve → graph resumes → Gmail `send` API → audit log row written

---

## 3. Data Model (Postgres)

```sql
users            (id, google_sub, email, name, created_at)
gmail_accounts   (id, user_id, refresh_token_enc, history_id, last_synced_at, status)
emails           (id, account_id, gmail_id UNIQUE, thread_id, sender, recipients[],
                  subject, snippet, body_text, labels[], internal_date, has_attachments)
email_chunks     (id, email_id, chunk_index, content, embedding vector(768))
transactions     (id, email_id, txn_date, amount, currency, merchant, reference_no,
                  txn_type, account_hint, confidence, extracted_at)
agent_actions    (id, user_id, action_type, payload_json, status
                  [drafted|approved|rejected|sent], approved_at, sent_at)  -- audit log
chat_sessions    (id, user_id, title, created_at)
chat_messages    (id, session_id, role, content, tool_calls_json, created_at)
```

**Indexes that matter (interview talking points):**
- `emails(account_id, internal_date DESC)` — date-range filters
- `emails(gmail_id)` unique — idempotent sync (safe re-runs)
- `email_chunks.embedding` — **HNSW** index for fast ANN search
- GIN on `emails.labels` — label filtering

---

## 4. Retrieval Design (Hybrid Search)

Naive vector search fails on emails ("June", "from HDFC" are metadata, not semantics). So:

1. **Metadata pre-filter (SQL):** sender / date-range / labels extracted from the query by the agent → `WHERE` clause narrows candidates
2. **Semantic search (pgvector):** cosine similarity over `email_chunks.embedding` within the filtered set
3. **Keyword boost (Postgres FTS):** `tsvector` match on subject/body, blended with vector score
4. **(V2) Rerank:** cross-encoder rerank of top-20 → top-5

> This "filter-then-search" hybrid is the #1 thing that separates this from tutorial RAG projects. Benchmark it (see §8).

---

## 5. LangGraph Agent Design

**Single agent, tool-calling, with interrupt-based approval.**

```
State: { messages, user_id, pending_action }

Nodes:
  agent        → Gemini w/ bound tools (the reasoning loop)
  tools        → executes tool calls
  human_review → interrupt() — pauses for approve/reject on send-type actions

Edges:
  agent → tools → agent (loop)
  agent → human_review (when tool ∈ {send_email, forward_emails})
  human_review → tools (approved) | agent (rejected, with feedback)
```

**Tools:**
| Tool | Signature | Notes |
|---|---|---|
| `search_emails` | (query, sender?, date_from?, date_to?, labels?) → email summaries + ids | Hybrid search §4 |
| `get_email` | (email_id) → full body + thread | For deep reads |
| `extract_transactions` | (email_ids \| filters) → structured rows | Pydantic-validated output |
| `list_transactions` | (date_from?, date_to?, merchant?) → table | Reads `transactions` table |
| `draft_email` | (to, subject, body) → draft preview | Never sends |
| `send_email` / `forward_emails` | (draft_id \| email_ids, to) | **Always gated by human approval** |
| `export_csv` | (transaction filters) → download link | |

**Structured extraction:** Pydantic model `Transaction(amount, currency, txn_date, merchant, reference_no, txn_type, confidence)` + Gemini structured output mode; reject rows with `confidence < 0.7` to a review list.

---

## 6. API Surface (FastAPI)

```
POST  /auth/google/callback      # OAuth code → JWT + store refresh token (encrypted)
GET   /me
POST  /sync/trigger              # manual sync (also runs on beat schedule)
GET   /emails?query=&sender=&from=&to=      # browse/filter
POST  /chat/sessions             # create session
POST  /chat/sessions/{id}/messages   # SSE stream — agent response
POST  /actions/{id}/approve      # human-in-the-loop resolution
POST  /actions/{id}/reject
GET   /transactions?from=&to=&merchant=
GET   /transactions/export.csv
```

---

## 7. Security & Privacy (do these, mention them in interviews)

- **Minimal scopes:** start `gmail.readonly`; add `gmail.send` only when approval flow ships
- **Encrypt refresh tokens** at rest (Fernet key from env)
- **PII redaction before LLM:** regex-mask PAN/Aadhaar/account numbers in bodies sent to Gemini; keep raw only in your DB
- **Audit log:** every agent-initiated action in `agent_actions` — immutable trail
- **No auto-send ever:** `interrupt()` gate is non-negotiable
- **Secrets:** `.env` + pydantic-settings; never commit; Railway env vars in prod

---

## 8. Evals & Observability (the differentiator)

- **LangSmith tracing** on every agent run (set `LANGCHAIN_TRACING_V2=true`)
- Build a **golden dataset**: ~30 real queries against your own inbox with expected email ids
  - e.g. "flight booking in March", "last electricity bill amount", "all Swiggy orders in June"
- Metrics: **Recall@5** (right email retrieved?), answer faithfulness (LLM-as-judge via LangSmith evals)
- **Benchmark page in README:** hybrid search vs vector-only — table of scores
- Track regressions when you change chunking/prompts

---

## 9. Roadmap

### Phase 0 — Python foundations (Week 1)
- Python syntax via the project itself: type hints, dataclasses/Pydantic, `async/await`
- Set up: `uv` (modern package manager), `ruff` (lint/format), `pytest`
- FastAPI hello-world + Docker Compose (Postgres + Redis)

### Phase 1 — Sync pipeline (Week 2)
- Google OAuth (login + Gmail readonly scope)
- Gmail full sync → incremental via `historyId`; Celery worker + beat
- Emails stored, chunked, embedded into pgvector

### Phase 2 — Chat MVP (Week 3–4)
- LangGraph agent with `search_emails` + `get_email`
- Hybrid retrieval (metadata filter + vector + FTS)
- Next.js streaming chat UI with citations
- LangSmith tracing on

### Phase 3 — Transactions (Week 5)
- Extraction task + Pydantic schema + `transactions` table
- `/transactions` UI table + CSV export
- Low-confidence review queue

### Phase 4 — Actions with approval (Week 6)
- `draft_email` / `forward_emails` tools + `interrupt()` approval flow
- Approve/Reject UI, `gmail.send` scope, audit log

### Phase 5 — Polish & ship (Week 7)
- Golden-dataset evals + benchmark page
- Primary demo: `docker compose up` locally (see §13 free-cost strategy)
- Optional live link: Render (api) + Neon (Postgres) + Upstash (Redis) + Vercel (UI) — all free tiers
- README: architecture diagram, 2-min Loom demo

### V2 backlog
- Gmail **push via GCP Pub/Sub** (replace polling) — great scaling story
- Cross-encoder reranker; daily digest (Celery beat); Outlook support; multi-account

---

## 10. Repo Layout

```
mailmind/
├── apps/
│   ├── api/                  # FastAPI + LangGraph + Celery
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── auth/         # Google OAuth, JWT
│   │   │   ├── gmail/        # sync service, parser
│   │   │   ├── agent/        # LangGraph graph, tools, prompts
│   │   │   ├── retrieval/    # hybrid search
│   │   │   ├── transactions/ # extraction, models
│   │   │   ├── workers/      # celery app, tasks, beat schedule
│   │   │   └── models/       # SQLAlchemy + Pydantic schemas
│   │   ├── alembic/          # migrations
│   │   ├── tests/
│   │   └── pyproject.toml    # uv-managed
│   └── web/                  # Next.js chat UI (Vercel AI SDK)
├── docker-compose.yml        # postgres(pgvector), redis, api, worker
├── evals/                    # golden dataset + scoring scripts
└── README.md                 # diagram, demo video, benchmark table
```

---

## 11. Resume Bullets (what you'll earn)

- Built **MailMind**, an AI email agent using **FastAPI, LangGraph, and Gemini** with tool-calling (search, extract, draft, send) and **human-in-the-loop approval** for outbound actions
- Designed a **hybrid retrieval pipeline** (SQL metadata filtering + **pgvector** semantic search + Postgres FTS) over 10k+ emails, serving cited answers with Recall@5 of X% on a golden dataset
- Implemented **incremental Gmail sync** (historyId) with **Celery/Redis** workers, idempotent ingestion, and structured transaction extraction via Pydantic-validated LLM outputs
- Added **LangSmith** tracing and an eval harness benchmarking hybrid vs vector-only retrieval; deployed on Railway/Vercel with Docker

---

## 12. Python Learning Map (NestJS → FastAPI mental model)

| You know (NestJS) | You'll learn (Python) |
|---|---|
| Module / Controller / Service | Router / endpoint fn / plain service class |
| class-validator DTOs | **Pydantic** models |
| TypeORM/Prisma | **SQLAlchemy 2.0** + Alembic migrations |
| Bull queues | **Celery** tasks + beat |
| Guards / Interceptors | **Depends()** dependency injection |
| npm / package.json | **uv** / pyproject.toml |
| ESLint + Prettier | **ruff** (both in one) |
| Jest | **pytest** |

---

## 13. Cost Plan — Guaranteed ₹0 (hard requirement)

**Strategy: local-first.** The interview demo runs on your laptop with `docker compose up` — that's the primary deployment. A cloud link is a nice-to-have, added only if the free tiers cooperate.

### Everything, itemized

| Item | Choice | Cost | Notes / limits that matter |
|---|---|---|---|
| LLM | Gemini free tier | ₹0 | Rate-limited (RPM/RPD caps) — fine for demos; add retry/backoff in code |
| Embeddings | Gemini `text-embedding-004` free tier | ₹0 | Batch + throttle embeds in the Celery worker to stay under rate limits |
| Postgres + pgvector | Docker container locally | ₹0 | No limits at all on your machine |
| Redis | Docker container locally | ₹0 | — |
| API + worker | Run locally (uvicorn + celery) | ₹0 | — |
| Frontend | Next.js dev/local build | ₹0 | — |
| LangSmith | Free tier | ₹0 | 5k traces/month — plenty |
| Gmail API | Google free quota | ₹0 | 1B quota units/day — you'll never touch it |
| Google OAuth | Free | ₹0 | Keep app in "Testing" mode — your account as test user, no verification review needed |

### Optional cloud demo link (still ₹0, set up only at the end)

| Piece | Free host | Catch to know |
|---|---|---|
| Postgres + pgvector | **Neon** free tier | 0.5GB storage — plenty for one inbox |
| Redis | **Upstash** free tier | 10k commands/day — enough for demo traffic |
| FastAPI + worker | **Render** free web service | Sleeps after 15 min idle → ~50s cold start. **Warm it up 5 min before any interview** |
| Frontend | **Vercel** free | No catch |

### Demo-day checklist (put this in the README too)
1. `docker compose up` → open localhost — nothing can go down mid-interview
2. Pre-sync your inbox the night before (embeddings already in pgvector)
3. Have 5 rehearsed queries that show off: citation search, date filter, transaction table, CSV export, approval flow
4. If showing the cloud link: hit the Render URL 5 min early to wake it

### Keeping usage tiny (design choices that keep it free)
- Sync only **last 6–12 months** of email (config: `SYNC_MONTHS=6`) — smaller embed volume
- Embed once, store forever — never re-embed unchanged emails (idempotent by `gmail_id`)
- Use Gemini Flash (not Pro) for extraction — cheaper rate-limit bucket, plenty accurate
- Cache agent answers per session; don't re-run retrieval on page refresh
