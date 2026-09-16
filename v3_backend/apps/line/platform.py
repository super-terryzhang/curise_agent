"""LinePlatform — thin async wrapper around the LINE Messaging API.

We don't pull in `line-bot-sdk` because everything we need is five HTTPS
endpoints. Doing it ourselves means:
- Zero SDK version churn to manage.
- Tests can replace the whole class with a fake (no SDK monkey-patching).
- We control the timeout / retry / logging policy.

Endpoints used:
- POST  /v2/bot/message/reply       — free reply within 30s reply-token window
- POST  /v2/bot/message/push        — paid push (per-recipient billing)
- GET   /v2/bot/profile/{userId}    — display name lookup
- GET   /v2/bot/message/{id}/content (api-data host)
                                    — download media bytes (file/image)
- POST  /v2/bot/chat/loading/start  — show typing indicator while agent runs

The class is *async* — webhook handlers running in FastAPI's event loop call
into LINE without blocking. Callers MUST `await close()` on shutdown so
the underlying httpx client releases its connection pool.

`FakeLinePlatform` (in `tests/integration/line/conftest.py`) implements
the same surface for tests — no real network calls.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_PROFILE_URL_TMPL = "https://api.line.me/v2/bot/profile/{user_id}"
_CONTENT_URL_TMPL = "https://api-data.line.me/v2/bot/message/{message_id}/content"
_LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"


class LinePlatformError(Exception):
    """Wrapper for LINE API failures — preserves status + body for logs."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class LinePlatform:
    """Async client for the LINE Messaging API.

    One instance per process is enough; the underlying `httpx.AsyncClient`
    pools connections.
    """

    def __init__(self, *, channel_access_token: str, timeout_seconds: float = 10.0) -> None:
        self._token = channel_access_token
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "Authorization": f"Bearer {channel_access_token}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ─── Sending ────────────────────────────────────────────────

    async def reply_text(self, *, reply_token: str, texts: list[str]) -> None:
        """Reply to a webhook event using its reply_token. Free + 30s window.

        `texts` is already split (see `delivery.split_for_line`); LINE
        accepts up to 5 messages per call. We don't enforce that here —
        the caller is responsible.
        """
        body = {
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": t} for t in texts],
        }
        await self._post_json(_REPLY_URL, body)

    async def push_text(self, *, to: str, texts: list[str]) -> None:
        """Push to a userId / groupId. Counts toward billing (per recipient)."""
        body = {
            "to": to,
            "messages": [{"type": "text", "text": t} for t in texts],
        }
        await self._post_json(_PUSH_URL, body)

    async def reply_flex(
        self,
        *,
        reply_token: str,
        alt_text: str,
        contents: dict,
    ) -> None:
        """Reply with a single Flex Message.

        `alt_text` is what shows in notifications / older client previews.
        `contents` is the Flex JSON (bubble / carousel) produced by
        `apps.line.flex_renderer`. Same reply_token + 30s rules as text.
        """
        body = {
            "replyToken": reply_token,
            "messages": [
                {
                    "type": "flex",
                    "altText": alt_text or "新消息",
                    "contents": contents,
                }
            ],
        }
        await self._post_json(_REPLY_URL, body)

    async def show_loading(self, *, chat_id: str, seconds: int = 30) -> None:
        """Show the typing indicator on a 1:1 chat for up to 60 seconds.

        LINE silently rejects this for groups — we still call it because
        it returns 400 cleanly and we'd rather not branch on chat_type
        every time. We log + swallow the error.
        """
        if seconds < 5:
            seconds = 5
        if seconds > 60:
            seconds = 60
        body = {"chatId": chat_id, "loadingSeconds": seconds}
        try:
            await self._post_json(_LOADING_URL, body)
        except LinePlatformError as exc:
            logger.debug("show_loading rejected (chat %s): %s", chat_id[:8], exc)

    # ─── Lookups ────────────────────────────────────────────────

    async def get_profile(self, *, user_id: str) -> dict[str, Any] | None:
        """Fetch a user's display name + picture URL. None on 404 / blocked."""
        url = _PROFILE_URL_TMPL.format(user_id=user_id)
        try:
            r = await self._client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("get_profile network error: %s", exc)
            return None
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            logger.warning("get_profile %s → %s", user_id[:8], r.status_code)
            return None
        return r.json()

    async def download_content(self, *, message_id: str) -> bytes | None:
        """Download an image / file message's bytes. None on failure."""
        url = _CONTENT_URL_TMPL.format(message_id=message_id)
        try:
            r = await self._client.get(url, timeout=httpx.Timeout(60.0))
        except httpx.HTTPError as exc:
            logger.warning("download_content network error: %s", exc)
            return None
        if r.status_code != 200:
            logger.warning("download_content %s → %s", message_id, r.status_code)
            return None
        return r.content

    # ─── Internals ──────────────────────────────────────────────

    async def _post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await self._client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise LinePlatformError(f"network error: {exc}") from exc

        if r.status_code >= 400:
            raise LinePlatformError(
                f"LINE API {r.status_code}",
                status_code=r.status_code,
                body=r.text[:500],
            )

        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {}
