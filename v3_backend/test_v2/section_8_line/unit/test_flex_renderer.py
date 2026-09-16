"""Section 8 — apps/line/flex_renderer.py: structure detection + Flex JSON building.

测试目标：
    渲染层将 agent 的 markdown 文本（表格 / 列表 / 普通段落）正确分类，
    并产出 LINE Flex Message JSON 结构 — header / body / footer 都符合
    手机屏幕宽度的硬限制（≤4 列、≤10 行 / 项），状态颜色映射正确，
    长内容截断，markdown 字符在 Flex 输出中被剥离干净。

为什么重要：
    LINE 不渲染 markdown — 没这一层，用户看到的是字面的 `**bold**` 和
    管道符 `|`，订单列表完全不可读。这是 2026-05 修过的一次回归。
    渲染层 fail-soft 至关重要：任何解析异常必须落到 None 让上层走纯文本，
    不能向用户抛错。

设计方法：
    纯函数 — 没有 LINE SDK / httpx / DB 调用。直接断言返回值与 JSON 形状。
    对参数化场景（多种 markdown 形式、状态颜色映射）用 pytest.mark.parametrize
    避免重复样板。每个测试只验证一个行为。
"""

from __future__ import annotations

import json

import pytest

from apps.line.flex_renderer import (
    MAX_LIST_ITEMS,
    MAX_TABLE_COLS,
    MAX_TABLE_ROWS,
    alt_text_for,
    detect_structure,
    list_to_flex,
    parse_list,
    parse_markdown_table,
    table_to_flex,
    try_render_flex,
)


# ─── detect_structure ─────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "",
        "您好，有什么需要帮助？",
        "1. only one item\n",                # 仅 1 个列表项，不算 list
        "1. first\n2. second\n",             # 2 项，未达 3 项门槛
        "just a single sentence with no structure.",
    ],
)
def test_detect_returns_plain_for_unstructured_text(text: str) -> None:
    """不满足表格或列表门槛 → plain（上层会走 strip_markdown + split_for_line）。"""
    assert detect_structure(text) == "plain"


def test_detect_table_via_separator_line() -> None:
    """分隔行 `|---|---|` 是表格识别的唯一锚点。"""
    text = (
        "您最近的订单：\n\n"
        "| 订单号 | 状态 |\n"
        "|--------|------|\n"
        "| A1 | pending |\n"
    )
    assert detect_structure(text) == "table"


def test_detect_table_with_alignment_markers_in_separator() -> None:
    """`:---:` / `:---` 是合法 markdown 表格对齐语法，必须仍然被识别。"""
    text = "| A | B |\n|:---|---:|\n| 1 | 2 |\n"
    assert detect_structure(text) == "table"


@pytest.mark.parametrize(
    "text",
    [
        "1. one\n2. two\n3. three\n",
        "- a\n- b\n- c\n",
        "* x\n* y\n* z\n",
        "• alpha\n• beta\n• gamma\n",
    ],
)
def test_detect_list_for_3_plus_items(text: str) -> None:
    """3+ 项的有序 / 项目符号列表都识别为 list。"""
    assert detect_structure(text) == "list"


def test_detect_table_takes_precedence_over_list_shape() -> None:
    """既看起来像表格又看起来像列表时，表格优先 — 形状更明确。"""
    text = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n"
    assert detect_structure(text) == "table"


# ─── parse_markdown_table ─────────────────────────────────────


def test_parse_table_extracts_prefix_rows_suffix() -> None:
    """前后非表格文本进入 prefix / suffix，数据行从分隔线下开始。"""
    text = (
        "前导说明：\n\n"
        "| Order | Customer | Status |\n"
        "|-------|----------|--------|\n"
        "| 100 | ACME | pending |\n"
        "| 101 | BETA | approved |\n\n"
        "末尾注释"
    )
    prefix, rows, suffix = parse_markdown_table(text)
    assert prefix == "前导说明："
    assert rows[0] == ["Order", "Customer", "Status"]
    assert rows[1] == ["100", "ACME", "pending"]
    assert rows[2] == ["101", "BETA", "approved"]
    assert "末尾注释" in suffix


def test_parse_table_with_no_separator_returns_empty_rows() -> None:
    """无分隔行 → 不是表格，rows 空，原文进 prefix。"""
    prefix, rows, suffix = parse_markdown_table("just prose")
    assert rows == []
    assert prefix == "just prose"
    assert suffix == ""


