"""Section 8 — POST /line/webhook signature gate (HTTP layer).

测试目标：
    webhook 入口 *第一道闸*：没有 X-Line-Signature 或签名错误时必须 401。
    覆盖正常路径、缺失头、错误签名、改了 body 后签名仍是旧的、空 channel secret
    fail-closed —— 这一组合穷尽了攻击面。

为什么重要：
    没有这个 gate，任何人都能 POST /line/webhook 假冒 LINE 触发处理逻辑，
    用 stub_agent 看起来是无害文本，生产 agent 会调真实工具改数据库。
    更险恶：空 secret 时如果默认 pass，那一次配置事故就把整个 endpoint 开放。

设计方法：
    在 HTTP 层断言 status code —— 测的是 webhook router 本身，不需要 stub_agent。
    `make_webhook_payload` 生成对的 body+sig；负面测试覆盖签名错配的几种组合。
"""

from __future__ import annotations

from test_v2.section_8_line.integration.conftest import (
    make_dm_text_event,
    make_webhook_payload,
    post_webhook,
)


def test_missing_signature_header_returns_401(client, line_settings, fake_platform) -> None:
    """没有 X-Line-Signature header → 401。"""
    payload = make_webhook_payload([make_dm_text_event(text="hi")])
    r = client.post(
        "/line/webhook",
        content=payload.body,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401


def test_wrong_signature_returns_401(client, line_settings, fake_platform) -> None:
    """签名头存在但值错误 → 401。"""
    payload = make_webhook_payload([make_dm_text_event(text="hi")])
    r = post_webhook(client, payload=payload, signature="cZ29tZW9uZS1lbHNlcw==")
    assert r.status_code == 401


def test_tampered_body_with_original_signature_returns_401(
    client, line_settings, fake_platform
) -> None:
    """关键安全性：改一字节 body 但带原签名 —— 必须 401（重放/MITM 防护）。"""
    payload = make_webhook_payload([make_dm_text_event(text="hi")])
    tampered = payload.body.replace(b"hi", b"HI", 1)
    assert tampered != payload.body  # sanity
    r = client.post(
        "/line/webhook",
        content=tampered,
        headers={
            "X-Line-Signature": payload.signature,  # 旧 body 的签名
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401


def test_correct_signature_returns_200(client, line_settings, fake_platform, stub_agent) -> None:
    """合法签名 → 200，response body 形如 {"ok": True, "events": N}。"""
    payload = make_webhook_payload([make_dm_text_event(text="hi")])
    r = post_webhook(client, payload=payload)
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "events": 1}


def test_correct_signature_with_empty_events_array_still_200(
    client, line_settings, fake_platform
) -> None:
    """LINE 偶尔发空 events（如健康检查），正常 200 + events: 0。"""
    payload = make_webhook_payload([])
    r = post_webhook(client, payload=payload)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "events": 0}


def test_empty_channel_secret_rejects_all_requests(
    client, fake_platform, monkeypatch
) -> None:
    """fail-closed: LINE_CHANNEL_SECRET 未配置时所有请求 401 —— 配置事故
    不应等同于"开放端点"。"""
    from infrastructure.config import settings as live_settings

    monkeypatch.setattr(live_settings, "LINE_CHANNEL_SECRET", "")
    monkeypatch.setattr(live_settings, "LINE_CHANNEL_ACCESS_TOKEN", "anything")
    monkeypatch.setattr(live_settings, "LINE_DISABLE_SIGNATURE_VERIFICATION", False)
    payload = make_webhook_payload([make_dm_text_event(text="hi")])
    r = post_webhook(client, payload=payload)
    assert r.status_code == 401
