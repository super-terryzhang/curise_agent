"""Bind-flow URL helpers + signature verification for LINE webhooks.

`build_bind_url` produces the link we send to the user via LINE on first
contact. The frontend page at `${LINE_BIND_BASE_URL}/line/bind?token=...`
collects the user's email + password, then POSTs to
`/api/line/bind/{token}` to consume the token (see `apps/http/line_bind.py`).

`verify_signature` is the gatekeeper for `POST /line/webhook`: anything
without a valid X-Line-Signature header is dropped before we even parse
the body. LINE generates the signature as the base64-encoded HMAC-SHA256
of the *raw* request body using the channel secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import urlencode


def build_bind_url(*, base_url: str, token: str) -> str:
    """Construct the URL we send via LINE for the user to complete binding.

    `base_url` is `LINE_BIND_BASE_URL` from settings. The frontend handles
    `/line/bind` and uses the token from the query string.
    """
    base = base_url.rstrip("/")
    qs = urlencode({"token": token})
    return f"{base}/line/bind?{qs}"


def verify_signature(*, body: bytes, signature_header: str, channel_secret: str) -> bool:
    """Return True iff `signature_header` is a valid LINE signature for `body`.

    `body` MUST be the raw request bytes — re-serializing the parsed JSON
    will not match because LINE signs the exact bytes they sent.

    Defenses included:
    - Empty / missing header → False
    - Empty channel_secret → False (don't accidentally pass when misconfigured)
    - Constant-time comparison (`hmac.compare_digest`)
    """
    if not signature_header or not channel_secret:
        return False

    digest = hmac.new(
        channel_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature_header)
