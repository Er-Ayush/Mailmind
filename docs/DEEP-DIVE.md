# MailMind — Project Deep Dive & Tech Stack Guide

> A complete explanation of how MailMind works, every technology in it, why it was chosen,
> and how the pieces fit together. Written to be read top-to-bottom as a learning document —
> and as interview preparation.

---

## 1. What MailMind Is

MailMind connects to a user's Gmail account and turns the inbox into something you can
**talk to**:

- *"When did my Amazon order arrive?"* → searches emails semantically, answers with citations
- *"List my transactions this month"* → structured table extracted from bank/UPI/receipt emails
- *"Forward my latest invoice to my CA"* → the agent drafts the action, **pauses**, and only
  executes after the human clicks Approve

Three ideas make it more than a chatbot:

1. **Agent with tools** — the LLM doesn't answer from memory; it calls functions
   (search, read, extract, send) and grounds every answer in their results.
2. **Hybrid retrieval** — combines SQL filtering, vector similarity, and keyword search,
   because each catches what the others miss.
3. **Human-in-the-loop (HITL)** — the agent can *propose* outbound actions but can never
   *execute* them without explicit approval. Safety is enforced by the framework, not by
   prompt-begging.

---

## 2. The 30-Second Architecture

```
Browser (Next.js :3001)
   │  SSE stream (tokens, citations, approval requests)
   ▼
FastAPI (:8000) ──── LangGraph agent (Gemini) ──── 7 tools
   │                        │ interrupt() on send-type tools
   │                        ▼
   │                 agent_actions table → /approve → graph resumes → Gmail send
   ▼
Postgres 16 + pgvector  ◄──── Celery worker (sync every 5 min, embed every 2 min)
Redis (broker + locks)  ◄──── Celery beat (scheduler)         ▲
                                                              │ Gmail API (historyId delta)
                                                              ▼
                                                         Google OAuth (PKCE)
```

**Data flow, end to end:**

1. User clicks *Connect Google* → OAuth → we store an **encrypted refresh token**
2. Celery pulls the last 30 days of email → parses → stores rows in `emails`
3. Email bodies are chunked → embedded via Gemini → vectors stored in `email_chunks`
4. User asks a question → LangGraph agent picks tools → hybrid search → cited answer streams back token-by-token
5. If the agent wants to send/forward → `interrupt()` freezes the graph mid-execution →
   UI shows an approval card → approve resumes the graph exactly where it stopped

---

## 3. Tech Stack, One by One

### 3.1 Python 3.12 + uv (package manager)

**What it is:** `uv` is the modern Python package/environment manager (from Astral, the
ruff people). It replaces `pip` + `virtualenv` + `pyenv` in one Rust-fast tool.

**Why chosen:** 10–100× faster than pip, lockfile-based reproducible installs
(`uv.lock`), and it auto-downloads the pinned Python version (`.python-version` says 3.12
— anyone cloning gets the same interpreter).

**In this project:** `apps/api/pyproject.toml` declares dependencies; `uv sync` builds
`.venv`; `uv run <cmd>` executes inside it without activating anything.

**NestJS analogy:** `pyproject.toml` = `package.json`, `uv.lock` = `pnpm-lock.yaml`.

> Why 3.12 and not 3.14? The AI ecosystem (langchain, psycopg wheels) lags the newest
> interpreter. 3.12 is the current "everything works" version — a real-world lesson in
> choosing boring versions for app code.

### 3.2 FastAPI (web framework)

**What it is:** Python's async-first web framework; the de-facto standard for AI/LLM
backends.

**Core concepts used here:**

| Concept | Where | NestJS equivalent |
|---|---|---|
| Router modules (`APIRouter`) | `app/routers/*.py` | Controllers |
| Dependency injection (`Depends`) | `get_db`, `current_user` | Providers/Guards |
| Pydantic request/response models | `MessageIn`, etc. | class-validator DTOs |
| Lifespan context manager | `app/main.py` | `onModuleInit/Destroy` |
| `StreamingResponse` | chat SSE endpoint | `res.write()` streams |
| Auto OpenAPI docs | `/docs` | `@nestjs/swagger` |

**The DI pattern to internalize** (`app/security.py`):

```python
async def current_user(db=Depends(get_db), mailmind_session=Cookie(None)) -> User: ...
# any endpoint that declares `user: User = Depends(current_user)` is protected
```

FastAPI resolves the dependency tree per request — `current_user` needs `get_db`, which
yields a DB session scoped to that request. Same mental model as Nest's request-scoped
providers, but declared in function signatures.

