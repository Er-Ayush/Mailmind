"""Hybrid retrieval: SQL metadata filters -> pgvector cosine + Postgres FTS blend.

The "filter-then-search" pattern: metadata (sender/date) is exact SQL, semantics
is vector similarity, keywords get a full-text boost. Scores blend 70/30.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.embeddings import embed_query

VEC_WEIGHT = 0.7
FTS_WEIGHT = 0.3


def hybrid_search(
    db: Session,
    account_ids: list[int],
    query: str,
    sender: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    k: int = 8,
    use_fts: bool = True,
) -> list[dict[str, Any]]:
    qvec = str(embed_query(query))

    filters = ["e.account_id = ANY(:aids)", "c.embedding IS NOT NULL"]
    params: dict[str, Any] = {"aids": account_ids, "qvec": qvec, "q": query, "k": k * 4}
    if sender:
        filters.append("e.sender ILIKE :sender")
        params["sender"] = f"%{sender}%"
    if date_from:
        filters.append("e.internal_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append("e.internal_date <= :date_to")
        params["date_to"] = date_to

    fts_expr = (
        "ts_rank(to_tsvector('english', coalesce(e.subject,'') || ' ' || "
        "coalesce(e.body_text,'')), websearch_to_tsquery('english', :q))"
        if use_fts
        else "0"
    )

    sql = text(
        f"""
        SELECT e.id AS email_id, e.gmail_id, e.thread_id, e.subject, e.sender,
               e.internal_date, e.snippet, c.content AS chunk,
               (1 - (c.embedding <=> CAST(:qvec AS vector))) AS vec_score,
               {fts_expr} AS fts_score
        FROM email_chunks c
        JOIN emails e ON e.id = c.email_id
        WHERE {" AND ".join(filters)}
        ORDER BY ({VEC_WEIGHT} * (1 - (c.embedding <=> CAST(:qvec AS vector)))
                  + {FTS_WEIGHT} * {fts_expr}) DESC
        LIMIT :k
        """
    )

    rows = db.execute(sql, params).mappings().all()

    # Dedupe chunks -> best-scoring hit per email, keep top-k
    seen: dict[int, dict[str, Any]] = {}
    for r in rows:
        if r["email_id"] not in seen:
            seen[r["email_id"]] = {
                "email_id": r["email_id"],
                "gmail_id": r["gmail_id"],
                "thread_id": r["thread_id"],
                "subject": r["subject"],
                "sender": r["sender"],
                "date": r["internal_date"].isoformat() if r["internal_date"] else None,
                "snippet": r["snippet"],
                "chunk": r["chunk"],
                "score": round(
                    VEC_WEIGHT * float(r["vec_score"]) + FTS_WEIGHT * float(r["fts_score"]), 4
                ),
            }
        if len(seen) >= k:
            break
    return list(seen.values())
