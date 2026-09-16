"""Section 8 — webhook DM event handling: bound / unbound / image / file / reset / blocked.

测试目标：
    POST /line/webhook 收到 DM 消息时按这几条路径正确分流：
    - 未绑定 + text → 回复 bind URL，**不调用** agent
    - 未绑定 + follow → welcome + bind URL
    - 已绑定 + text → 调 agent，把回复发回 LINE
    - 已绑定 + image → 礼貌"暂不支持"，不调 agent
    - 已绑定 + file → 礼貌"暂不支持"，不调 agent
    - 已绑定 + "新对话" → 关闭活跃会话，回复"已为您开始一段新对话"
    - 已绑定但 is_blocked → 静默丢弃，零回复
    最后断言 HTTP response 本身是 200 + valid JSON（新增覆盖）。

为什么重要：
    DM handler 是 LINE 入口最核心的代码 —— 一个分支走错要么用户收不到 reply
    （让 LINE 看起来挂了），要么 agent 被未授权用户触发（权限泄漏）。
    image/file 安全短路尤其关键：当前 agent 没有图片/文件解析能力，让它处理
    会浪费 token 还可能给错误答案。

设计方法：
    用 FakeLinePlatform 替 LinePlatform、stub_agent 替 run_for_line。
    每个测试：seed user → bind LINE id → post webhook → 断言
    fake_platform / stub_agent / DB 状态。
"""

from __future__ import annotations

from domains.identity.models import User
from domains.line import service as line_service
from domains.line.models import LineUser
from infrastructure.security import hash_password

from test_v2.section_8_line.integration.conftest import (
    make_dm_text_event,
    make_file_event,
    make_follow_event,
    make_image_event,
    make_webhook_payload,
    post_webhook,
)


# ─── helpers (kept inline so tests are self-contained) ────────


