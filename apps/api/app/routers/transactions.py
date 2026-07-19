import csv
import io

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Email, GmailAccount, Transaction, User
from app.security import current_user
from app.workers.tasks import extract_transactions_task

router = APIRouter(prefix="/transactions", tags=["transactions"])


async def _rows(user: User, db: AsyncSession) -> list[dict]:
    account_ids = (
        (await db.execute(select(GmailAccount.id).where(GmailAccount.user_id == user.id)))
        .scalars()
        .all()
    )
    rows = (
        await db.execute(
            select(Transaction, Email.subject, Email.sender, Email.gmail_id)
            .join(Email, Email.id == Transaction.email_id)
            .where(Email.account_id.in_(account_ids))
            .order_by(Transaction.txn_date.desc())
        )
    ).all()
    return [
        {
            "id": t.id,
            "email_id": t.email_id,
            "gmail_id": gmail_id,
            "date": t.txn_date.isoformat() if t.txn_date else None,
            "amount": float(t.amount) if t.amount is not None else None,
            "currency": t.currency,
            "merchant": t.merchant,
            "reference_no": t.reference_no,
            "type": t.txn_type,
            "account_hint": t.account_hint,
            "confidence": t.confidence,
            "email_subject": subject,
            "email_sender": sender,
        }
        for t, subject, sender, gmail_id in rows
    ]


@router.get("")
async def list_txns(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    return await _rows(user, db)


@router.post("/extract")
async def trigger_extract(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    account_ids = (
        (await db.execute(select(GmailAccount.id).where(GmailAccount.user_id == user.id)))
        .scalars()
        .all()
    )
    task = extract_transactions_task.delay(list(account_ids))
    return {"queued": task.id}


@router.get("/export.csv")
async def export_csv(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    rows = await _rows(user, db)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "date", "amount", "currency", "merchant", "reference_no",
            "type", "account_hint", "confidence", "email_subject", "email_sender",
        ],
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=mailmind-transactions.csv"},
    )
