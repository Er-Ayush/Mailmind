from datetime import UTC, datetime

from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.prebuilt import create_react_agent

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import ALL_TOOLS
from app.config import get_settings


def checkpointer_conn_string() -> str:
    """Plain postgres:// DSN for the LangGraph Postgres checkpointer."""
    s = get_settings()
    return s.database_url.replace("postgresql+asyncpg", "postgresql")


def build_agent(checkpointer: AsyncPostgresSaver, user_email: str):
    s = get_settings()
    llm = ChatGoogleGenerativeAI(
        model=s.gemini_chat_model,
        google_api_key=s.gemini_api_key,
        temperature=0.2,
    )
    prompt = SYSTEM_PROMPT.format(
        today=datetime.now(UTC).date().isoformat(), user_email=user_email
    )
    return create_react_agent(llm, ALL_TOOLS, prompt=prompt, checkpointer=checkpointer)
