"""Transaction extraction: Gemini structured output over transaction-looking emails."""

import logging
from datetime import datetime

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Email, Transaction

logger = logging.getLogger(__name__)

# Cheap pre-filter: only run the LLM on emails that look transactional
TXN_HINTS = (
    "transaction", "debited", "credited", "payment", "receipt", "invoice",
    "order", "refund", "upi", "imps", "neft", "rtgs", "emi", "statement",
    "spent", "purchase", "txn", "paid", "bill",
)


class ExtractedTxn(BaseModel):
    """One financial transaction extracted from an email. Null fields = not present."""

    is_transaction: bool = Field(description="False if the email contains no real transaction")
    txn_date: str | None = Field(None, description="ISO date of the transaction, e.g. 2026-07-19")
    amount: float | None = Field(None, description="Transaction amount, no currency symbol")
    currency: str | None = Field(None, description="ISO currency code, e.g. INR, USD")
    merchant: str | None = Field(None, description="Merchant/payee/payer name")
    reference_no: str | None = Field(None, description="UTR / order id / reference number")
    txn_type: str | None = Field(None, description="One of: debit, credit, refund, other")
    account_hint: str | None = Field(None, description="Masked account/card, e.g. XX1234")
    confidence: float = Field(0.0, description="0-1 confidence that fields are correct")


def looks_transactional(email: Email) -> bool:
    text = f"{email.subject or ''} {email.snippet or ''}".lower()
    return any(h in text for h in TXN_HINTS)


def _extractor():
    s = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=s.gemini_chat_model, google_api_key=s.gemini_api_key, temperature=0
    )
    return llm.with_structured_output(ExtractedTxn)


def extract_for_email(db: Session, email: Email, chain=None) -> Transaction | None:
    """Extract + persist a transaction for one email. Idempotent (unique email_id)."""
    existing = db.execute(
        select(Transaction.id).where(Transaction.email_id == email.id)
    ).scalar()
    if existing:
        return None

    chain = chain or _extractor()
    content = (
        f"From: {email.sender}\nDate: {email.internal_date}\nSubject: {email.subject}\n\n"
        f"{(email.body_text or email.snippet or '')[:8000]}"
    )
    try:
        result: ExtractedTxn = chain.invoke(
            "Extract the financial transaction from this email if there is one.\n\n" + content
        )
    except Exception:
        logger.exception("extraction failed for email %s", email.id)
        return None

    if not result.is_transaction:
        return None

    txn_date = None
    if result.txn_date:
        try:
            txn_date = datetime.fromisoformat(result.txn_date)
        except ValueError:
            pass

    txn = Transaction(
        email_id=email.id,
        txn_date=txn_date or email.internal_date,
        amount=result.amount,
        currency=result.currency or "INR",
        merchant=result.merchant,
        reference_no=result.reference_no,
        txn_type=result.txn_type,
        account_hint=result.account_hint,
        confidence=result.confidence,
    )
    db.add(txn)
    db.commit()
    return txn


def extract_pending(db: Session, account_ids: list[int], limit: int = 50) -> int:
    """Extract transactions for recent transactional-looking emails without one yet."""
    emails = (
        db.execute(
            select(Email)
            .outerjoin(Transaction, Transaction.email_id == Email.id)
            .where(Email.account_id.in_(account_ids), Transaction.id.is_(None))
            .order_by(Email.internal_date.desc())
            .limit(limit * 4)
        )
        .scalars()
        .all()
    )
    chain = _extractor()
    count = 0
    for email in emails:
        if not looks_transactional(email):
            continue
        if extract_for_email(db, email, chain):
            count += 1
        if count >= limit:
            break
    return count
