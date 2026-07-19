import logging
from datetime import UTC, datetime

from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.gmail.client import gmail_service
from app.gmail.parser import parse_message
from app.models import Email, GmailAccount

logger = logging.getLogger(__name__)

FETCH_FORMAT = "full"


def _store_message(db: Session, account: GmailAccount, msg: dict) -> bool:
    """Insert a parsed message if unseen. Returns True if inserted (idempotent by gmail_id)."""
    data = parse_message(msg)
    exists = db.execute(select(Email.id).where(Email.gmail_id == data["gmail_id"])).scalar()
    if exists:
        return False
    db.add(Email(account_id=account.id, **data))
    return True


def initial_sync(db: Session, account: GmailAccount) -> int:
    """First sync: pull the last SYNC_DAYS of mail, newest first."""
    service = gmail_service(account)
    query = f"newer_than:{get_settings().sync_days}d"
    inserted = 0
    page_token = None

    while True:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=100, pageToken=page_token)
            .execute()
        )
        ids = [m["id"] for m in resp.get("messages", [])]
        for mid in ids:
            msg = service.users().messages().get(userId="me", id=mid, format=FETCH_FORMAT).execute()
            if _store_message(db, account, msg):
                inserted += 1
        db.commit()
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    # Baseline historyId for future incremental syncs
    profile = service.users().getProfile(userId="me").execute()
    account.history_id = str(profile["historyId"])
    account.last_synced_at = datetime.now(UTC)
    db.commit()
    logger.info("initial_sync account=%s inserted=%d", account.email, inserted)
    return inserted


def incremental_sync(db: Session, account: GmailAccount) -> int:
    """Follow-up sync via history.list since the stored historyId."""
    service = gmail_service(account)
    inserted = 0
    page_token = None
    latest_history_id = account.history_id

    try:
        while True:
            resp = (
                service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=account.history_id,
                    historyTypes="messageAdded",
                    pageToken=page_token,
                )
                .execute()
            )
            for h in resp.get("history", []):
                latest_history_id = str(h["id"])
                for added in h.get("messagesAdded", []):
                    mid = added["message"]["id"]
                    try:
                        msg = (
                            service.users()
                            .messages()
                            .get(userId="me", id=mid, format=FETCH_FORMAT)
                            .execute()
                        )
                    except HttpError as e:
                        if e.resp.status == 404:  # deleted before we fetched it
                            continue
                        raise
                    if _store_message(db, account, msg):
                        inserted += 1
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        if e.resp.status == 404:
            # historyId too old — Gmail expired it; fall back to a fresh window sync
            logger.warning("historyId expired for %s; falling back to initial_sync", account.email)
            return initial_sync(db, account)
        raise

    account.history_id = latest_history_id
    account.last_synced_at = datetime.now(UTC)
    db.commit()
    if inserted:
        logger.info("incremental_sync account=%s inserted=%d", account.email, inserted)
    return inserted


def sync_account(db: Session, account: GmailAccount) -> int:
    if account.history_id is None:
        return initial_sync(db, account)
    return incremental_sync(db, account)
