"""Agent tools. Sync functions (LangGraph runs them in a worker thread when the
graph is invoked async). Human-gated actions call interrupt() — the graph pauses
until /actions/{id}/approve|reject resumes it."""

from datetime import datetime

from langchain_core.tools import tool
from langgraph.types import interrupt
from sqlalchemy import select

from app.agent.context import get_context
from app.db_sync import SyncSession
from app.gmail import send as gmail_send
from app.models import Email, GmailAccount, Transaction
from app.retrieval.hybrid import hybrid_search
from app.txn_extract import extract_for_email


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@tool
def search_emails(
    query: str,
    sender: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Search the user's emails semantically + by keyword.

    Args:
        query: what to look for (natural language or keywords).
        sender: optional filter — matches the From field (substring, e.g. "hdfc" or "amazon").
        date_from: optional ISO date lower bound, e.g. "2026-06-01".
        date_to: optional ISO date upper bound.

    Returns a list of matching emails with email_id, subject, sender, date, snippet, chunk.
    Cite results by email_id.
    """
    ctx = get_context()
    with SyncSession() as db:
        return hybrid_search(
            db,
            ctx.account_ids,
            query,
            sender=sender,
            date_from=_parse_dt(date_from),
            date_to=_parse_dt(date_to),
        )


@tool
def get_email(email_id: int) -> dict:
    """Read one email in full by its email_id (from search results)."""
    ctx = get_context()
    with SyncSession() as db:
        email = db.get(Email, email_id)
        if email is None or email.account_id not in ctx.account_ids:
            return {"error": f"email {email_id} not found"}
        return {
            "email_id": email.id,
            "gmail_id": email.gmail_id,
            "subject": email.subject,
            "sender": email.sender,
            "recipients": email.recipients,
            "date": email.internal_date.isoformat() if email.internal_date else None,
            "body": (email.body_text or email.snippet or "")[:12000],
            "labels": email.labels,
        }


@tool
def list_transactions(
    date_from: str | None = None,
    date_to: str | None = None,
    merchant: str | None = None,
) -> list[dict]:
    """List already-extracted financial transactions (amount, merchant, reference no).

    Args:
        date_from/date_to: optional ISO date bounds.
        merchant: optional merchant name filter (substring).
    """
    ctx = get_context()
    with SyncSession() as db:
        q = (
            select(Transaction, Email.subject, Email.sender)
            .join(Email, Email.id == Transaction.email_id)
            .where(Email.account_id.in_(ctx.account_ids))
            .order_by(Transaction.txn_date.desc())
            .limit(100)
        )
        if df := _parse_dt(date_from):
            q = q.where(Transaction.txn_date >= df)
        if dt := _parse_dt(date_to):
            q = q.where(Transaction.txn_date <= dt)
        if merchant:
            q = q.where(Transaction.merchant.ilike(f"%{merchant}%"))
        rows = db.execute(q).all()
        return [
            {
                "email_id": t.email_id,
                "date": t.txn_date.isoformat() if t.txn_date else None,
                "amount": float(t.amount) if t.amount is not None else None,
                "currency": t.currency,
                "merchant": t.merchant,
                "reference_no": t.reference_no,
                "type": t.txn_type,
                "confidence": t.confidence,
            }
            for t, _subj, _sender in rows
        ]


@tool
def extract_transactions(email_ids: list[int]) -> list[dict]:
    """Extract transaction details (amount, merchant, UTR/reference) from specific emails
    that aren't in the transactions list yet. Pass email_ids from search results."""
    ctx = get_context()
    out = []
    with SyncSession() as db:
        for eid in email_ids[:10]:
            email = db.get(Email, eid)
            if email is None or email.account_id not in ctx.account_ids:
                continue
            txn = extract_for_email(db, email)
            if txn:
                out.append(
                    {
                        "email_id": eid,
                        "amount": float(txn.amount) if txn.amount is not None else None,
                        "currency": txn.currency,
                        "merchant": txn.merchant,
                        "reference_no": txn.reference_no,
                        "type": txn.txn_type,
                    }
                )
    return out


@tool
def draft_email(to: str, subject: str, body: str) -> dict:
    """Draft a new email for the user to review. Does NOT send anything."""
    return {
        "draft": {"to": to, "subject": subject, "body": body},
        "note": "Draft only — use send_email to request sending (requires user approval).",
    }


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send a new email. PAUSES for explicit user approval before anything is sent."""
    decision = interrupt(
        {"action_type": "send_email", "payload": {"to": to, "subject": subject, "body": body}}
    )
    if not decision.get("approved"):
        return f"User REJECTED the send. Reason: {decision.get('reason', 'not given')}"
    ctx = get_context()
    with SyncSession() as db:
        account = db.get(GmailAccount, ctx.primary_account_id)
        sent_id = gmail_send.send_email(account, to, subject, body)
    return f"Email sent successfully (gmail id {sent_id})."


@tool
def forward_emails(email_ids: list[int], to: str, note: str = "") -> str:
    """Forward one or more existing emails (by email_id) to a recipient.
    PAUSES for explicit user approval before anything is sent."""
    decision = interrupt(
        {
            "action_type": "forward_emails",
            "payload": {"email_ids": email_ids, "to": to, "note": note},
        }
    )
    if not decision.get("approved"):
        return f"User REJECTED the forward. Reason: {decision.get('reason', 'not given')}"
    ctx = get_context()
    sent = []
    with SyncSession() as db:
        account = db.get(GmailAccount, ctx.primary_account_id)
        for eid in email_ids[:10]:
            email = db.get(Email, eid)
            if email is None or email.account_id not in ctx.account_ids:
                continue
            sent.append(gmail_send.forward_email(db, account, eid, to, note))
    return f"Forwarded {len(sent)} email(s) to {to}."


ALL_TOOLS = [
    search_emails,
    get_email,
    list_transactions,
    extract_transactions,
    draft_email,
    send_email,
    forward_emails,
]
