from datetime import UTC, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 768  # Gemini text-embedding-004


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    gmail_accounts: Mapped[list["GmailAccount"]] = relationship(back_populates="user")


class GmailAccount(Base):
    __tablename__ = "gmail_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    email: Mapped[str] = mapped_column(String(320))
    refresh_token_enc: Mapped[str] = mapped_column(Text)  # Fernet-encrypted
    history_id: Mapped[str | None] = mapped_column(String(32))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")

    user: Mapped[User] = relationship(back_populates="gmail_accounts")

    __table_args__ = (UniqueConstraint("user_id", "email"),)


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("gmail_accounts.id"))
    gmail_id: Mapped[str] = mapped_column(String(32), unique=True)
    thread_id: Mapped[str | None] = mapped_column(String(32))
    sender: Mapped[str | None] = mapped_column(String(500))
    recipients: Mapped[list | None] = mapped_column(JSON)
    subject: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[list | None] = mapped_column(JSON)
    internal_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    embedded: Mapped[bool] = mapped_column(Boolean, default=False)

    chunks: Mapped[list["EmailChunk"]] = relationship(back_populates="email")

    __table_args__ = (Index("ix_emails_account_date", "account_id", internal_date.desc()),)


class EmailChunk(Base):
    __tablename__ = "email_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(default=0)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    email: Mapped[Email] = relationship(back_populates="chunks")

    __table_args__ = (UniqueConstraint("email_id", "chunk_index"),)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"), unique=True)
    txn_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    merchant: Mapped[str | None] = mapped_column(String(300))
    reference_no: Mapped[str | None] = mapped_column(String(100))
    txn_type: Mapped[str | None] = mapped_column(String(20))  # debit|credit|refund|other
    account_hint: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[float | None] = mapped_column()
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("chat_sessions.id"))
    thread_id: Mapped[str] = mapped_column(String(64))  # LangGraph thread for resume
    action_type: Mapped[str] = mapped_column(String(40))  # send_email | forward_emails
    payload_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending_approval")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="session")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text)
    tool_calls_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
