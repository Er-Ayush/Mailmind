from cryptography.fernet import Fernet
from fastapi import Cookie, Depends, HTTPException
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import User

SESSION_COOKIE = "mailmind_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="session")


def create_session_token(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session_token(token: str) -> int | None:
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return int(data["uid"])
    except (BadSignature, KeyError, ValueError):
        return None


def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key.encode())


def encrypt_token(raw: str) -> str:
    return _fernet().encrypt(raw.encode()).decode()


def decrypt_token(enc: str) -> str:
    return _fernet().decrypt(enc.encode()).decode()


async def current_user(
    db: AsyncSession = Depends(get_db),
    mailmind_session: str | None = Cookie(default=None),
) -> User:
    """Dependency: resolve the logged-in user from the signed session cookie."""
    if not mailmind_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = read_session_token(mailmind_session)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user
