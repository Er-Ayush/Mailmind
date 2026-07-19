import logging
import time

from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.config import get_settings

logger = logging.getLogger(__name__)


def embedder() -> GoogleGenerativeAIEmbeddings:
    s = get_settings()
    return GoogleGenerativeAIEmbeddings(
        model=s.gemini_embedding_model,
        google_api_key=s.gemini_api_key,
        output_dimensionality=s.embedding_dim,  # match vector(768) column
    )


def embed_texts(texts: list[str], batch_size: int | None = None) -> list[list[float]]:
    """Embed documents in throttled batches to respect Gemini free-tier rate limits."""
    if not texts:
        return []
    batch_size = batch_size or get_settings().embed_batch_size
    model = embedder()
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
    return embedder().embed_query(text)
