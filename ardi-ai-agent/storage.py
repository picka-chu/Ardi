import logging
from uuid import uuid4
import boto3
from botocore.exceptions import ClientError

from config import R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_BUCKET, R2_PUBLIC_URL

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
        )
    return _client


async def upload_product_photo(photo_bytes: bytes, business_id: int, product_name: str) -> str | None:
    key = f"products/{business_id}/{uuid4()}-{product_name[:30].replace(' ', '_')}.jpg"
    try:
        client = _get_client()
        client.put_object(
            Bucket=R2_BUCKET,
            Key=key,
            Body=photo_bytes,
            ContentType="image/jpeg",
        )
        url = f"{R2_PUBLIC_URL}/{key}" if R2_PUBLIC_URL else key
        logger.info("Photo uploaded to R2: %s", key)
        return url
    except ClientError as e:
        logger.error("R2 upload failed: %s", e)
        return None
    except Exception as e:
        logger.error("R2 upload error: %s", e)
        return None