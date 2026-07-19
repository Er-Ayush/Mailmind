from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from app.config import get_settings
from app.models import GmailAccount
from app.security import decrypt_token

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def gmail_service(account: GmailAccount) -> Resource:
    """Build an authenticated Gmail API client from a stored (encrypted) refresh token."""
    s = get_settings()
    creds = Credentials(
        token=None,
        refresh_token=decrypt_token(account.refresh_token_enc),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
