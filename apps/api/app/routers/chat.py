import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import AgentContext, set_context
from app.agent.graph import build_agent
from app.db import get_db
from app.models import AgentAction, ChatMessage, ChatSession, GmailAccount, User
from app.security import current_user

router = APIRouter(prefix="/chat", tags=["chat"])


class MessageIn(BaseModel):
    content: str


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _agent_ctx(user: User, db: AsyncSession) -> AgentContext:
    accounts = (
        (await db.execute(select(GmailAccount).where(GmailAccount.user_id == user.id)))
        .scalars()
        .all()
    )
    if not accounts:
        raise HTTPException(400, "No Gmail account connected")
    return AgentContext(
        user_id=user.id,
        account_ids=[a.id for a in accounts],
        primary_account_id=accounts[0].id,
        user_email=user.email,
    )


@router.post("/sessions")
async def create_session(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    session = ChatSession(user_id=user.id, title="New chat")
    db.add(session)
    await db.commit()
    return {"id": session.id}


@router.get("/sessions")
async def list_sessions(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    sessions = (
        (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.user_id == user.id)
                .order_by(ChatSession.created_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    return [
        {"id": s.id, "title": s.title, "created_at": s.created_at.isoformat()} for s in sessions
    ]


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(404, "session not found")
    messages = (
        (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        {"id": m.id, "role": m.role, "content": m.content, "tool_calls": m.tool_calls_json}
        for m in messages
    ]


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: int,
    body: MessageIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(404, "session not found")

    ctx = await _agent_ctx(user, db)

    # persist user message; first message titles the session
    db.add(ChatMessage(session_id=session_id, role="user", content=body.content))
    if session.title == "New chat":
        session.title = body.content[:80]
    await db.commit()

    thread_id = f"session-{session_id}"
    graph = build_agent(request.app.state.checkpointer, ctx.user_email)

    async def stream() -> AsyncGenerator[str, None]:
        set_context(ctx)
        final_text = ""
        tool_calls_log: list[dict] = []
        try:
            async for mode, payload in graph.astream(
                {"messages": [{"role": "user", "content": body.content}]},
                config={"configurable": {"thread_id": thread_id}},
                stream_mode=["messages", "updates"],
            ):
                if mode == "messages":
                    chunk, _meta = payload
                    if isinstance(chunk, AIMessageChunk) and chunk.content:
                        text = chunk.content if isinstance(chunk.content, str) else "".join(
                            p.get("text", "") for p in chunk.content if isinstance(p, dict)
                        )
                        if text:
                            final_text += text
                            yield _sse("token", {"text": text})
                elif mode == "updates":
                    if "__interrupt__" in payload:
                        intr = payload["__interrupt__"][0]
                        info = intr.value  # {"action_type", "payload"}
                        async for out in _handle_interrupt(info, thread_id, session_id):
                            yield out
                        return
                    # surface tool activity to the UI
                    for _node, update in payload.items():
                        for msg in (update or {}).get("messages", []):
                            if getattr(msg, "tool_calls", None):
                                for tc in msg.tool_calls:
                                    tool_calls_log.append({"tool": tc["name"], "args": tc["args"]})
                                    yield _sse("tool", {"name": tc["name"], "args": tc["args"]})
                            elif getattr(msg, "type", "") == "tool" and msg.name == "search_emails":
                                try:
                                    results = (
                                        json.loads(msg.content)
                                        if isinstance(msg.content, str)
                                        else msg.content
                                    )
                                    yield _sse("citations", {"results": results})
                                except (json.JSONDecodeError, TypeError):
                                    pass
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})
            return

        from app.db import async_session

        async with async_session() as db2:
            db2.add(
                ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=final_text,
                    tool_calls_json={"calls": tool_calls_log} if tool_calls_log else None,
                )
            )
            await db2.commit()
        yield _sse("done", {})

    async def _handle_interrupt(
        info: dict, thread_id: str, session_id: int
    ) -> AsyncGenerator[str, None]:
        from app.db import async_session

        async with async_session() as db2:
            action = AgentAction(
                user_id=user.id,
                session_id=session_id,
                thread_id=thread_id,
                action_type=info.get("action_type", "unknown"),
                payload_json=info.get("payload", {}),
                status="pending_approval",
                created_at=datetime.now(UTC),
            )
            db2.add(action)
            await db2.commit()
            yield _sse(
                "action_required",
                {
                    "action_id": action.id,
                    "action_type": action.action_type,
                    "payload": action.payload_json,
                },
            )

    return StreamingResponse(stream(), media_type="text/event-stream")
