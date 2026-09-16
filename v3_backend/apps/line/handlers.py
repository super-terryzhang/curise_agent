"""Per-event handlers — what we do with each parsed LINE event.

Phase L1 scope:
- DM text messages from a bound user → run agent, reply with answer
- DM text messages from an UNBOUND user → reply with bind URL
- DM follow event → welcome + bind URL
- DM image/file → polite "not yet supported" reply (Phase L3 will add)
- Group events → polite "未支持" reply (Phase L2 will add)

All branches that touch the agent or DB go through `domains.line.service`
or `agent_runner.run_for_line` — handlers are mostly orchestration. This
file deliberately does NOT call into `apps.http.*`; it depends only on
the domain + agent layers + its own `apps.line.*` siblings.

Reply policy:
- We always use `platform.reply_text` (free, within 30s of webhook receipt).
- On group-not-supported / file-not-supported we still reply (so the user
  isn't left wondering); these are short messages that fit in one chunk.
- We never `push` from a handler — push costs money and Phase L1 doesn't
  need any.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from agent.storage.models import ChatSession
from apps.line import agent_runner, delivery, identity
from apps.line import session as session_mod
from apps.line.platform import LinePlatform
from apps.line.session import SessionSource
from domains.line import service as line_service
from infrastructure.config import settings

logger = logging.getLogger(__name__)


# ─── Parsed event shape ───────────────────────────────────────


@dataclass(frozen=True)
class ParsedEvent:
    """Webhook event we've extracted out of the raw JSON.

    We deliberately work with this small shape rather than passing dicts
    around — it documents what the rest of the code is allowed to assume,
    and keeps the linebot SDK out of the picture entirely.
    """

    event_id: str
    event_type: str  # "message" | "follow" | "unfollow" | "join" | "leave" | other
    message_type: str | None  # "text" | "image" | "file" | None
    text: str | None
    reply_token: str | None
    source: SessionSource


# ─── Reply texts (kept in one place for easy translation later) ──


_WELCOME = (
    "欢迎使用邮轮供应链管理助手！\n\n"
    "为了保护公司数据，使用前需要先绑定您的公司账号：\n{url}\n\n"
    "绑定后即可发送消息查询订单、产品、文档等信息。"
)
_BIND_REQUIRED = (
    "您还未绑定公司账号。请点击下方链接完成绑定后再使用：\n{url}\n\n"
    "（链接 {ttl} 分钟内有效）"
)
_GROUP_NOT_SUPPORTED = (
    "群聊功能即将上线。当前请通过私聊向我提问。"
)
_FILE_NOT_SUPPORTED = (
    "文件解析功能即将上线。当前请通过文字描述您的需求。"
)
_IMAGE_NOT_SUPPORTED = (
    "图片解析功能即将上线。当前请通过文字描述您的需求。"
)
_RESET_DONE = (
    "已为您开始一段新对话。"
)


# ─── Public entry ─────────────────────────────────────────────


async def process_event(
    *,
    event: ParsedEvent,
    db: Session,
    platform: LinePlatform,
) -> None:
    """Top-level dispatcher. Always swallows exceptions (logs + best-effort
    reply) — we already returned 200 to LINE before getting here, so raising
    accomplishes nothing but log spam.
    """
    try:
        # Idempotency guard. LINE retries on 5xx; even though we return 200
        # immediately, network glitches can cause a redelivery.
        if not line_service.record_event_id(db, event_id=event.event_id):
            logger.info("LINE: skipping duplicate event %s", event.event_id)
            return

        if event.source.is_group:
            await _handle_group_event(event=event, platform=platform)
            return

        # DM path.
        await _handle_dm_event(event=event, db=db, platform=platform)
    except Exception:
        logger.exception("LINE: process_event failed for %s", event.event_id)


# ─── Group ────────────────────────────────────────────────────


async def _handle_group_event(*, event: ParsedEvent, platform: LinePlatform) -> None:
    """Phase L1: groups are not yet supported. Reply once and bail."""
    if not event.reply_token:
        return
    # Only respond on text messages — follow/join etc. shouldn't trigger this
    # message; we keep silent so we don't spam every group event.
    if event.event_type != "message" or event.message_type != "text":
        return
    await platform.reply_text(
        reply_token=event.reply_token,
        texts=[_GROUP_NOT_SUPPORTED],
    )


# ─── DM ───────────────────────────────────────────────────────


async def _handle_dm_event(
    *, event: ParsedEvent, db: Session, platform: LinePlatform
) -> None:
    line_user = line_service.find_user_by_line_id(
        db,
        line_user_id=event.source.user_id,
        channel_id=event.source.channel_id,
    )

    if line_user is None:
        await _handle_unbound_dm(event=event, db=db, platform=platform)
        return

    if line_user.is_blocked:
        # Silently drop messages from blocked users. Don't reply — the user
        # would learn the bot is reachable and try again.
        logger.info(
            "LINE: dropping message from blocked user %s", line_user.line_user_id[:8]
        )
        return

    from domains.identity import service as identity_service

    try:
        current_user = identity_service.get_business_user(db, line_user.user_id)
    except identity_service.AuthError:
        logger.info("LINE: internal account unavailable")
        return
    line_service.update_last_active(db, line_user=line_user)
    await _handle_bound_dm(event=event, db=db, line_user_id=line_user.user_id, line_user_role=current_user.role, platform=platform)


async def _handle_unbound_dm(
    *, event: ParsedEvent, db: Session, platform: LinePlatform
) -> None:
    """Send the bind URL. Same reply for both follow and message events,
    just with slightly different framing."""
    if not event.reply_token:
        return

    plaintext = line_service.generate_bind_token(
        db,
        line_user_id=event.source.user_id,
        channel_id=event.source.channel_id,
        ttl_minutes=settings.LINE_BIND_TOKEN_TTL_MINUTES,
    )
    url = identity.build_bind_url(
        base_url=settings.LINE_BIND_BASE_URL, token=plaintext
    )
    template = _WELCOME if event.event_type == "follow" else _BIND_REQUIRED
    msg = template.format(url=url, ttl=settings.LINE_BIND_TOKEN_TTL_MINUTES)
    await platform.reply_text(reply_token=event.reply_token, texts=[msg])


async def _handle_bound_dm(
    *,
    event: ParsedEvent,
    db: Session,
    line_user_id: int,
    line_user_role: str,
    platform: LinePlatform,
) -> None:
    if event.event_type == "follow":
        # Already bound, but they re-followed (e.g. unblocked us). Send a
        # short welcome-back; no bind link needed.
        if event.reply_token:
            await platform.reply_text(
                reply_token=event.reply_token,
                texts=["欢迎回来！请发送您的问题。"],
            )
        return

    if event.event_type != "message" or not event.reply_token:
        return

    if event.message_type == "image":
        await platform.reply_text(
            reply_token=event.reply_token, texts=[_IMAGE_NOT_SUPPORTED]
        )
        return
    if event.message_type == "file":
        await platform.reply_text(
            reply_token=event.reply_token, texts=[_FILE_NOT_SUPPORTED]
        )
        return
    if event.message_type != "text" or event.text is None:
        # Sticker, video, audio, location — silently skip.
        return

    text = event.text.strip()
    if not text:
        return

    # Reset keyword — close the active session, open a fresh one, ack.
    if session_mod.is_reset_keyword(text):
        _close_active_session(db, user_id=line_user_id, end_reason="user_reset")
        await platform.reply_text(reply_token=event.reply_token, texts=[_RESET_DONE])
        return

    # Resolve session: most recent active LINE session for this user, or new.
    session_id = _resolve_session(db, user_id=line_user_id, source=event.source)

    # Show typing indicator (DM only — LINE rejects on groups). Roughly
    # 100ms HTTP round-trip in prod; we await so the user sees the indicator
    # immediately rather than racing with the agent. Failure logged + ignored
    # by show_loading itself.
    await platform.show_loading(chat_id=event.source.user_id, seconds=30)

    # Run the agent. This is sync work on a worker thread — wrap in to_thread
    # so the FastAPI event loop keeps serving other requests.
    answer = await asyncio.to_thread(
        agent_runner.run_for_line,
        user_id=line_user_id,
        user_role=line_user_role,
        session_id=session_id,
        text=text,
    )

    # A reply may have waited on the model while the account was disabled.
    from domains.identity import service as identity_service

    try:
        current = identity_service.get_business_user(db, line_user_id)
    except identity_service.AccountInactive:
        return
    if current.role != line_user_role:
        return

    # Pick rendering route: Flex bubble for structured (tables / lists),
    # plain text otherwise. format_for_line never raises — on any parse
    # failure it falls back to text so the user always gets a reply.
    reply = delivery.format_for_line(answer or "")
    if isinstance(reply, delivery.FlexReply):
        await platform.reply_flex(
            reply_token=event.reply_token,
            alt_text=reply.alt_text,
            contents=reply.contents,
        )
    else:
        await platform.reply_text(
            reply_token=event.reply_token, texts=reply.chunks
        )


# ─── Helpers ──────────────────────────────────────────────────


def _lookup_user_role(db: Session, user_id: int) -> str:
    """Fetch the current valid internal role; missing users fail closed."""
    from domains.identity import service as identity_service

    return identity_service.get_business_user(db, user_id).role


def _resolve_session(
    db: Session, *, user_id: int, source: SessionSource
) -> str:
    """Pick the right ChatSession to reuse, or mint a new one.

    Strategy: find the most recent `platform_type='line'` session for this
    user. If it's stale (idle >30 min), close it and start fresh. Else
    reuse it so the agent has continuity.
    """
    from sqlalchemy import select

    stmt = (
        select(ChatSession)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.platform_type == "line",
            ChatSession.platform_user_id == source.user_id,
            ChatSession.status == "active",
        )
        .order_by(ChatSession.updated_at.desc())
        .limit(1)
    )
    existing = db.execute(stmt).scalar_one_or_none()

    if existing is not None:
        if session_mod.is_session_idle(existing.updated_at):
            existing.status = "closed"
            existing.end_reason = "idle_timeout"
            db.commit()
        else:
            return existing.id

    sid = uuid.uuid4().hex
    row = ChatSession(
        id=sid,
        user_id=user_id,
        title="LINE 对话",
        status="active",
        platform_type="line",
        platform_user_id=source.user_id,
    )
    db.add(row)
    db.commit()
    return sid


def _close_active_session(db: Session, *, user_id: int, end_reason: str) -> None:
    """Mark all this user's active LINE sessions closed (usually one)."""
    from sqlalchemy import update

    db.execute(
        update(ChatSession)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.platform_type == "line",
            ChatSession.status == "active",
        )
        .values(status="closed", end_reason=end_reason)
    )
    db.commit()