### 3.3 Pydantic & pydantic-settings

**What it is:** Runtime data validation from type hints. The backbone of FastAPI and of
LLM structured outputs.

**Three distinct jobs in this project:**

1. **API validation** — `MessageIn(BaseModel)` rejects malformed request bodies with 422s
2. **Configuration** — `Settings(BaseSettings)` in `app/config.py` reads `.env`/env vars,
   validates types, provides defaults. `DATABASE_URL` env var → `settings.database_url`.
3. **LLM structured output** — `ExtractedTxn` in `app/txn_extract.py`: we hand the schema
   to Gemini and it must return JSON matching it. Pydantic validates the response; the
   LLM literally cannot return a malformed transaction.

That third use is the modern LLM-engineering pattern to talk about in interviews:
**schemas as the contract between deterministic code and probabilistic models.**

### 3.4 PostgreSQL 16 + pgvector + Full-Text Search

**What it is:** One database doing three jobs — relational store, vector database, and
keyword search engine.

**Why one DB instead of Postgres + Pinecone/Qdrant:** fewer moving parts, transactional
consistency between emails and their vectors (delete an email → chunks cascade), and
`JOIN`s between metadata and vectors in a single query. "Just use Postgres" is the
industry's current default for this scale.

**The three index types on our schema** (see the initial Alembic migration):

```sql
-- 1. B-tree: exact-match / range scans (classic)
CREATE INDEX ix_emails_account_date ON emails (account_id, internal_date DESC);

-- 2. HNSW: approximate nearest-neighbor for vectors (pgvector)
CREATE INDEX ... ON email_chunks USING hnsw (embedding vector_cosine_ops);

-- 3. GIN: inverted index for full-text search
CREATE INDEX ix_emails_fts ON emails USING gin (to_tsvector('english', subject || body));
```

**HNSW in one paragraph:** exact nearest-neighbor over N vectors is O(N) per query.
HNSW (Hierarchical Navigable Small World) builds a multi-layer graph where upper layers
are sparse "highways" and lower layers are dense "streets"; search descends greedily and
finds *approximate* neighbors in roughly O(log N). You trade a little recall for a lot of
speed — the standard trade in production vector search.

**Cosine distance:** `embedding <=> query_vector` is pgvector's cosine-distance operator.
`1 - distance = similarity`. Cosine is scale-invariant, which is why we don't need to
normalize vectors.

### 3.5 SQLAlchemy 2.0 + Alembic

**SQLAlchemy** is Python's ORM. We use the modern 2.0 style — `Mapped[int]`,
`mapped_column()` — in `app/models.py`, with **two engines**:

- **async engine** (`asyncpg` driver) for FastAPI request handlers (`app/db.py`)
- **sync engine** (`psycopg` driver) for Celery workers (`app/db_sync.py`) — Celery tasks
  are synchronous by nature

That split is a real architectural decision worth explaining in interviews: *the web tier
is async for concurrency; the worker tier is sync for simplicity — same models, two
session factories.*

**Alembic** is migrations (NestJS: TypeORM migrations). `alembic revision --autogenerate`
diffs models vs database; `alembic upgrade head` applies. We hand-edited the autogenerated
migration to add the HNSW and GIN indexes — autogenerate doesn't know about expression
indexes, a classic real-world gap.

### 3.6 Redis + Celery (background jobs)

**Redis** plays three roles here: Celery's message **broker** (task queue), Celery's
**result backend**, and a **distributed lock** store.

**Celery** is Python's standard task-queue framework (NestJS analogy: Bull/BullMQ).

**The moving parts:**

| Process | Command | Job |
|---|---|---|
| Worker | `celery -A app.workers.celery_app worker` | executes tasks from the queue |
| Beat | `celery -A app.workers.celery_app beat` | cron scheduler — enqueues `sync_all_accounts` every 5 min, `embed_pending` every 2 min |

**Why a queue at all?** Syncing 827 emails takes minutes and embedding them takes ~40
minutes on free-tier rate limits. That cannot happen inside an HTTP request. The API
enqueues (`sync_one_account.delay(id)`) and returns instantly; workers grind in the
background; the UI polls `/sync/status`.

**The distributed lock (a real bug we hit):** beat, the post-sync chain, and a manual
trigger all enqueued `embed_pending` simultaneously → two workers embedded the same
emails → duplicate-key crashes. Fix in `app/workers/tasks.py`:

```python
lock = redis.lock("mailmind:embed_pending", timeout=3600, blocking=False)
if not lock.acquire(blocking=False):
    return 0   # someone else is embedding; skip
```

