"""Resource-scoped download credentials; only issue after business authorization."""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode

from infrastructure.config import settings


def normalize_key(key: str) -> str:
    if not key or key.startswith("/") or "\\" in key or any(ord(c) < 32 for c in key):
        raise ValueError("invalid storage key")
    if any(part in ("", ".", "..") for part in key.split("/")):
        raise ValueError("invalid storage key")
    return str(PurePosixPath(key))


def _signature(key: str, expires: int) -> str:
    # Purpose separation: a file signature cannot be used as an access JWT.
    message = f"cruise-download-v1\n{settings.JWT_ISSUER}\n{expires}\n{key}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def signed_download_url(key: str, expires_in: int = 3600) -> str:
    key = normalize_key(key)
    expires = int(time.time()) + max(1, min(expires_in, 3600))
    query = urlencode({"expires": expires, "signature": _signature(key, expires)})
    return f"{settings.PUBLIC_BACKEND_URL.rstrip('/')}/uploads/{quote(key, safe='/')}?{query}"


def valid_download_signature(key: str, expires: int, signature: str) -> bool:
    now = int(time.time())
    return (
        now < expires <= now + 3600
        and len(signature) == 64
        and hmac.compare_digest(_signature(key, expires), signature)
    )
