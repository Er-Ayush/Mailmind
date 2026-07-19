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
    """Chunk + embed emails that haven't been embedded yet. Idempotent and resumable."""
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
        for email in emails:
            # subject gets prepended so it's searchable within every chunk
            base = f"Subject: {email.subject or ''}\nFrom: {email.sender or ''}\n\n"
            chunks = chunk_text(base + (email.body_text or email.snippet or ""))
            if not chunks:
                email.embedded = True
                continue
            vectors = embed_texts(chunks)
            for idx, (content, vec) in enumerate(zip(chunks, vectors, strict=True)):
                db.add(
                    EmailChunk(email_id=email.id, chunk_index=idx, content=content, embedding=vec)
                )
            email.embedded = True
            embedded_count += 1
            db.commit()  # commit per email — resumable if rate-limited mid-run
    logger.info("embed_pending embedded=%d", embedded_count)
    return embedded_count
