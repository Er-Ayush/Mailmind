"""Retrieval eval: Recall@5 for hybrid search vs vector-only.

Usage (from apps/api):
    uv run python ../../evals/run_evals.py

Reads ../../evals/golden.json — a list of {query, expected_email_ids} built from
YOUR real inbox (see golden.example.json). Prints a markdown table you can paste
into the README.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select  # noqa: E402

from app.db_sync import SyncSession  # noqa: E402
from app.models import GmailAccount  # noqa: E402
from app.retrieval.hybrid import hybrid_search  # noqa: E402

K = 5


def recall_at_k(retrieved: list[int], expected: list[int], k: int = K) -> float:
    if not expected:
        return 0.0
    top = set(retrieved[:k])
    return len(top & set(expected)) / len(set(expected))


def main() -> None:
    golden_path = Path(__file__).parent / "golden.json"
    if not golden_path.exists():
        print("evals/golden.json not found — copy golden.example.json and fill with real queries")
        raise SystemExit(1)
    golden = json.loads(golden_path.read_text())

    with SyncSession() as db:
        account_ids = list(db.execute(select(GmailAccount.id)).scalars().all())
        rows = []
        for case in golden:
            query, expected = case["query"], case["expected_email_ids"]
            hybrid = [
                r["email_id"] for r in hybrid_search(db, account_ids, query, k=K, use_fts=True)
            ]
            vec_only = [
                r["email_id"] for r in hybrid_search(db, account_ids, query, k=K, use_fts=False)
            ]
            rows.append(
                (query, recall_at_k(hybrid, expected), recall_at_k(vec_only, expected))
            )

    print(f"\n| Query | Hybrid Recall@{K} | Vector-only Recall@{K} |")
    print("|---|---|---|")
    for query, h, v in rows:
        print(f"| {query[:60]} | {h:.2f} | {v:.2f} |")
    hy = sum(r[1] for r in rows) / len(rows)
    vo = sum(r[2] for r in rows) / len(rows)
    print(f"| **Average** | **{hy:.2f}** | **{vo:.2f}** |")


if __name__ == "__main__":
    main()
