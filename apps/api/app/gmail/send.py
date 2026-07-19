import base64
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.gmail.client import gmail_service
from app.models import Email, GmailAccount


def _send_raw(account: GmailAccount, message: EmailMessage) -> str:
    service = gmail_service(account)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent["id"]


def send_email(account: GmailAccount, to: str, subject: str, body: str) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = account.email
    msg["Subject"] = subject
    msg.set_content(body)
    return _send_raw(account, msg)


def forward_email(
    db: Session, account: GmailAccount, email_db_id: int, to: str, note: str = ""
) -> str:
    original = db.get(Email, email_db_id)
    if original is None:
        raise ValueError(f"email id {email_db_id} not found")
    subject = original.subject or "(no subject)"
    if not subject.lower().startswith("fwd:"):
        subject = f"Fwd: {subject}"
    body_parts = []
    if note:
        body_parts.append(note + "\n")
    body_parts.append(
        f"---------- Forwarded message ----------\n"
        f"From: {original.sender}\n"
        f"Date: {original.internal_date}\n"
        f"Subject: {original.subject}\n\n"
        f"{original.body_text or original.snippet or ''}"
    )
    return send_email(account, to, subject, "\n".join(body_parts))
