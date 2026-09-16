"""POST /line/webhook — LINE Messaging API webhook entry point.

Flow:
1. Read raw body bytes (signature is over the bytes, not the parsed JSON).
2. Verify HMAC-SHA256(channel_secret, body) == X-Line-Signature, in
   constant time. Reject 401 on failure.
3. Parse JSON, walk events, build `ParsedEvent` per event.
4. Dispatch each via `handlers.process_event` as a background task.
5. Return 200 immediately so LINE doesn't retry (their timeout is short).

Security:
- 401 on bad signature is the *only* gate. Without it, any POST to
  /line/webhook would impersonate the platform. Never disable except
  via `LINE_DISABLE_SIGNATURE_VERIFICATION` flag for local dev — and
  the flag is logged at WARNING when active.
- We never log the channel secret, the access token, or the raw body
  contents (PII). Only the event count and event ids show up in logs.
- The `LinePlatform` instance is singleton-scoped via DI so tests can
  swap in a fake without monkey-patching anything.

Tests override `get_line_platform` via `app.dependency_overrides`. In
production it's instantiated lazily on first use against
`settings.LINE_CHANNEL_ACCESS_TOKEN`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from apps.line import handlers, identity
from apps.line.handlers import ParsedEvent
from apps.line.platform import LinePlatform
from apps.line.session import SessionSource
from infrastructure.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["line"])

# ─── Platform DI ──────────────────────────────────────────────


_platform_singleton: LinePlatform | None = None


def get_line_platform() -> LinePlatform:
    """FastAPI dependency. Lazy singleton.

    Override in tests via `app.dependency_overrides[get_line_platform] = ...`.
    """
    global _platform_singleton
    if _platform_singleton is None:
        if not settings.LINE_CHANNEL_ACCESS_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LINE channel not configured",
            )
        _platform_singleton = LinePlatform(
            channel_access_token=settings.LINE_CHANNEL_ACCESS_TOKEN
        )
    return _platform_singleton


# ─── Webhook ──────────────────────────────────────────────────


@router.post("/line/webhook")
async def line_webhook(
    request: Request,
    background: BackgroundTasks,
    platform: LinePlatform = Depends(get_line_platform),
) -> dict[str, Any]:
    body = await request.body()

    if not _signature_ok(body=body, request=request):
        # Don't leak any details — just 401.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")

    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Malformed body (which would have failed signature anyway, but defensive).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="malformed body"
        )

    events = payload.get("events") or []
    logger.info("LINE webhook received %d event(s)", len(events))

    parsed: list[ParsedEvent] = []
    for raw in events:
        evt = _parse_event(raw)
        if evt is not None:
            parsed.append(evt)

    # Hand off to BackgroundTasks: FastAPI runs these after the 200-OK
    # is flushed to LINE, so the platform sees a fast response. Each task
    # opens its OWN DB session — the request-scope DB is not used.
    for evt in parsed:
        background.add_task(_dispatch, evt, platform)

    return {"ok": True, "events": len(parsed)}


# ─── Internals ────────────────────────────────────────────────


def _signature_ok(*, body: bytes, request: Request) -> bool:
    if settings.LINE_DISABLE_SIGNATURE_VERIFICATION:
        logger.warning(
            "LINE webhook signature verification is DISABLED — dev only!"
        )
        return True
    sig = request.headers.get("X-Line-Signature", "")
    return identity.verify_signature(
        body=body,
        signature_header=sig,
        channel_secret=settings.LINE_CHANNEL_SECRET,
    )


async def _dispatch(event: ParsedEvent, platform: LinePlatform) -> None:
    """Open a fresh DB session for this event and run the handler."""
    from infrastructure.db.session import SessionLocal

    db = SessionLocal()
    try:
        await handlers.process_event(event=event, db=db, platform=platform)
    finally:
        db.close()


def _parse_event(raw: dict[str, Any]) -> ParsedEvent | None:
    """Translate a LINE webhook event into our internal `ParsedEvent`.

    Returns None for events we don't care about (unfollow, postback in
    Phase L1, etc.). The handler still runs the dedup record so we don't
    re-process them later.
    """
    event_id = str(raw.get("webhookEventId") or raw.get("id") or "")
    event_type = raw.get("type") or ""
    src = raw.get("source") or {}
    src_type = src.get("type") or ""
    user_id = src.get("userId") or ""

    if not user_id:
        # Spec violation — every source has a userId in v2 webhook. Drop.
        return None

    if src_type == "group":
        chat_id = src.get("groupId") or user_id
        chat_type = "group"
    elif src_type == "room":
        chat_id = src.get("roomId") or user_id
        chat_type = "group"  # treat rooms as groups for permission semantics
    else:
        chat_id = user_id
        chat_type = "dm"

    source = SessionSource(
        platform="line",
        channel_id=settings.LINE_CHANNEL_ID,
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=user_id,
    )

    message_type: str | None = None
    text: str | None = None
    reply_token: str | None = raw.get("replyToken")

    if event_type == "message":
        msg = raw.get("message") or {}
        message_type = msg.get("type") or ""
        if message_type == "text":
            text = msg.get("text")
        # image / file → bytes downloaded by handler if needed (Phase L3).

    return ParsedEvent(
        event_id=event_id,
        event_type=event_type,
        message_type=message_type,
        text=text,
        reply_token=reply_token,
        source=source,
    )
