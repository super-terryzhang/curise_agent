"""Render structured agent output (markdown tables / numbered or bullet lists)
into LINE Flex Message JSON.

Why
---
Our agent often replies with markdown tables or numbered lists when the user
asks for structured info ("列出我的订单"). LINE doesn't render markdown — by
default we'd strip the pipes and dump everything as a single text blob,
which is unreadable on a 5-inch screen.

This module detects two structured shapes and converts them into Flex bubbles:

  1. Markdown table  ─ `| col1 | col2 |` rows with a `|---|---|` separator.
     Rendered as a vertical bubble with header row + data rows.
  2. Numbered/bullet list  ─ 3+ lines starting with `1.`, `-`, `*`, or `•`.
     Rendered as a vertical bubble with one row per item.

Anything else falls through to plain text (the caller decides).

Constraints (mobile-realistic)
------------------------------
- Tables: max 4 columns (wider squeezes to unreadable on phone), max 10 rows.
  More rows get a "still N more — see web UI" footer.
- Lists: max 10 items, same overflow treatment.
- Long cell content is set to `wrap: true` so it line-breaks within its
  column rather than overflowing.
- We never raise — bad input falls back to "plain" detection so the caller
  can route to text-only delivery.

This module has NO LINE SDK or httpx imports. It returns plain dicts; the
caller (`platform.reply_flex`) is responsible for sending.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

logger = logging.getLogger(__name__)


# Hard caps — chosen for mobile readability on a typical LINE bubble width.
MAX_TABLE_COLS: int = 4
MAX_TABLE_ROWS: int = 10
MAX_LIST_ITEMS: int = 10

# Status keywords → color hex. Applied to table cells whose content matches.
# Keeps the visual signal subtle but useful (red = needs attention, green = done).
_STATUS_COLORS: dict[str, str] = {
    "pending": "#f59e0b",      # amber — needs action
    "rematch": "#f59e0b",
    "待审批": "#f59e0b",
    "审批中": "#f59e0b",
    "approved": "#10b981",     # green — done
    "已批准": "#10b981",
    "completed": "#10b981",
    "已完成": "#10b981",
    "rejected": "#ef4444",     # red — bad
    "已拒绝": "#ef4444",
    "failed": "#ef4444",
    "失败": "#ef4444",
    "error": "#ef4444",
}


# ─── Structure detection ──────────────────────────────────────


_TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
# A "list item": starts with `1.` / `1)` / `-` / `*` / `•` followed by space.
_LIST_ITEM_RE = re.compile(r"^\s*(?:\d{1,3}[.)]\s+|[\-\*•]\s+)(\S.*)$")


Structure = Literal["table", "list", "plain"]


def detect_structure(text: str) -> Structure:
    """Classify an agent reply.

    Order matters: table check first (a table can contain pipe chars that
    might also look list-ish to a naive scan), then list check, else plain.
    """
    if not text:
        return "plain"

    if _TABLE_SEP_RE.search(text):
        return "table"

    list_lines = [ln for ln in text.splitlines() if _LIST_ITEM_RE.match(ln)]
    if len(list_lines) >= 3:
        return "list"

    return "plain"


# ─── Table parsing ────────────────────────────────────────────


def parse_markdown_table(text: str) -> tuple[str, list[list[str]], str]:
    """Pull a markdown table out of `text`.

    Returns:
      (prefix_text, rows, suffix_text)
      - prefix_text:  any non-table text BEFORE the table block (often "您最近 5 个订单：")
      - rows:         list of cell lists. rows[0] is the header.
      - suffix_text:  any text AFTER the table

    On parse failure: rows is empty and the whole text goes into prefix_text.
    """
    lines = text.splitlines()
    # Find the separator line ─── this anchors the table.
    sep_idx: int | None = None
    for i, line in enumerate(lines):
        if _TABLE_SEP_RE.match(line):
            sep_idx = i
            break
    if sep_idx is None or sep_idx == 0:
        return text, [], ""

    # Header is the line just before the separator. Walk up to collect any
    # additional pipe-rows above the header (rare in practice).
    header_line = lines[sep_idx - 1]
    if not _TABLE_ROW_RE.match(header_line):
        return text, [], ""

    # Data rows: every subsequent line that's still a pipe row.
    data_start = sep_idx + 1
    data_end = data_start
    while data_end < len(lines) and _TABLE_ROW_RE.match(lines[data_end]):
        data_end += 1

    # Build prefix / suffix from non-table lines.
    prefix_text = "\n".join(lines[: sep_idx - 1]).strip()
    suffix_text = "\n".join(lines[data_end:]).strip()

    # Parse cells. Strip the leading/trailing pipe before splitting so we
    # don't get empty cells from `| a | b |`.
    def _parse_row(line: str) -> list[str]:
        inner = line.strip().strip("|")
        return [c.strip() for c in inner.split("|")]

    rows: list[list[str]] = [_parse_row(header_line)]
    rows.extend(_parse_row(lines[i]) for i in range(data_start, data_end))

    return prefix_text, rows, suffix_text


# ─── List parsing ─────────────────────────────────────────────


def parse_list(text: str) -> tuple[str, list[str], str]:
    """Pull a numbered/bullet list out of `text`.

    Returns (prefix_text, items, suffix_text). Items are stripped of the
    leading marker. Non-list lines around the block go into prefix/suffix.
    """
    lines = text.splitlines()
    first_item: int | None = None
    last_item: int = -1
    for i, line in enumerate(lines):
        if _LIST_ITEM_RE.match(line):
            if first_item is None:
                first_item = i
            last_item = i

    if first_item is None:
        return text, [], ""

    prefix_text = "\n".join(lines[:first_item]).strip()
    suffix_text = "\n".join(lines[last_item + 1 :]).strip()

    items: list[str] = []
    for i in range(first_item, last_item + 1):
        m = _LIST_ITEM_RE.match(lines[i])
        if m:
            items.append(m.group(1).strip())
        else:
            # In-between line that's not a list item — append to the previous
            # item as a continuation (common when items have multi-line desc).
            if items and lines[i].strip():
                items[-1] = items[-1] + " " + lines[i].strip()
    return prefix_text, items, suffix_text


# ─── Flex builders ────────────────────────────────────────────


def table_to_flex(prefix_text: str, rows: list[list[str]], suffix_text: str) -> dict[str, Any]:
    """Build a Flex bubble from parsed table rows.

    rows[0] is the header. Truncates to MAX_TABLE_COLS columns and
    MAX_TABLE_ROWS data rows; adds a "+ N 行省略" footer when truncated.
    """
    if not rows:
        raise ValueError("table_to_flex needs at least 1 row")

    # Cap columns: pick the first MAX_TABLE_COLS from each row.
    n_cols = min(MAX_TABLE_COLS, max(len(r) for r in rows))
    rows = [(r + [""] * n_cols)[:n_cols] for r in rows]

    header_cells, *data_rows = rows
    total_data = len(data_rows)
    truncated = total_data > MAX_TABLE_ROWS
    visible_rows = data_rows[:MAX_TABLE_ROWS]

    # Auto-size columns: give wider flex to columns whose header looks like
    # a name field; numeric / status columns get less space.
    # Heuristic only — for v1, give all cols equal weight. Easy to tune later.
    col_flex = [2] * n_cols

    body_contents: list[dict[str, Any]] = []

    # Header row
    body_contents.append(
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": _truncate_cell(h) or " ",
                    "weight": "bold",
                    "size": "xs",
                    "color": "#6b7280",
                    "flex": col_flex[i],
                    "wrap": True,
                }
                for i, h in enumerate(header_cells)
            ],
        }
    )
    body_contents.append({"type": "separator", "margin": "xs"})

    # Data rows
    for row in visible_rows:
        body_contents.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "margin": "sm",
                "contents": [
                    _table_cell(cell, flex=col_flex[i])
                    for i, cell in enumerate(row)
                ],
            }
        )

    if truncated:
        body_contents.append({"type": "separator", "margin": "sm"})
        body_contents.append(
            {
                "type": "text",
                "text": f"…还有 {total_data - MAX_TABLE_ROWS} 行 — 完整列表请到 Web 端查看",
                "size": "xxs",
                "color": "#9ca3af",
                "margin": "sm",
                "wrap": True,
            }
        )

    bubble: dict[str, Any] = {
        "type": "bubble",
        "size": "mega",  # widest available; tables need it
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": body_contents,
        },
    }

    if prefix_text:
        bubble["header"] = {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": _truncate_cell(prefix_text, max_chars=80),
                    "weight": "bold",
                    "size": "sm",
                    "color": "#111827",
                    "wrap": True,
                }
            ],
            "paddingBottom": "sm",
        }

    if suffix_text:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": _truncate_cell(suffix_text, max_chars=140),
                    "size": "xs",
                    "color": "#6b7280",
                    "wrap": True,
                }
            ],
        }

    return bubble


def list_to_flex(prefix_text: str, items: list[str], suffix_text: str) -> dict[str, Any]:
    """Build a Flex bubble showing a vertical list of items."""
    if not items:
        raise ValueError("list_to_flex needs at least 1 item")

    total = len(items)
    truncated = total > MAX_LIST_ITEMS
    visible = items[:MAX_LIST_ITEMS]

    body_contents: list[dict[str, Any]] = []
    for i, item in enumerate(visible, start=1):
        body_contents.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "margin": "sm" if i > 1 else "none",
                "contents": [
                    {
                        "type": "text",
                        "text": f"{i}.",
                        "size": "sm",
                        "color": "#6b7280",
                        "flex": 0,
                        "weight": "bold",
                    },
                    {
                        "type": "text",
                        "text": _truncate_cell(item, max_chars=200),
                        "size": "sm",
                        "color": "#111827",
                        "flex": 1,
                        "wrap": True,
                    },
                ],
            }
        )

    if truncated:
        body_contents.append({"type": "separator", "margin": "sm"})
        body_contents.append(
            {
                "type": "text",
                "text": f"…还有 {total - MAX_LIST_ITEMS} 项 — 完整列表请到 Web 端查看",
                "size": "xxs",
                "color": "#9ca3af",
                "margin": "sm",
                "wrap": True,
            }
        )

    bubble: dict[str, Any] = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": body_contents,
        },
    }

    if prefix_text:
        bubble["header"] = {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": _truncate_cell(prefix_text, max_chars=80),
                    "weight": "bold",
                    "size": "sm",
                    "color": "#111827",
                    "wrap": True,
                }
            ],
            "paddingBottom": "sm",
        }

    if suffix_text:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": _truncate_cell(suffix_text, max_chars=140),
                    "size": "xs",
                    "color": "#6b7280",
                    "wrap": True,
                }
            ],
        }

    return bubble


# ─── Cell formatting helpers ──────────────────────────────────


def _table_cell(value: str, *, flex: int) -> dict[str, Any]:
    """A single table cell — applies status colors and trimming."""
    text = _truncate_cell(value, max_chars=24) or " "
    color = _status_color_for(value)
    cell: dict[str, Any] = {
        "type": "text",
        "text": text,
        "size": "xs",
        "flex": flex,
        "wrap": True,
        "color": color or "#111827",
    }
    if color:
        # Highlighted status cells get a bit more weight to stand out.
        cell["weight"] = "bold"
    return cell


def _status_color_for(value: str) -> str | None:
    """Return a hex color if the cell content looks like a known status, else None."""
    v = value.strip().lower()
    if not v:
        return None
    for keyword, color in _STATUS_COLORS.items():
        if keyword.lower() in v:
            return color
    return None


def _truncate_cell(value: str, *, max_chars: int = 40) -> str:
    """Strip markdown + trim long cell content with an ellipsis.

    LINE Flex doesn't render markdown — `**bold**`, `*italic*`, `` `code` ``,
    `[label](url)` etc. all appear as literal characters in cells if we don't
    strip them. This applies to every text fragment we put into Flex JSON:
    table cells, list items, header/footer prefix and suffix.
    """
    if value is None:
        return ""
    # Lazy import — delivery.py's format_for_line lazy-imports this module,
    # so importing strip_markdown back from delivery at top of file would
    # work, but lazy keeps load order obvious and zero-cost.
    from apps.line.delivery import strip_markdown

    s = strip_markdown(str(value)).strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


# ─── Top-level entry ─────────────────────────────────────────


def try_render_flex(text: str) -> dict[str, Any] | None:
    """Return a Flex bubble dict if `text` is recognized as structured,
    else None. The caller decides whether to fall back to plain text.

    Never raises — any parsing failure returns None.
    """
    if not text:
        return None
    try:
        kind = detect_structure(text)
        if kind == "table":
            prefix, rows, suffix = parse_markdown_table(text)
            if len(rows) < 2:
                # No data rows — not worth a flex card.
                return None
            return table_to_flex(prefix, rows, suffix)
        if kind == "list":
            prefix, items, suffix = parse_list(text)
            if len(items) < 3:
                return None
            return list_to_flex(prefix, items, suffix)
    except Exception:
        logger.exception("flex_renderer: failed to render — falling back to text")
    return None


def alt_text_for(text: str, *, max_chars: int = 80) -> str:
    """Build the `altText` (used in notification preview / older clients)
    from the agent's reply. Strip pipes / fence markers, trim to length."""
    if not text:
        return "新消息"
    cleaned = re.sub(r"[|`*_#>]+", "", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:max_chars] if len(cleaned) > max_chars else cleaned
