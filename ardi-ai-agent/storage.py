import asyncio
import logging
import re
import threading
from uuid import uuid4
import boto3
from botocore.exceptions import ClientError

from config import R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_BUCKET, R2_PUBLIC_URL

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()
_MAX_BYTES = 5 * 1024 * 1024


def _r2_configured() -> bool:
    return bool(R2_ACCESS_KEY and R2_SECRET_KEY and R2_ENDPOINT and R2_BUCKET)


def _sanitize_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "product")[:30]).strip("_")
    return safe or "product"


def _get_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = boto3.client(
                    "s3",
                    endpoint_url=R2_ENDPOINT,
                    aws_access_key_id=R2_ACCESS_KEY,
                    aws_secret_access_key=R2_SECRET_KEY,
                )
    return _client


def _upload_sync(key: str, photo_bytes: bytes, content_type: str) -> str | None:
    client = _get_client()
    client.put_object(
        Bucket=R2_BUCKET,
        Key=key,
        Body=photo_bytes,
        ContentType=content_type,
    )
    return f"{R2_PUBLIC_URL}/{key}" if R2_PUBLIC_URL else key


def _detect_content_type(photo_bytes: bytes) -> str:
    if photo_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if photo_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if photo_bytes[:4] == b"RIFF" and photo_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


async def upload_product_photo(photo_bytes: bytes, business_id: int, product_name: str) -> str | None:
    if not _r2_configured():
        logger.error("R2 not configured — set R2_ACCESS_KEY/R2_SECRET_KEY/R2_ENDPOINT/R2_BUCKET")
        return None
    if not photo_bytes or len(photo_bytes) > _MAX_BYTES:
        logger.error("Rejected upload: empty or too large (%s bytes)", len(photo_bytes or b""))
        return None
    safe = _sanitize_name(product_name)
    ext = {"image/png": "png", "image/webp": "webp"}.get(_detect_content_type(photo_bytes), "jpg")
    key = f"products/{int(business_id)}/{uuid4()}-{safe}.{ext}"
    content_type = _detect_content_type(photo_bytes)
    try:
        url = await asyncio.to_thread(_upload_sync, key, photo_bytes, content_type)
        logger.info("Photo uploaded to R2: %s", key)
        return url
    except ClientError as e:
        logger.error("R2 upload failed: %s", e)
        return None
    except Exception as e:
        logger.error("R2 upload error: %s", e)
        return None