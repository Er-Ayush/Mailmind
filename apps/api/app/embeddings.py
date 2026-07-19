"""Embeddings with two providers:

- "local" (default): sentence-transformers all-mpnet-base-v2, 768 dims. Free,
  unlimited, offline — API free tiers count every chunk against daily caps, which
  a whole-inbox backfill blows through in minutes.
- "gemini": gemini-embedding-2 via API (768 dims requested) — kept for reference
  and easy switching.

The provider must never be mixed per deployment: vectors from different models
live in different spaces and cannot be compared.
"""

import logging
import time
from functools import lru_cache

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _local_model():
    from sentence_transformers import SentenceTransformer

    # 768 dims — matches vector(768) schema; ~420MB one-time download
    return SentenceTransformer("sentence-transformers/all-mpnet-base-v2")


def _local_embed(texts: list[str]) -> list[list[float]]:
    return _local_model().encode(texts, batch_size=32, show_progress_bar=False).tolist()


def _gemini_embedder():
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    s = get_settings()
    return GoogleGenerativeAIEmbeddings(
        model=s.gemini_embedding_model,
        google_api_key=s.gemini_api_key,
        output_dimensionality=s.embedding_dim,
    )


def embed_texts(texts: list[str], batch_size: int | None = None) -> list[list[float]]:
    """Embed documents. Local: fast and unlimited. Gemini: throttled batches."""
    if not texts:
        return []
    s = get_settings()
    if s.embedding_provider == "local":
        return _local_embed(texts)

    batch_size = batch_size or s.embed_batch_size
    model = _gemini_embedder()
    out: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        for attempt in range(5):
            try:
                out.extend(model.embed_documents(batch))
                break
            except Exception as exc:  # rate limit / transient — back off and retry
                wait = 2**attempt * 5
                logger.warning("embed batch failed (%s); retrying in %ss", exc, wait)
                time.sleep(wait)
        else:
            raise RuntimeError("embedding failed after 5 retries")
        if i + batch_size < len(texts):
            # free tier: ~30k tokens/min — a 32-chunk batch is ~12k tokens → ~2/min
            time.sleep(26.0)
    return out


def embed_query(text: str) -> list[float]:
    if get_settings().embedding_provider == "local":
        return _local_embed([text])[0]
    return _gemini_embedder().embed_query(text)
