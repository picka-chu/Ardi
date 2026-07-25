import json
import logging
import time
import random
import asyncio
import threading
from google import genai
from google.genai import types

from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

_embed_client = None
_embed_lock = threading.Lock()


def _get_embed_client():
    global _embed_client
    if _embed_client is None:
        with _embed_lock:
            if _embed_client is None:
                _embed_client = genai.Client(api_key=GEMINI_API_KEY)
    return _embed_client

EMBED_MODEL = "text-embedding-004"
CAPTION_MODEL = "gemini-2.5-flash"
MAX_RETRIES = 2

CAPTION_PROMPT = """Describe this product photo for an Ethiopian shop in 1 short sentence (max 15 words).
Focus on: what the item is, its colour, packaging, or any visible label.
Return ONLY the description, no extra text."""


def _generate_caption_sync(image_bytes: bytes) -> str:
    for attempt in range(MAX_RETRIES):
        try:
            response = _get_embed_client().models.generate_content(
                model=CAPTION_MODEL,
                contents=types.Content(parts=[
                    types.Part(text=CAPTION_PROMPT),
                    types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=image_bytes)),
                ]),
            )
            text = response.text
            if text:
                return text.strip()[:200]
        except Exception as e:
            logger.warning("Caption attempt %d failed: %s", attempt + 1, e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(1 + random.uniform(0, 1))
    return ""


def _embed_text_sync(text: str) -> list[float]:
    if not text.strip():
        return []
    for attempt in range(MAX_RETRIES):
        try:
            result = _get_embed_client().models.embed_content(
                model=EMBED_MODEL,
                contents=text,
            )
            return result.embeddings[0].values
        except Exception as e:
            logger.warning("Embed attempt %d failed: %s", attempt + 1, e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(1 + random.uniform(0, 1))
    return []


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def product_to_match_text(product) -> str:
    parts = [product.name]
    if product.price:
        parts.append(f"{product.price} ETB")
    if product.photo_caption:
        parts.append(product.photo_caption)
    return " — ".join(parts)


def find_best_match_sync(customer_caption: str, customer_embedding: list[float], products: list, threshold: float = 0.60) -> list[dict]:
    """Return products matching the customer's photo, sorted by similarity."""
    results = []
    for p in products:
        if not p.photo_embedding:
            continue
        embed = json.loads(p.photo_embedding) if isinstance(p.photo_embedding, str) else p.photo_embedding
        sim = cosine_similarity(customer_embedding, embed)
        if sim >= threshold:
            results.append({"product": p, "similarity": sim})
    results.sort(key=lambda r: r["similarity"], reverse=True)
    return results


# ─── Async wrappers ──────────────────────────────────────────────────────────


async def generate_caption(image_bytes: bytes) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _generate_caption_sync, image_bytes)


async def embed_text(text: str) -> list[float]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _embed_text_sync, text)


async def caption_and_embed(image_bytes: bytes) -> tuple[str, list[float]]:
    """Generate caption and embedding in parallel."""
    caption_task = generate_caption(image_bytes)
    # For embedding we need the caption first, so we wait
    caption = await caption_task
    if not caption:
        return "", []
    embed = await embed_text(caption)
    return caption, embed
