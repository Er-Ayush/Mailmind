from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import GmailAccount, User
from app.security import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    create_session_token,
    current_user,
    encrypt_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _flow() -> Flow:
    s = get_settings()
    return Flow.from_client_config(
        {
            "web": {
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [s.oauth_redirect_uri],
            }
        },
        scopes=SCOPES,
        redirect_uri=s.oauth_redirect_uri,
    )


@router.get("/google/login")
async def google_login() -> RedirectResponse:
    if not get_settings().google_client_id:
        raise HTTPException(500, "GOOGLE_CLIENT_ID not configured — fill apps/api/.env")
    auth_url, _state = _flow().authorization_url(
        access_type="offline",  # get a refresh token
        prompt="consent",  # force refresh token even on re-auth
        include_granted_scopes="true",
    )
    return RedirectResponse(auth_url)


@router.get("/google/callback")
async def google_callback(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    flow = _flow()
    try:
        flow.fetch_token(authorization_response=str(request.url))
    except Exception as exc:
        raise HTTPException(400, f"OAuth exchange failed: {exc}") from exc

    creds = flow.credentials
    if not creds.refresh_token:
        raise HTTPException(
            400,
            "No refresh token returned — remove app access at "
            "myaccount.google.com/permissions and retry",
        )

    # Identify the user via the ID token / userinfo
    import google.auth.transport.requests
    import google.oauth2.id_token

    idinfo = google.oauth2.id_token.verify_oauth2_token(
        creds.id_token,
        google.auth.transport.requests.Request(),
        get_settings().google_client_id,
        clock_skew_in_seconds=10,
    )
    google_sub: str = idinfo["sub"]
    email: str = idinfo["email"]
    name: str | None = idinfo.get("name")

    user = (
        await db.execute(select(User).where(User.google_sub == google_sub))
    ).scalar_one_or_none()
    if user is None:
        user = User(google_sub=google_sub, email=email, name=name)
        db.add(user)
        await db.flush()

    account = (
        await db.execute(
            select(GmailAccount).where(
                GmailAccount.user_id == user.id, GmailAccount.email == email
            )
        )
    ).scalar_one_or_none()
    enc = encrypt_token(creds.refresh_token)
    if account is None:
        account = GmailAccount(user_id=user.id, email=email, refresh_token_enc=enc)
        db.add(account)
    else:
        account.refresh_token_enc = enc
        account.status = "active"
    await db.commit()

    resp = RedirectResponse(get_settings().frontend_origin)
    resp.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return resp


@router.get("/me")
async def me(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    accounts = (
        (await db.execute(select(GmailAccount).where(GmailAccount.user_id == user.id)))
        .scalars()
        .all()
    )
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "gmail_accounts": [
            {
                "id": a.id,
                "email": a.email,
                "status": a.status,
                "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
            }
            for a in accounts
        ],
    }