Single-flight execution via Redis `SET NX`. This exact pattern (idempotent workers +
distributed locks) is a staple system-design interview topic.

### 3.7 Google OAuth 2.0 + Gmail API

**OAuth flow used:** *authorization code flow with PKCE* for a web server app.

1. `/auth/google/login` → redirect to Google's consent page with our client_id, requested
   scopes (`gmail.readonly`, `gmail.send`), and a PKCE **code challenge**
2. User consents → Google redirects to `/auth/google/callback?code=...`
3. We exchange the code (+ PKCE **code verifier**) for tokens
4. The **refresh token** (long-lived) is what we keep — encrypted — to mint short-lived
   access tokens forever after

**PKCE bug we hit live:** login and callback are two separate HTTP requests, but the code
verifier generated at step 1 must be presented at step 3. Our stateless design lost it →
`Missing code verifier`. Fix: carry it across in a short-lived httponly cookie. (Study
`app/routers/auth.py` — this is the best OAuth learning material in the repo.)

**Gmail API — incremental sync** (`app/gmail/sync.py`):

- **First sync:** `messages.list(q="newer_than:30d")` → page through → fetch each message
- **Every sync after:** `history.list(startHistoryId=...)` returns *only what changed*
  since last time — the delta-sync pattern used by every serious mail client
- **Idempotency:** unique constraint on `gmail_id` + check-before-insert means re-running
  a sync can never duplicate

**MIME parsing** (`app/gmail/parser.py`): emails are trees of parts (text/plain,
text/html, attachments). We walk the tree, prefer plain text, fall back to
BeautifulSoup-stripped HTML.

### 3.8 Cryptography: Fernet & itsdangerous

Two small but interview-worthy security choices in `app/security.py`:

- **Fernet (symmetric encryption)** — refresh tokens are encrypted *at rest* in Postgres.
  A DB dump alone cannot impersonate users; the attacker also needs the `FERNET_KEY` from
  the environment. (Defense in depth.)
- **itsdangerous (signed cookies)** — the session cookie is `{"uid": 1}` **signed** with a
  secret. Users can read it but cannot forge it — any tampering breaks the signature.
  Signing ≠ encryption: we don't care if the user sees their own uid; we care that they
  can't claim someone else's.

### 3.9 Gemini (LLM + embeddings)

Two different model types, doing different jobs:

**`gemini-flash-latest` (chat model)** — powers the agent: reasoning, tool selection,
answer composition, and structured extraction. Temperature 0.2 for chat, 0 for extraction
(determinism matters more than creativity when parsing invoices).

**`gemini-embedding-2` (embedding model)** — turns text into 768-dim vectors where
semantic similarity ≈ geometric closeness. "Flight booking confirmation" and "your ticket
is attached" land near each other despite sharing no keywords. That's what makes semantic
search work.

**Free-tier rate-limit engineering (we hit all of these live):**

| Limit type | What happened | The fix |
|---|---|---|
| Requests/day per model | Per-email embedding = 827 requests; cap is 1,000 | Batch chunks *across* emails: ~50 requests total |
| Tokens/minute | 64-chunk batches ≈ 24k tokens grazed the 30k TPM cap | 32-chunk batches + 26s pacing |
| Requests/minute bursts | Concurrent tasks hammering simultaneously | Redis lock + exponential backoff (5s → 80s) |
| Model lifecycle | `text-embedding-004` retired; `gemini-2.5-flash` closed to new keys | Model-listing probe; prefer `-latest` aliases |

Also note: **quotas are per-model** — switching embedding models bought us a fresh daily
budget mid-incident. Real production incident-response thinking.

### 3.10 LangChain + LangGraph (the agent)

**LangChain** provides the model abstractions (`ChatGoogleGenerativeAI`), the `@tool`
decorator (docstring + type hints → the JSON schema the LLM sees), and structured-output
binding.

**LangGraph** runs the agent as a **state machine**:

```
        ┌──────────┐   tool_calls?   ┌─────────┐
  ───►  │  agent   │ ──────────────► │  tools  │
        │ (Gemini) │ ◄────────────── │         │
        └──────────┘   results       └─────────┘
             │ no tool calls                │ send-type tool
             ▼                              ▼
          final answer               interrupt() ⏸ → human → resume
```

This is the **ReAct loop** (Reason → Act → observe → repeat), built by
`create_react_agent` in `app/agent/graph.py`.