def _seed_user(session_factory, *, email: str = "u@x.com", role: str = "employee") -> int:
    db = session_factory()
    try:
        u = User(
            email=email,
            hashed_password=hash_password("password123"),
            full_name=email.split("@")[0],
            role=role,
            is_active=True,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _bind(session_factory, *, line_user_id: str, internal_user_id: int) -> None:
    db = session_factory()
    try:
        line_service.bind_user(
            db,
            line_user_id=line_user_id,
            channel_id="default",
            internal_user_id=internal_user_id,
        )
    finally:
        db.close()


# ─── Unbound user paths ───────────────────────────────────────


def test_unbound_user_text_replies_with_bind_url_and_skips_agent(
    client, line_settings, fake_platform, stub_agent
) -> None:
    payload = make_webhook_payload([make_dm_text_event(text="hi", user_id="Unew")])
    r = post_webhook(client, payload=payload)
    assert r.status_code == 200

    # 一条回复 + 包含 bind 链接
    assert len(fake_platform.replies) == 1
    msg = fake_platform.replies[0]["texts"][0]
    assert "绑定" in msg
    assert "/line/bind?token=" in msg
    # 未绑定用户绝不能触发 agent —— 否则未授权也能查公司数据
    assert stub_agent["calls"] == []


def test_unbound_user_follow_replies_with_welcome_plus_bind(
    client, line_settings, fake_platform, stub_agent
) -> None:
    """follow 事件用更友好的 welcome 文案，但仍带 bind URL。"""
    payload = make_webhook_payload([make_follow_event(user_id="Unew")])
    r = post_webhook(client, payload=payload)
    assert r.status_code == 200
    msg = fake_platform.replies[0]["texts"][0]
    assert "欢迎" in msg
    assert "/line/bind?token=" in msg


# ─── Bound user paths ─────────────────────────────────────────


def test_bound_user_text_invokes_agent_and_relays_reply(
    client, line_settings, fake_platform, session_factory, stub_agent
) -> None:
    uid = _seed_user(session_factory)
    _bind(session_factory, line_user_id="Uabc", internal_user_id=uid)

    payload = make_webhook_payload(
        [make_dm_text_event(text="list my orders", user_id="Uabc")]
    )
    r = post_webhook(client, payload=payload)
    assert r.status_code == 200

    # agent 被调用一次，参数透传
    assert len(stub_agent["calls"]) == 1
    call = stub_agent["calls"][0]
    assert call["user_id"] == uid
    assert call["user_role"] == "employee"
    assert call["text"] == "list my orders"

    # 把 stub 的回复送回 LINE
    assert len(fake_platform.replies) == 1
    assert fake_platform.replies[0]["texts"] == ["SECTION8 STUB REPLY"]


def test_bound_user_image_message_polite_reply_without_agent(
    client, line_settings, fake_platform, session_factory, stub_agent
) -> None:
    uid = _seed_user(session_factory)
    _bind(session_factory, line_user_id="Uabc", internal_user_id=uid)

    payload = make_webhook_payload([make_image_event(user_id="Uabc")])
    r = post_webhook(client, payload=payload)
    assert r.status_code == 200

    # 图片消息走"暂不支持"短路，agent 完全不被调用
    assert "图片解析" in fake_platform.replies[0]["texts"][0]
    assert stub_agent["calls"] == []


def test_bound_user_file_message_polite_reply_without_agent(
    client, line_settings, fake_platform, session_factory, stub_agent
) -> None:
    uid = _seed_user(session_factory)
    _bind(session_factory, line_user_id="Uabc", internal_user_id=uid)

    payload = make_webhook_payload([make_file_event(user_id="Uabc")])
    r = post_webhook(client, payload=payload)
    assert r.status_code == 200

    assert "文件解析" in fake_platform.replies[0]["texts"][0]
    assert stub_agent["calls"] == []


def test_bound_user_reset_keyword_closes_session_and_acks(
    client, line_settings, fake_platform, session_factory, stub_agent
) -> None:
    """`新对话` → handler 把活跃 ChatSession 关闭、回复"已为您开始一段新对话"，
    不调 agent。"""
    from agent.storage.models import ChatSession

    uid = _seed_user(session_factory)
    _bind(session_factory, line_user_id="Uabc", internal_user_id=uid)

    # 预置一个 active 会话，验证它会被关闭
    db = session_factory()
    try:
        existing = ChatSession(
            id="prev-session-1",
            user_id=uid,
            title="LINE 对话",
            status="active",
            platform_type="line",
            platform_user_id="Uabc",
        )
        db.add(existing)
        db.commit()
    finally:
        db.close()

    payload = make_webhook_payload(
        [make_dm_text_event(text="新对话", user_id="Uabc")]
    )
    post_webhook(client, payload=payload)

    # reply 是"已为您开始一段新对话"，agent 未被调
    assert any("新对话" in t for t in fake_platform.replies[0]["texts"])
    assert stub_agent["calls"] == []

    # 预置的会话现在 status='closed'
    db = session_factory()
    try:
        row = db.get(ChatSession, "prev-session-1")
        assert row is not None
        assert row.status == "closed"
        assert row.end_reason == "user_reset"
    finally:
        db.close()


def test_blocked_user_is_silently_dropped(
    client, line_settings, fake_platform, session_factory, stub_agent
) -> None:
    """is_blocked=True → 零回复、零 agent 调用 —— 不暴露 bot 存在。"""
    uid = _seed_user(session_factory)
    _bind(session_factory, line_user_id="Uabc", internal_user_id=uid)
    db = session_factory()
    try:
        lu = db.query(LineUser).filter_by(line_user_id="Uabc").one()
        lu.is_blocked = True
        db.commit()
    finally:
        db.close()

    payload = make_webhook_payload([make_dm_text_event(text="hi", user_id="Uabc")])
    r = post_webhook(client, payload=payload)

    # HTTP 仍然 200（LINE 不重试），但 fake 看到的回复 + agent 调用都为零
    assert r.status_code == 200
    assert fake_platform.replies == []
    assert fake_platform.flex_replies == []
    assert stub_agent["calls"] == []


def test_webhook_http_response_is_200_with_valid_json_envelope(
    client, line_settings, fake_platform, session_factory, stub_agent
) -> None:
    """新增覆盖：HTTP 层响应本身要是 200 + JSON 形如 {"ok": True, "events": N}。

    旧测试只断言 fake_platform 收没收到 reply，没验过 webhook endpoint
    回给 LINE 的 envelope —— 如果哪天 router return 改成 dict 但忘了
    序列化或 status 改成 202，集成测试也该立刻 fail。
    """
    uid = _seed_user(session_factory)
    _bind(session_factory, line_user_id="Uabc", internal_user_id=uid)

    payload = make_webhook_payload([make_dm_text_event(text="hi", user_id="Uabc")])
    r = post_webhook(client, payload=payload)

    # 1. 状态码恰好 200，不是 202 / 204 / 200-but-text
    assert r.status_code == 200
    # 2. 真的是 JSON
    body = r.json()
    assert isinstance(body, dict)
    # 3. envelope shape
    assert body == {"ok": True, "events": 1}
    # 4. Content-Type 是 JSON
    assert r.headers.get("content-type", "").startswith("application/json")
