from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import AgentContext, set_context
from app.agent.graph import build_agent
from app.db import get_db
from app.models import AgentAction, ChatMessage, GmailAccount, User
from app.security import current_user

router = APIRouter(prefix="/actions", tags=["actions"])


class RejectIn(BaseModel):
    reason: str = ""


async def _resume(
    request: Request, user: User, db: AsyncSession, action: AgentAction, resume_value: dict
) -> str:
    accounts = (
        (await db.execute(select(GmailAccount).where(GmailAccount.user_id == user.id)))
        .scalars()
        .all()
    )
    set_context(
        AgentContext(
            user_id=user.id,
            account_ids=[a.id for a in accounts],
            primary_account_id=accounts[0].id,
            user_email=user.email,
        )
    )
    graph = build_agent(request.app.state.checkpointer, user.email)
    result = await graph.ainvoke(
        Command(resume=resume_value),
        config={"configurable": {"thread_id": action.thread_id}},
    )
    final = result["messages"][-1].content if result.get("messages") else ""
    if isinstance(final, list):  # gemini can return content parts
        final = "".join(p.get("text", "") for p in final if isinstance(p, dict))
    if action.session_id:
        db.add(ChatMessage(session_id=action.session_id, role="assistant", content=final))
        await db.commit()
    return final


@router.get("")
async def list_actions(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    actions = (
        (
            await db.execute(
                select(AgentAction)
                .where(AgentAction.user_id == user.id)
                .order_by(AgentAction.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": a.id,
            "action_type": a.action_type,
            "payload": a.payload_json,
            "status": a.status,
            "created_at": a.created_at.isoformat(),
        }
        for a in actions
    ]


@router.post("/{action_id}/approve")
async def approve(
    action_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    action = await db.get(AgentAction, action_id)
    if action is None or action.user_id != user.id:
        raise HTTPException(404, "action not found")
    if action.status != "pending_approval":
        raise HTTPException(409, f"action already {action.status}")

    final = await _resume(request, user, db, action, {"approved": True})

    action.status = "sent"
    action.resolved_at = datetime.now(UTC)
    action.sent_at = datetime.now(UTC)
    await db.commit()
    return {"status": "sent", "result": final}


@router.post("/{action_id}/reject")
async def reject(
    action_id: int,
    body: RejectIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    action = await db.get(AgentAction, action_id)
    if action is None or action.user_id != user.id:
        raise HTTPException(404, "action not found")
    if action.status != "pending_approval":
        raise HTTPException(409, f"action already {action.status}")

    final = await _resume(
        request, user, db, action, {"approved": False, "reason": body.reason}
    )

    action.status = "rejected"
    action.resolved_at = datetime.now(UTC)
    await db.commit()
    return {"status": "rejected", "result": final}
