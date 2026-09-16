"""Section 8 integration fixtures: fake LinePlatform + stubbed agent + webhook helpers.

测试目标：
    把两块"真实世界"换成可观测的 in-process 替身：
    - LinePlatform → FakeLinePlatform 把每次 reply / push / loading 调用
      塞进 list，测试断言 list 内容。
    - agent_runner.run_for_line → 固定返回字符串的 stub，避免拉起 LLM。
    再提供一组 helper 工厂来构造 webhook payload + 正确的 HMAC 签名。

为什么重要：
    LINE 集成测试要验的是 *路由 / 数据流*，不是真的发 HTTPS 给 LINE。
    把 platform 替成 fake 让我们能断言"这条消息 *被发送* 了"而无需 mock httpx。
    把 agent 替成 stub 避免一次集成测试要跑 5-30s 的 LLM 调用。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.line import webhook as webhook_module
from main import app

# 测试专用 channel secret + access token —— 任何随便的字符串都行，
# 关键是固定下来让 helper 能预签名。
LINE_TEST_SECRET = "section8-channel-secret"
LINE_TEST_TOKEN = "section8-access-token"


# ─── FakeLinePlatform ─────────────────────────────────────────


@dataclass
class FakeLinePlatform:
    """In-process LinePlatform 替身。

    与生产 LinePlatform 接口一致（reply_text / reply_flex / push_text /
    show_loading / get_profile / download_content / close），但每次调用
    都被记录进 list 而非走网络。
    """

    replies: list[dict[str, Any]] = field(default_factory=list)
    flex_replies: list[dict[str, Any]] = field(default_factory=list)
    pushes: list[dict[str, Any]] = field(default_factory=list)
    loadings: list[dict[str, Any]] = field(default_factory=list)
    profile_calls: list[str] = field(default_factory=list)

    async def reply_text(self, *, reply_token: str, texts: list[str]) -> None:
        self.replies.append({"reply_token": reply_token, "texts": list(texts)})

    async def reply_flex(
        self, *, reply_token: str, alt_text: str, contents: dict[str, Any]
    ) -> None:
        self.flex_replies.append(
            {"reply_token": reply_token, "alt_text": alt_text, "contents": contents}
        )

    async def push_text(self, *, to: str, texts: list[str]) -> None:
        self.pushes.append({"to": to, "texts": list(texts)})

    async def show_loading(self, *, chat_id: str, seconds: int = 30) -> None:
        self.loadings.append({"chat_id": chat_id, "seconds": seconds})

    async def get_profile(self, *, user_id: str) -> dict[str, Any] | None:
        self.profile_calls.append(user_id)
        return {"displayName": f"Tester-{user_id[:4]}"}

    async def download_content(self, *, message_id: str) -> bytes | None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_platform(monkeypatch) -> FakeLinePlatform:
    """Override get_line_platform DI + reset singleton cache."""
    fp = FakeLinePlatform()
    app.dependency_overrides[webhook_module.get_line_platform] = lambda: fp
    # 重置惰性 singleton —— 否则非 override 路径会拿到旧的真实 client
    monkeypatch.setattr(webhook_module, "_platform_singleton", None)
    yield fp
    app.dependency_overrides.pop(webhook_module.get_line_platform, None)


# ─── line settings (channel secret etc.) ──────────────────────


@pytest.fixture
def line_settings(monkeypatch) -> None:
    """Plug fixed test secrets into the live `settings` singleton so the
    webhook signature verifier knows what key to expect."""
    from infrastructure.config import settings as live_settings

    monkeypatch.setattr(live_settings, "LINE_CHANNEL_SECRET", LINE_TEST_SECRET)
    monkeypatch.setattr(live_settings, "LINE_CHANNEL_ACCESS_TOKEN", LINE_TEST_TOKEN)
    monkeypatch.setattr(live_settings, "LINE_CHANNEL_ID", "default")
    monkeypatch.setattr(live_settings, "LINE_BIND_BASE_URL", "http://localhost:3002")
    monkeypatch.setattr(live_settings, "LINE_DISABLE_SIGNATURE_VERIFICATION", False)


# ─── stub_agent ───────────────────────────────────────────────


@pytest.fixture
def stub_agent(monkeypatch) -> dict[str, Any]:
    """Replace agent_runner.run_for_line with a recorder/stub.

    Returns a state dict — tests can mutate `state["reply"]` before posting
    to control what the agent returns, and read `state["calls"]` after to
    verify which user / role / text the agent was invoked with.
    """
    state: dict[str, Any] = {"calls": [], "reply": "SECTION8 STUB REPLY"}

    def _stub(**kwargs: Any) -> str:
        state["calls"].append(kwargs)
        return state["reply"]

    from apps.line import agent_runner
    from apps.line import handlers as handlers_module

    monkeypatch.setattr(agent_runner, "run_for_line", _stub)
    monkeypatch.setattr(handlers_module.agent_runner, "run_for_line", _stub)

    return state


# ─── Webhook payload + signature helpers ──────────────────────


@dataclass
class WebhookCall:
    body: bytes
    signature: str


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def make_webhook_payload(events: list[dict[str, Any]]) -> WebhookCall:
    """Build raw JSON body + correct HMAC signature for `events`."""
    body = json.dumps({"events": events}, ensure_ascii=False).encode("utf-8")
    return WebhookCall(body=body, signature=_sign(body, LINE_TEST_SECRET))


def make_dm_text_event(
    *,
    text: str,
    user_id: str = "Uabc123",
    event_id: str = "evt-1",
    reply_token: str = "rt-1",
) -> dict[str, Any]:
    return {
        "type": "message",
        "webhookEventId": event_id,
        "replyToken": reply_token,
        "source": {"type": "user", "userId": user_id},
        "message": {"type": "text", "id": "m-1", "text": text},
    }


def make_group_text_event(
    *,
    text: str,
    group_id: str = "Cgroup1",
    user_id: str = "Uabc123",
    event_id: str = "evt-grp-1",
    reply_token: str = "rt-grp-1",
) -> dict[str, Any]:
    return {
        "type": "message",
        "webhookEventId": event_id,
        "replyToken": reply_token,
        "source": {"type": "group", "groupId": group_id, "userId": user_id},
        "message": {"type": "text", "id": "m-1", "text": text},
    }


def make_group_follow_event(
    *,
    group_id: str = "Cgroup1",
    user_id: str = "Uabc123",
    event_id: str = "evt-grp-follow-1",
    reply_token: str = "rt-grp-follow-1",
) -> dict[str, Any]:
    """A group `follow` / join shouldn't trigger a reply (would spam group)."""
    return {
        "type": "follow",
        "webhookEventId": event_id,
        "replyToken": reply_token,
        "source": {"type": "group", "groupId": group_id, "userId": user_id},
    }


