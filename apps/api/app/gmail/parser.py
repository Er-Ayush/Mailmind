import base64
import re
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup


def _b64decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _walk_parts(payload: dict[str, Any]) -> tuple[str, str, bool]:
    """Return (plain_text, html_text, has_attachments) from a message payload tree."""
    plain, html = [], []
    has_attachments = False

    def walk(part: dict[str, Any]) -> None:
        nonlocal has_attachments
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        if body.get("attachmentId"):
            has_attachments = True
        data = body.get("data")
        if data:
            if mime == "text/plain":
                plain.append(_b64decode(data))
            elif mime == "text/html":
                html.append(_b64decode(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return "\n".join(plain), "\n".join(html), has_attachments


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Gmail API message resource into our emails-table shape."""
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    plain, html, has_attachments = _walk_parts(msg.get("payload", {}))

    body = plain
    if not body and html:
        body = BeautifulSoup(html, "html.parser").get_text(separator="\n")
    body = _clean_text(body)

    internal_ms = int(msg.get("internalDate", "0"))
    return {
        "gmail_id": msg["id"],
        "thread_id": msg.get("threadId"),
        "sender": headers.get("from"),
        "recipients": [v for k in ("to", "cc") if (v := headers.get(k))],
        "subject": headers.get("subject"),
        "snippet": msg.get("snippet"),
        "body_text": body[:100_000],  # guard against pathological emails
        "labels": msg.get("labelIds", []),
        "internal_date": datetime.fromtimestamp(internal_ms / 1000, tz=UTC),
        "has_attachments": has_attachments,
    }


def chunk_text(text: str, size: int = 1500, overlap: int = 150) -> list[str]:
    """Simple sliding-window chunking for embeddings."""
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return chunks
