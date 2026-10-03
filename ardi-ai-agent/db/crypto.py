"""At-rest encryption for shop payment secrets (Chapa keys).

Format: "fernet:<token>" when ARDI_MASTER_KEY is set, else "plain:<value>"
with a startup warning. Legacy raw values (no prefix) still decrypt.
Nothing here ever logs secret material.
"""
import base64
import hashlib
import logging

logger = logging.getLogger(__name__)
_warned = False


def _fernet():
    from config import ARDI_MASTER_KEY
    key = (ARDI_MASTER_KEY or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        if len(key) == 44:
            return Fernet(key.encode())
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))
    except Exception as e:
        logger.error("Fernet init failed — secrets stored unencrypted: %s", e)
        return None


def key_configured() -> bool:
    from config import ARDI_MASTER_KEY
    return bool((ARDI_MASTER_KEY or "").strip())


def encrypt_secret(plain: str) -> str:
    global _warned
    value = (plain or "").strip()
    if not value:
        return ""
    f = _fernet()
    if f is None:
        if not _warned:
            logger.warning("ARDI_MASTER_KEY empty — shop secrets stored WITHOUT encryption")
            _warned = True
        return f"plain:{value}"
    return "fernet:" + f.encrypt(value.encode()).decode()


def decrypt_secret(stored: str | None) -> str:
    if not stored:
        return ""
    if stored.startswith("fernet:"):
        f = _fernet()
        if f is None:
            logger.error("Cannot decrypt shop secret: ARDI_MASTER_KEY missing")
            return ""
        try:
            return f.decrypt(stored[len("fernet:"):].encode()).decode()
        except Exception:
            logger.error("Shop secret decryption failed (wrong key?)")
            return ""
    if stored.startswith("plain:"):
        return stored[len("plain:"):]
    return stored  # legacy raw value