def make_follow_event(
    *,
    user_id: str = "Uabc123",
    event_id: str = "evt-follow-1",
    reply_token: str = "rt-follow-1",
) -> dict[str, Any]:
    return {
        "type": "follow",
        "webhookEventId": event_id,
        "replyToken": reply_token,
        "source": {"type": "user", "userId": user_id},
    }


def make_image_event(
    *,
    user_id: str = "Uabc123",
    event_id: str = "evt-img-1",
    reply_token: str = "rt-img-1",
) -> dict[str, Any]:
    return {
        "type": "message",
        "webhookEventId": event_id,
        "replyToken": reply_token,
        "source": {"type": "user", "userId": user_id},
        "message": {"type": "image", "id": "img-1"},
    }


def make_file_event(
    *,
    user_id: str = "Uabc123",
    event_id: str = "evt-file-1",
    reply_token: str = "rt-file-1",
) -> dict[str, Any]:
    return {
        "type": "message",
        "webhookEventId": event_id,
        "replyToken": reply_token,
        "source": {"type": "user", "userId": user_id},
        "message": {"type": "file", "id": "f-1", "fileName": "thing.pdf"},
    }


def post_webhook(
    client: TestClient, *, payload: WebhookCall, signature: str | None = None
) -> Any:
    """POST raw bytes + X-Line-Signature header; allow override for negative tests."""
    return client.post(
        "/line/webhook",
        content=payload.body,
        headers={
            "X-Line-Signature": (
                signature if signature is not None else payload.signature
            ),
            "Content-Type": "application/json",
        },
    )
