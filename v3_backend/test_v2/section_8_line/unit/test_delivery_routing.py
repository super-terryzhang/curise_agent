"""Section 8 — apps/line/delivery.py: markdown strip + chunk split + Flex vs text routing.

测试目标：
    delivery 层做三件事：(1) 把 markdown 字符剥离干净避免在 LINE 显示成字面，
    (2) 把超长回复切到 ≤4500 chars × ≤5 messages 的 LINE 协议上限，
    (3) `format_for_line` 根据内容形状选择 FlexReply（结构化）或 TextReply（散文）。

为什么重要：
    LINE 的 5000 char / 5 msg / reply 协议限制是硬性的 —— 不切就被服务端拒收。
    markdown 不剥离用户就看到 `**bold**` 字面字符。route 错了表格变一堆 `|`。

设计方法：
    纯函数测试，无 IO。优先用 parametrize 把同类断言（strip 不同 markdown
    形式、不同 chunk size）合并。FlexReply / TextReply 是 frozen dataclass，
    直接 isinstance 判定 + 字段断言。
"""

from __future__ import annotations

import pytest

from apps.line.delivery import (
    LINE_MAX_MESSAGES_PER_REPLY,
    FlexReply,
    TextReply,
    format_for_line,
    split_for_line,
    strip_markdown,
)


# ─── strip_markdown ───────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("**bold**", "bold"),                                # bold *
        ("__bold__", "bold"),                                # bold _
        ("*italic*", "italic"),                              # italic *
        ("_italic_", "italic"),                              # italic _
        ("use `foo()` here", "use foo() here"),              # inline code
        ("# Heading", "Heading"),                            # h1
        ("### Subhead\nbody", "Subhead\nbody"),              # h3
        ("> quoted", "quoted"),                              # blockquote
    ],
)
def test_strip_removes_markdown_form(raw: str, expected: str) -> None:
    assert strip_markdown(raw) == expected


def test_strip_link_keeps_label_and_url() -> None:
    """`[text](url)` → `text (url)` —— url 仍可被用户点选 / 复制。"""
    assert strip_markdown("[文档](https://x.com/d)") == "文档 (https://x.com/d)"


def test_strip_fenced_code_drops_fence_keeps_body() -> None:
    """三反引号 codeblock：边界去掉，主体保留（技术输出别丢）。"""
    out = strip_markdown("```python\nprint(42)\n```")
    assert "print(42)" in out
    assert "```" not in out


def test_strip_returns_empty_on_empty() -> None:
    """空输入 不能抛 —— 调用方依赖此 contract。"""
    assert strip_markdown("") == ""


def test_strip_is_idempotent_on_clean_text() -> None:
    plain = "完全没有任何 markdown 的普通中英文 mixed text 42."
    assert strip_markdown(plain) == plain


# ─── split_for_line ───────────────────────────────────────────


def test_split_empty_returns_single_empty_chunk() -> None:
    """contract: 调用方可以无条件 chunks[0]，所以空输入也要返回 [""]"""
    assert split_for_line("") == [""]


def test_split_short_text_returns_one_chunk() -> None:
    assert split_for_line("hello") == ["hello"]


def test_split_exactly_at_limit_returns_one_chunk() -> None:
    """正好等于 max_chars → 不切。"""
    text = "x" * 4500
    chunks = split_for_line(text)
    assert chunks == [text]


def test_split_above_limit_creates_multiple_chunks() -> None:
    text = "a" * 9000
    chunks = split_for_line(text)
    assert len(chunks) == 2
    assert all(len(c) <= 4500 for c in chunks)


def test_split_prefers_paragraph_boundary_when_available() -> None:
    """有 `\\n\\n` 边界在 limit//2 之后 → 切在那里 而不是硬截。"""
    body = ("a" * 3000) + "\n\n" + ("b" * 3000)
    chunks = split_for_line(body, max_chars=4500)
    assert len(chunks) == 2
    assert chunks[0].endswith("a")
    assert chunks[1].startswith("b")


def test_split_caps_at_max_messages_with_truncation_marker() -> None:
    """超过 5 条上限 → 最后一条追加"已省略"标记。"""
    huge = "x" * (4500 * 8)
    chunks = split_for_line(huge)
    assert len(chunks) == LINE_MAX_MESSAGES_PER_REPLY
    # 兜底 marker 必须出现
    assert "省略" in chunks[-1] or "网页" in chunks[-1]


def test_split_every_chunk_respects_limit_for_huge_input() -> None:
    """极长输入下，每个 chunk 仍 ≤ max_chars —— LINE 拒收超长消息。"""
    text = ("word " * 8000).rstrip()
    for chunk in split_for_line(text):
        assert len(chunk) <= 4500


# ─── format_for_line (top-level dispatch) ─────────────────────


def test_format_returns_flex_for_table_input() -> None:
    text = (
        "您的订单：\n\n"
        "| 订单号 | 状态 |\n"
        "|--------|------|\n"
        "| 100 | pending |\n"
        "| 101 | approved |\n"
    )
    reply = format_for_line(text)
    assert isinstance(reply, FlexReply)
    assert reply.contents["type"] == "bubble"
    assert reply.alt_text  # 非空 —— LINE 要求


def test_format_returns_text_for_plain_prose() -> None:
    text = "你好，有什么我可以帮助的？"
    reply = format_for_line(text)
    assert isinstance(reply, TextReply)
    assert reply.chunks == ["你好，有什么我可以帮助的？"]


def test_format_empty_input_returns_text_with_one_chunk() -> None:
    """空输入 → TextReply 而非崩溃，handler 还能 reply 至少一条消息。"""
    reply = format_for_line("")
    assert isinstance(reply, TextReply)
    assert reply.chunks == [""]


def test_format_long_text_returns_chunked_text_reply() -> None:
    """超长散文：仍走 text，chunks 已切好。"""
    text = "段落一。" + ("a" * 5000) + "\n\n段落二。" + ("b" * 5000)
    reply = format_for_line(text)
    assert isinstance(reply, TextReply)
    assert len(reply.chunks) >= 2
    for chunk in reply.chunks:
        assert len(chunk) <= 4500


def test_format_table_input_strips_markdown_in_cells() -> None:
    """Regression: 表格单元格里的 **bold** 不应原样进入 Flex JSON。"""
    import json

    text = (
        "| # | Status |\n"
        "|---|--------|\n"
        "| **1** | **pending** |\n"
        "| 2 | approved |\n"
    )
    reply = format_for_line(text)
    assert isinstance(reply, FlexReply)
    flat = json.dumps(reply.contents, ensure_ascii=False)
    assert "**" not in flat
