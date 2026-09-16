"""Section 8 — apps/line/identity.build_bind_url: bind URL construction.

测试目标：
    把 LINE_BIND_BASE_URL + plaintext token 拼成绑定链接，URL 安全 —
    末尾斜杠规范化、特殊字符正确 percent-encode。

为什么重要：
    这条链接是用户在 LINE 上唯一看到的东西，写错了用户进不了绑定页。
    Token 是 urlsafe base64 不太可能含特殊字符，但 build_bind_url 不应
    假设输入永远干净（防御性 contract）。

设计方法：
    纯函数，零依赖。每个 assert 一个具体行为：协议、末尾斜杠、URL-encoding。
"""

from __future__ import annotations

from apps.line.identity import build_bind_url


def test_build_url_includes_token_query_param() -> None:
    """基本契约：base + /line/bind?token=<token>。"""
    url = build_bind_url(base_url="http://localhost:3002", token="abc123")
    assert url == "http://localhost:3002/line/bind?token=abc123"


def test_build_url_strips_trailing_slash_from_base() -> None:
    """无论 base_url 是否带 `/`，结果路径只有一个 `/line/bind`。"""
    with_slash = build_bind_url(base_url="https://app.example.com/", token="t")
    without_slash = build_bind_url(base_url="https://app.example.com", token="t")
    assert with_slash == without_slash
    assert "//line/bind" not in with_slash


def test_build_url_preserves_https_scheme() -> None:
    """HTTPS 必须原样保留 —— LINE 生产强制 HTTPS。"""
    url = build_bind_url(base_url="https://app.example.com", token="abc")
    assert url.startswith("https://app.example.com/line/bind?")


def test_build_url_url_encodes_special_chars_in_token() -> None:
    """防御性：万一 token 里出现 `/` `+` `=`（旧版 base64 风格），
    必须 percent-encode 而不是裸塞进 query string。"""
    url = build_bind_url(base_url="https://x.com", token="a/b+c=")
    # urlencode 把 / → %2F, + → %2B, = → %3D
    assert "token=a%2Fb%2Bc%3D" in url
    # 原字符不能出现在 query 部分
    assert "token=a/b+c=" not in url
