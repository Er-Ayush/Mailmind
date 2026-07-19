from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Email, EmailChunk, GmailAccount, User
from app.security import current_user
from app.workers.tasks import sync_one_account

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/trigger")
async def trigger_sync(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    accounts = (
        (await db.execute(select(GmailAccount).where(GmailAccount.user_id == user.id)))
        .scalars()
        .all()
    )
    task_ids = {}
    for account in accounts:
        result = sync_one_account.delay(account.id)
        task_ids[account.email] = result.id
    return {"queued": task_ids}


@router.get("/status")
async def sync_status(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    accounts = (
        (await db.execute(select(GmailAccount).where(GmailAccount.user_id == user.id)))
        .scalars()
        .all()
    )
    account_ids = [a.id for a in accounts]
    total = (
        await db.execute(select(func.count(Email.id)).where(Email.account_id.in_(account_ids)))
    ).scalar()
    embedded = (
        await db.execute(
            select(func.count(Email.id)).where(
                Email.account_id.in_(account_ids), Email.embedded.is_(True)
            )
        )
    ).scalar()
    chunks = (
        await db.execute(
            select(func.count(EmailChunk.id))
            .join(Email)
            .where(Email.account_id.in_(account_ids))
        )
    ).scalar()
    return {
        "accounts": [
            {
                "email": a.email,
                "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
                "history_id": a.history_id,
            }
            for a in accounts
        ],
        "emails_total": total,
        "emails_embedded": embedded,
        "chunks": chunks,
    }