**The 8 tools** (`app/agent/tools.py`): `search_emails` (hybrid search),
`list_recent_emails` (pure SQL — recency isn't semantic!), `get_email`,
`list_transactions`, `extract_transactions`, `draft_email`, `send_email` ⏸,
`forward_emails` ⏸.

**The two concepts that make LangGraph worth its name:**

1. **Checkpointer** (`AsyncPostgresSaver`) — after every step, the entire graph state is
   persisted to Postgres keyed by `thread_id`. Conversations survive restarts, and…
2. **`interrupt()`** — inside `send_email`, the tool calls `interrupt({payload})`. The
   graph *freezes mid-tool-execution* and persists. Minutes later,
   `graph.ainvoke(Command(resume={"approved": True}), thread_id)` resumes **inside that
   same tool call**, which then actually sends. The approval gate is structural — no
   prompt engineering can bypass it.

**Context passing:** tools have fixed signatures, so per-request identity (user id,
account ids) travels via a Python `ContextVar` (`app/agent/context.py`) — the same
pattern as Node's `AsyncLocalStorage`.

### 3.11 Hybrid Retrieval (the search engine)

`app/retrieval/hybrid.py` — the intellectual core of the project.

**Why not pure vector search?** "Emails from HDFC in June" — *HDFC* is a sender filter and
*June* is a date range; neither is semantic. Vectors alone rank a July email about ICICI
"pretty close." Each retrieval mode has blind spots:

| Mode | Great at | Blind to |
|---|---|---|
| SQL filters | exact metadata (sender, dates) | meaning |
| Vector search | paraphrase, concepts | exact IDs, names, dates |
| Full-text (BM25-ish) | exact keywords, reference numbers | synonyms |

**Our pipeline:** SQL `WHERE` narrows the candidate set → one query computes both cosine
similarity and `ts_rank` → blended score `0.7 * vector + 0.3 * fts` → dedupe chunks to the
best hit per email → top-k with metadata for citations.

**Filter-then-search** beats search-then-filter: filtering first means the vector
comparison only runs over rows that can possibly qualify.

### 3.12 Server-Sent Events (streaming)

Chat responses stream token-by-token over **SSE** — a one-way HTTP stream
(`text/event-stream`) where the server keeps the connection open and writes:

```
event: token
data: {"text": "Your"}

event: citations
data: {"results": [...]}

event: action_required
data: {"action_id": 7, ...}
```

**Why SSE over WebSockets:** chat streaming is one-directional (server → client); SSE is
plain HTTP — no protocol upgrade, works through proxies, simpler auth. WebSockets earn
their complexity only for bidirectional traffic.

**Wrinkle worth knowing:** the browser's native `EventSource` only supports GET. Our chat
POST returns a stream, so the frontend parses it manually via `fetch` + `ReadableStream`
(`apps/web/lib/api.ts` — a hand-written SSE parser, ~30 lines).

### 3.13 Next.js 16 + Tailwind 4 (frontend)

App Router, all three pages are client components (`"use client"`) since everything is
per-user interactive data. Key pieces:

- `lib/api.ts` — typed API client; every call sends `credentials: "include"` so the
  session cookie rides along (CORS on the API side has `allow_credentials=True`)
- `components/Chat.tsx` — the streaming chat: appends tokens as they arrive, renders tool
  chips, citation links (deep-link to the Gmail message via `#all/<gmail_id>`), and the
  amber approval card with Approve/Reject buttons
- **Tailwind** utility classes throughout — no CSS files, design lives in the markup

### 3.14 Docker Compose (infrastructure)

`docker-compose.yml` runs the stateful services — `pgvector/pgvector:pg16` (Postgres with
the extension pre-installed) and `redis:7-alpine` — with healthchecks and a persistent
volume. The app processes run on the host for fast reload during development. Postgres
maps to host port **5433** because 5432 was taken — ports are config, not constants.

---

## 4. Request Walkthroughs (trace these in the code)

### A. "Find my flight booking" (read path)

1. `Chat.tsx` POSTs to `/chat/sessions/{id}/messages`; cookie authenticates via `current_user`
2. `chat.py` stores the user message, sets `AgentContext` (ContextVar), starts
   `graph.astream(...)` with `thread_id=session-{id}`
3. Gemini reads the system prompt + message → emits a `search_emails` tool call
4. The tool runs hybrid search → returns 8 results with ids/subjects/chunks
5. Gemini composes an answer citing `[email_id]`s → tokens stream out as SSE `token`
   events; the tool result also streamed earlier as a `citations` event
6. Final text is persisted to `chat_messages`; checkpointer saved every step

### B. "Forward it to my CA" (write path — the HITL showcase)

1. Same entry; Gemini calls `forward_emails(email_ids=[42], to="ca@example.com")`
2. The tool calls `interrupt({action_type, payload})` → **graph freezes**, state persists
3. `chat.py` catches `__interrupt__`, writes an `agent_actions` row
   (`status=pending_approval`), emits SSE `action_required`, closes the stream
4. UI renders the approval card. Time passes — the graph doesn't care; it's checkpointed.
5. Approve → `POST /actions/7/approve` → `actions.py` rebuilds context, calls
   `graph.ainvoke(Command(resume={"approved": True}), same thread_id)`
6. Execution resumes *inside* `forward_emails` — `interrupt()` returns the resume value —
   the Gmail send happens, audit row flips to `sent`, the agent's confirmation is saved

### C. Background sync (no user involved)

Beat (5 min) → `sync_all_accounts` → per account: `history.list` since stored
`historyId` → new messages parsed & inserted → chains `embed_pending` → Redis lock →
chunk + batch-embed → vectors committed per batch → `/sync/status` counters climb.

---

## 5. Production Bugs We Hit (and what they teach)

| # | Bug | Root cause | Fix | Lesson |
|---|---|---|---|---|
| 1 | `Missing code verifier` on OAuth callback | PKCE verifier generated at login wasn't available in the (stateless) callback request | Short-lived httponly cookie carries it | OAuth flows are multi-request state machines |
| 2 | Embedding quota exhausted in minutes | One API request **per email** (827 requests vs 1,000/day cap) | Batch chunks across emails → ~50 requests | Batch at the API-call level, not the domain-object level |
| 3 | `duplicate key … email_chunks` crashes | Three trigger paths ran `embed_pending` concurrently | Redis `SET NX` lock (single-flight) + stray-chunk cleanup | Any task with multiple triggers needs idempotency or mutual exclusion |
| 4 | Retired/closed models (`text-embedding-004`, `gemini-2.5-flash`) | Provider model lifecycle | Probe `ListModels`, use `-latest` aliases, per-model quota awareness | Treat model IDs as config; expect churn |
| 5 | Agent searched for the literal word "the" | "Most recent emails" isn't a semantic query | New pure-SQL `list_recent_emails` tool + prompt guidance | Give agents the *right* tools; don't force semantics onto structural queries |

These five stories are interview gold: each has symptom → diagnosis → fix → generalizable
principle.

---

## 6. Glossary (quick reference)

| Term | Meaning |
|---|---|
| **Embedding** | Text → vector of floats where semantic similarity ≈ geometric closeness |
| **RAG** | Retrieval-Augmented Generation: fetch relevant data, let the LLM answer from it |
| **ReAct loop** | LLM alternates Reasoning and Acting (tool calls) until it can answer |
| **HNSW** | Graph-based approximate nearest-neighbor index (fast vector search) |
| **HITL** | Human-in-the-loop: human approval gates inside automated flows |
| **Checkpointer** | LangGraph's persistence layer — graph state saved per thread |
| **`interrupt()`** | LangGraph primitive: pause the graph mid-node, resume later with a value |
| **PKCE** | Proof Key for Code Exchange — OAuth extension binding auth code to its initiator |
| **historyId** | Gmail's cursor for delta sync ("everything that changed since X") |
| **SSE** | Server-Sent Events: one-way HTTP streaming (server → browser) |
| **Fernet** | Symmetric authenticated encryption (encrypt-at-rest for tokens) |
| **Idempotency** | Running an operation twice has the same effect as once (safe retries) |
| **Single-flight** | At most one instance of a job runs at a time (via distributed lock) |
| **TPM / RPM / RPD** | Tokens/Requests per minute/day — the three API rate-limit axes |

---

## 7. Suggested Learning Path Through the Code

1. `app/main.py` + `app/config.py` — app wiring, settings (30 min)
2. `app/models.py` + the Alembic migration — schema + the three index types (30 min)
3. `app/routers/auth.py` + `app/security.py` — OAuth, PKCE, Fernet, signed cookies (1 hr)
4. `app/gmail/sync.py` — initial vs incremental sync, idempotency (45 min)
5. `app/workers/tasks.py` + `app/embeddings.py` — Celery, the lock, rate-limit pacing (45 min)
6. `app/retrieval/hybrid.py` — read the SQL slowly; this is the heart (1 hr)
7. `app/agent/` — tools, prompt, graph, `interrupt()` (1.5 hr)
8. `app/routers/chat.py` + `apps/web/lib/api.ts` — SSE end to end (1 hr)
9. `app/routers/actions.py` — the resume flow (30 min)

Total: a weekend of focused reading to own every line in interviews.
