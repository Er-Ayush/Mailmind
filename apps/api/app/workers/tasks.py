import logging

from sqlalchemy import select

from app.db_sync import SyncSession
from app.embeddings import embed_texts
from app.gmail.parser import chunk_text
from app.gmail.sync import sync_account
from app.models import Email, EmailChunk, GmailAccount
from app.workers.celery_app import celery

logger = logging.getLogger(__name__)

EMBED_EMAILS_PER_RUN = 200  # keep each task bounded; beat re-runs every 2 min


@celery.task(name="app.workers.tasks.sync_all_accounts")
def sync_all_accounts() -> dict:
    """Beat task: incremental (or first) sync for every active account."""
    results = {}
    with SyncSession() as db:
        accounts = db.execute(
            select(GmailAccount).where(GmailAccount.status == "active")
        ).scalars().all()
        for account in accounts:
            try:
                results[account.email] = sync_account(db, account)
            except Exception:
                logger.exception("sync failed for %s", account.email)
                results[account.email] = "error"
    # chain: embed whatever the sync brought in
    embed_pending.delay()
    return results


@celery.task(name="app.workers.tasks.sync_one_account")
def sync_one_account(account_id: int) -> int:
    with SyncSession() as db:
        account = db.get(GmailAccount, account_id)
        if account is None:
            return 0
        inserted = sync_account(db, account)
    embed_pending.delay()
    return inserted


@celery.task(name="app.workers.tasks.embed_pending")
def embed_pending() -> int:
    """Chunk + embed emails that haven't been embedded yet. Idempotent and resumable.

    Chunks from MANY emails are packed into each API request (free-tier daily
    request caps make per-email requests untenable: 827 emails must not mean
    827 requests). Commits after every API batch so progress survives 429s.
    """
    from app.config import get_settings

    batch_size = get_settings().embed_batch_size
    embedded_count = 0
    with SyncSession() as db:
        emails = (
            db.execute(
                select(Email)
                .where(Email.embedded.is_(False))
                .order_by(Email.internal_date.desc())
                .limit(EMBED_EMAILS_PER_RUN)
            )
            .scalars()
            .all()
        )

        # 1. chunk everything first (cheap, local)
        pending: list[tuple[Email, int, str]] = []  # (email, chunk_index, content)
        for email in emails:
            base = f"Subject: {email.subject or ''}\nFrom: {email.sender or ''}\n\n"
            chunks = chunk_text(base + (email.body_text or email.snippet or ""))
            if not chunks:
                email.embedded = True
                embedded_count += 1
                continue
            pending.extend((email, idx, content) for idx, content in enumerate(chunks))
        db.commit()

        # 2. embed in cross-email batches: one API request per `batch_size` chunks
        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            vectors = embed_texts([content for _, _, content in batch])
            touched: set[int] = set()
            for (email, idx, content), vec in zip(batch, vectors, strict=True):
                db.add(
                    EmailChunk(email_id=email.id, chunk_index=idx, content=content, embedding=vec)
                )
                touched.add(email.id)
            # an email is done when its last chunk is in this or an earlier batch
            done_ids = {
                e.id for e, _, _ in batch
            } - {e.id for e, _, _ in pending[i + batch_size :]}
            for email, _, _ in batch:
                if email.id in done_ids and not email.embedded:
                    email.embedded = True
                    embedded_count += 1
            db.commit()  # commit per API batch — resumable if rate-limited mid-run
    logger.info("embed_pending embedded=%d", embedded_count)
    return embedded_count


@celery.task(name="app.workers.tasks.extract_transactions_task")
def extract_transactions_task(account_ids: list[int]) -> int:
    """Extract transactions from recent transactional-looking emails."""
    from app.txn_extract import extract_pending

    with SyncSession() as db:
        return extract_pending(db, account_ids)
