"""Per-request agent context.

Tools are sync functions with fixed signatures; the current user's identity is
carried in a ContextVar set by the chat/actions endpoints before graph invocation.
"""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class AgentContext:
    user_id: int
    account_ids: list[int]
    primary_account_id: int
    user_email: str


_current: ContextVar[AgentContext | None] = ContextVar("agent_context", default=None)


def set_context(ctx: AgentContext) -> None:
    _current.set(ctx)


def get_context() -> AgentContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("agent context not set")
    return ctx