def test_parse_table_strips_leading_indent_on_pipe_rows() -> None:
    """带前导空格的 pipe row 也是合法 markdown 表格。"""
    text = "   | A | B |\n   |---|---|\n   | 1 | 2 |\n"
    _, rows, _ = parse_markdown_table(text)
    assert rows == [["A", "B"], ["1", "2"]]


# ─── parse_list ───────────────────────────────────────────────


def test_parse_list_strips_markers() -> None:
    """列表项的 `1.` / `-` 标记被剥离，只保留文本内容。"""
    text = "找到 3 个产品：\n\n1. Alpha\n2. Beta\n3. Gamma\n"
    prefix, items, suffix = parse_list(text)
    assert prefix == "找到 3 个产品："
    assert items == ["Alpha", "Beta", "Gamma"]


def test_parse_list_folds_continuation_into_previous_item() -> None:
    """非列表项的中间行附到上一项 — agent 经常输出多行描述。"""
    text = "1. First item\n   with extra detail\n2. Second item\n"
    _, items, _ = parse_list(text)
    assert "First item" in items[0]
    assert "extra detail" in items[0]
    assert items[1] == "Second item"


# ─── table_to_flex ────────────────────────────────────────────


def test_table_to_flex_header_and_data_rows_present() -> None:
    rows = [["#", "Customer"], ["1", "ACME"], ["2", "BETA"]]
    bubble = table_to_flex("My orders", rows, "footer note")
    assert bubble["type"] == "bubble"
    # Prefix → header
    assert bubble["header"]["contents"][0]["text"] == "My orders"
    # Suffix → footer
    assert bubble["footer"]["contents"][0]["text"] == "footer note"
    body = bubble["body"]["contents"]
    # 至少：header row + separator + 2 data rows
    assert len(body) >= 4


def test_table_to_flex_caps_column_count() -> None:
    """超过 MAX_TABLE_COLS 列的表只保留前 N 列，避免手机上被压扁。"""
    wide_header = [f"col{i}" for i in range(MAX_TABLE_COLS + 5)]
    wide_row = [f"v{i}" for i in range(MAX_TABLE_COLS + 5)]
    bubble = table_to_flex("", [wide_header, wide_row], "")
    header_row = bubble["body"]["contents"][0]
    assert len(header_row["contents"]) == MAX_TABLE_COLS


def test_table_to_flex_caps_rows_and_emits_overflow_note() -> None:
    """超过 MAX_TABLE_ROWS 数据行 → 截断 + 末尾追加省略说明。"""
    header = ["#"]
    rows = [header] + [[str(i)] for i in range(MAX_TABLE_ROWS + 7)]
    bubble = table_to_flex("", rows, "")
    body = bubble["body"]["contents"]
    overflow = [c for c in body if c.get("type") == "text" and "还有" in c.get("text", "")]
    assert len(overflow) == 1
    assert "7" in overflow[0]["text"]


@pytest.mark.parametrize(
    "keyword,expected_hex",
    [
        ("pending", "#f59e0b"),
        ("rematch", "#f59e0b"),
        ("待审批", "#f59e0b"),
        ("approved", "#10b981"),
        ("已完成", "#10b981"),
        ("rejected", "#ef4444"),
        ("failed", "#ef4444"),
        ("失败", "#ef4444"),
    ],
)
def test_table_status_cell_gets_color(keyword: str, expected_hex: str) -> None:
    """状态关键字（中英文都覆盖）→ 单元格染色 + 加粗。"""
    rows = [["#", "Status"], ["1", keyword]]
    bubble = table_to_flex("", rows, "")
    data_row = bubble["body"]["contents"][2]  # header row + separator + first data row
    status_cell = data_row["contents"][1]
    assert status_cell["color"] == expected_hex
    assert status_cell.get("weight") == "bold"


def test_table_cell_truncated_with_ellipsis_when_too_long() -> None:
    """长单元格 → 截到 24 字符 + `…`，不让一行撑爆。"""
    long_text = "this is a very long cell value that should certainly be truncated"
    rows = [["A"], [long_text]]
    bubble = table_to_flex("", rows, "")
    cell_text = bubble["body"]["contents"][2]["contents"][0]["text"]
    assert "…" in cell_text
    assert len(cell_text) <= 25  # 24 chars + ellipsis


def test_table_to_flex_rejects_empty_rows() -> None:
    """空 rows 是退化输入 — 由 try_render_flex 提前拦截，直接调用应抛错。"""
    with pytest.raises(ValueError):
        table_to_flex("", [], "")


