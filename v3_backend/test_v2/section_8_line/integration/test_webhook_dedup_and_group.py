"""Section 8 — webhook idempotency + group-chat rejection paths.

测试目标：
    - 同一 event_id 投递两次 → agent 只被触发一次（LINE 在超时/5xx 时会重试）。
    - 两条不同 event_id → 两次都处理。
    - 群聊 text 消息 → 回复"群聊功能即将上线"，**不调** agent。
    - 群聊 follow / join 事件 → 静默（不刷屏每个加群事件）。
    - 群里来的未绑定用户消息 → 不应生成 bind token（群里没法 OAuth）。

为什么重要：
    LINE 重试机制会导致重复事件；不去重就会重复扣 LLM token 和重复写 DB。
    群聊在 Phase L1 不支持，但 handler 不能因此变成静默无回应（用户体验差）；
    同时 follow/join 事件不能被当成 message 处理（会发垃圾信息）。
    群里发 bind URL 是浪费 —— 群成员不是同一个 LINE user，链接对群没意义。

设计方法：
    LINE 重试用同一个签名 payload 发两次 —— webhook 内部走 `record_event_id`
    去重；后一次直接 skip。group event 用 `source.type = "group"` 构造。
"""

from __future__ import annotations

from domains.identity.models import User
from domains.line import service as line_service
from domains.line.models import LineBindToken
from infrastructure.security import hash_password

from test_v2.section_8_line.integration.conftest import (
    make_dm_text_event,
    make_group_follow_event,
    make_group_text_event,
    make_webhook_payload,
    post_webhook,
)


def _seed_and_bind(session_factory, *, line_user_id: str = "Uabc") -> int:
    """Seed an employee + bind a LINE id to them. Returns internal user_id."""
    db = session_factory()
    try:
        u = User(
            email="u@x.com",
            hashed_password=hash_password("password123"),
            full_name="t",
            role="employee",
            is_active=True,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        uid = u.id
    finally:
        db.close()
    db = session_factory()
    try:
        line_service.bind_user(
            db,
            line_user_id=line_user_id,
            channel_id="default",
            internal_user_id=uid,
        )
    finally:
        db.close()
    return uid


# ─── Idempotency ──────────────────────────────────────────────


def test_duplicate_event_id_processed_exactly_once(
    client, line_settings, fake_platform, session_factory, stub_agent
) -> None:
    """同一条 webhook payload 发两次 —— 第二次走 record_event_id 去重，
    agent 只被调一次，fake_platform 只看到一次 reply。"""
    _seed_and_bind(session_factory, line_user_id="Uabc")

    payload = make_webhook_payload(
        [make_dm_text_event(text="hi", user_id="Uabc", event_id="evt-dup")]
    )
    post_webhook(client, payload=payload)
    assert len(stub_agent["calls"]) == 1
    assert len(fake_platform.replies) == 1

    # 重发完全同样的 payload —— 模拟 LINE 重试
    post_webhook(client, payload=payload)
    # 计数不变
    assert len(stub_agent["calls"]) == 1
    assert len(fake_platform.replies) == 1


def test_distinct_event_ids_both_processed(
    client, line_settings, fake_platform, session_factory, stub_agent
) -> None:
    """两条不同 event_id —— 不应被任何去重逻辑误杀。"""
    _seed_and_bind(session_factory, line_user_id="Uabc")

    p1 = make_webhook_payload(
        [
            make_dm_text_event(
                text="first", user_id="Uabc", event_id="evt-A", reply_token="rt-A"
            )
        ]
    )
    p2 = make_webhook_payload(
        [
            make_dm_text_event(
                text="second", user_id="Uabc", event_id="evt-B", reply_token="rt-B"
            )
        ]
    )
    post_webhook(client, payload=p1)
    post_webhook(client, payload=p2)

    assert len(stub_agent["calls"]) == 2
    assert len(fake_platform.replies) == 2
    # 顺序保留
    assert stub_agent["calls"][0]["text"] == "first"
    assert stub_agent["calls"][1]["text"] == "second"


# ─── Group paths ──────────────────────────────────────────────


def test_group_text_message_replies_with_not_yet_supported(
    client, line_settings, fake_platform, stub_agent
) -> None:
    """群里 @bot —— 当前 phase 礼貌"群聊功能即将上线"，不调 agent。"""
    payload = make_webhook_payload(
        [make_group_text_event(text="@bot what's up", group_id="Cg1", user_id="Uabc")]
    )
    r = post_webhook(client, payload=payload)
    assert r.status_code == 200
    assert len(fake_platform.replies) == 1
    assert "群聊" in fake_platform.replies[0]["texts"][0]
    assert stub_agent["calls"] == []


def test_group_follow_event_produces_no_reply(
    client, line_settings, fake_platform, stub_agent
) -> None:
    """群里发生 follow / join 不应回复 —— 否则机器人每加群就刷屏。"""
    payload = make_webhook_payload([make_group_follow_event(group_id="Cg1")])
    r = post_webhook(client, payload=payload)
    assert r.status_code == 200
    assert fake_platform.replies == []
    assert fake_platform.flex_replies == []
    assert stub_agent["calls"] == []


def test_unbound_user_in_group_does_not_generate_bind_token(
    client, line_settings, fake_platform, session_factory, stub_agent
) -> None:
    """群里的未绑定 sender —— 不应在 v3_line_bind_tokens 留行；群没法走 OAuth。"""
    payload = make_webhook_payload(
        [make_group_text_event(text="hi", user_id="Unobody-in-group")]
    )
    r = post_webhook(client, payload=payload)
    assert r.status_code == 200

    db = session_factory()
    try:
        assert db.query(LineBindToken).count() == 0
    finally:
        db.close()