# ─── list_to_flex ─────────────────────────────────────────────


def test_list_to_flex_outputs_one_box_per_item() -> None:
    bubble = list_to_flex("Top items", ["A", "B", "C"], "")
    assert bubble["type"] == "bubble"
    assert bubble["header"]["contents"][0]["text"] == "Top items"
    # body 内的 box 数应该等于 items 数（未触发省略）
    body = bubble["body"]["contents"]
    box_items = [c for c in body if c.get("type") == "box"]
    assert len(box_items) == 3


def test_list_to_flex_caps_and_emits_overflow_note() -> None:
    """超过 MAX_LIST_ITEMS 项 → 截断 + 在末尾追加省略说明。"""
    items = [f"item-{i}" for i in range(MAX_LIST_ITEMS + 4)]
    bubble = list_to_flex("", items, "")
    body = bubble["body"]["contents"]
    overflow = [c for c in body if c.get("type") == "text" and "还有" in c.get("text", "")]
    assert len(overflow) == 1
    assert "4" in overflow[0]["text"]


def test_list_to_flex_rejects_empty_items() -> None:
    with pytest.raises(ValueError):
        list_to_flex("", [], "")


# ─── try_render_flex (top-level happy + fallback) ─────────────


def test_try_render_returns_bubble_for_well_formed_table() -> None:
    text = "Title\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
    bubble = try_render_flex(text)
    assert bubble is not None
    assert bubble["type"] == "bubble"


def test_try_render_returns_bubble_for_well_formed_list() -> None:
    text = "Pick one:\n\n1. Alpha\n2. Beta\n3. Gamma\n"
    bubble = try_render_flex(text)
    assert bubble is not None
    assert bubble["type"] == "bubble"


@pytest.mark.parametrize(
    "text",
    [
        "",                                       # 空
        "just a plain sentence",                  # 纯文本
        "| A | B |\n|---|---|\n",                 # 表格但无数据行
        "1. only one\n",                          # 列表只 1 项
    ],
)
def test_try_render_returns_none_for_non_structured(text: str) -> None:
    """所有不值得 Flex 的情况都让上层 fallback 到纯文本。"""
    assert try_render_flex(text) is None


def test_try_render_swallows_parse_exceptions() -> None:
    """诡异输入不能让 try_render_flex 把异常抛给 webhook handler。"""
    weird = ("| \x00 | \xff |\n|---|---|\n" + "| a | b |\n") * 50
    out = try_render_flex(weird)
    # 不论返回 dict 还是 None，关键是不抛异常。
    assert out is None or isinstance(out, dict)


def test_try_render_strips_markdown_inside_table_cells() -> None:
    """Regression: agent 在单元格内放 **bold** — Flex 不渲染 markdown,
    必须在产出 JSON 时就剥离掉。"""
    text = (
        "您的订单：\n\n"
        "| 订单号 | 状态 |\n"
        "|--------|------|\n"
        "| **100** | **pending** |\n"
        "| 101 | approved |\n"
    )
    bubble = try_render_flex(text)
    flat = json.dumps(bubble, ensure_ascii=False)
    assert "**" not in flat, f"残留 ** in: {flat[:200]}"
    # 内容本身保留
    assert "100" in flat
    assert "pending" in flat


# ─── alt_text_for ─────────────────────────────────────────────


def test_alt_text_for_table_strips_pipes_and_markdown() -> None:
    raw = "**Orders:**\n| # | A |\n|---|---|\n| 1 | foo |"
    alt = alt_text_for(raw)
    assert "|" not in alt
    assert "*" not in alt
    assert "Orders" in alt


def test_alt_text_for_list_returns_readable_summary() -> None:
    raw = "Items:\n- alpha\n- beta\n- gamma"
    alt = alt_text_for(raw)
    # `-` 不在过滤集合内但首字符不应保留 markdown 标记；至少内容可读
    assert "alpha" in alt
    assert "*" not in alt
    assert "|" not in alt


def test_alt_text_for_plain_text_is_passthrough() -> None:
    raw = "你好，有什么可以帮助的？"
    alt = alt_text_for(raw)
    assert alt == raw


def test_alt_text_for_empty_returns_default_label() -> None:
    """LINE 拒绝空 altText — 必须有兜底字符串。"""
    assert alt_text_for("") == "新消息"


def test_alt_text_truncated_to_max_chars() -> None:
    raw = "x" * 200
    alt = alt_text_for(raw, max_chars=40)
    assert len(alt) == 40
