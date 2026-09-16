"""Message-delivery utilities — pure functions, no LINE SDK imports.

Two jobs:
1. `strip_markdown` — remove markdown formatting that LINE doesn't render.
   The chat agent emits markdown happily (web frontend renders it); LINE's
   plain text view shows the literal `**foo**`, which looks broken.
2. `split_for_line` — chunk long text into LINE-shaped pieces. LINE caps
   each text message at 5000 chars and at most 5 messages per reply call.

Splitting strategy:
- Try paragraph boundaries first (`\\n\\n`).
- Fall back to single newlines.
- Fall back to space.
- Last resort: hard cut at the char limit.

We never produce more than `max_messages` chunks — anything beyond gets
truncated and we append a "（更多内容已省略）" marker so the user knows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# LINE protocol limits.
LINE_MAX_TEXT_CHARS: int = 5000
LINE_MAX_MESSAGES_PER_REPLY: int = 5

# We leave a small safety margin under LINE_MAX_TEXT_CHARS to allow for
# the multi-byte char accounting that LINE may apply differently than us.
_DEFAULT_CHUNK_LIMIT: int = 4500

# Markdown patterns we strip. Order matters — fenced code first so its
# inner ** isn't re-processed by the bold pattern.
_FENCE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_STAR_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_BOLD_UNDER_RE = re.compile(r"__([^_\n]+)__")
_ITALIC_STAR_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_ITALIC_UNDER_RE = re.compile(r"(?<!_)_([^_\n]+)_(?!_)")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s+", re.MULTILINE)


def strip_markdown(text: str) -> str:
    """Remove the markdown LINE doesn't render.

    Codeblocks are kept (verbatim, fence removed) so that long technical
    output isn't lost. Links become `text (url)` so the URL is still tappable.
    """
    if not text:
        return text

    def _fence_replace(m: re.Match[str]) -> str:
        body = m.group(0)
        # Strip the opening ```lang and closing ```. Keep contents verbatim.
        body = re.sub(r"^```[a-zA-Z0-9_+\-]*\n?", "", body)
        body = re.sub(r"\n?```$", "", body)
        return body

    text = _FENCE_BLOCK_RE.sub(_fence_replace, text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _BOLD_STAR_RE.sub(r"\1", text)
    text = _BOLD_UNDER_RE.sub(r"\1", text)
    text = _ITALIC_STAR_RE.sub(r"\1", text)
    text = _ITALIC_UNDER_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1 (\2)", text)
    text = _HEADER_RE.sub("", text)
    text = _BLOCKQUOTE_RE.sub("", text)
    return text


def split_for_line(
    text: str,
    *,
    max_chars: int = _DEFAULT_CHUNK_LIMIT,
    max_messages: int = LINE_MAX_MESSAGES_PER_REPLY,
) -> list[str]:
    """Chunk `text` into ≤ `max_messages` strings, each ≤ `max_chars`.

    Returns at least one element (possibly the empty string `""` if the
    input was empty — caller decides whether to send it).
    """
    if not text:
        return [""]

    text = text.rstrip()
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    truncation_marker = "\n\n（更多内容已省略，请到网页查看完整答案）"

    while remaining and len(chunks) < max_messages:
        if len(chunks) == max_messages - 1 and len(remaining) > max_chars:
            # Last allowed chunk — leave room for the truncation marker.
            cut_limit = max_chars - len(truncation_marker)
            head = _split_at_boundary(remaining, cut_limit)
            chunks.append(head + truncation_marker)
            remaining = ""
            break

        if len(remaining) <= max_chars:
            chunks.append(remaining)
            remaining = ""
            break

        head = _split_at_boundary(remaining, max_chars)
        chunks.append(head)
        # Skip leading whitespace on the next chunk for readability.
        remaining = remaining[len(head):].lstrip()

    return chunks or [""]


def _split_at_boundary(text: str, limit: int) -> str:
    """Pick the longest prefix ≤ `limit` that ends at a sensible boundary."""
    if len(text) <= limit:
        return text

    # Try paragraph break first.
    cut = text.rfind("\n\n", 0, limit + 1)
    if cut > limit // 2:
        return text[:cut]

    # Single newline.
    cut = text.rfind("\n", 0, limit + 1)
    if cut > limit // 2:
        return text[:cut]

    # Word boundary.
    cut = text.rfind(" ", 0, limit + 1)
    if cut > limit // 2:
        return text[:cut]

    # Hard cut.
    return text[:limit]


# ─── Top-level dispatch: Flex vs text ─────────────────────────


@dataclass(frozen=True)
class FlexReply:
    """Wrap a single Flex bubble payload + the alt-text shown in notifications."""

    alt_text: str
    contents: dict[str, Any]


@dataclass(frozen=True)
class TextReply:
    """Wrap text chunks already split into LINE-shaped pieces."""

    chunks: list[str]


def format_for_line(text: str) -> FlexReply | TextReply:
    """Decide how to deliver an agent reply.

    If the reply contains a markdown table or 3+ item list, build a Flex
    bubble. Otherwise strip markdown and split into LINE-sized chunks.

    This is the ONLY place that picks the rendering route — handlers call
    this and then call `platform.reply_flex` or `platform.reply_text`
    based on the return type.
    """
    # Lazy import — keeps delivery.py importable without flex_renderer
    # transitively pulling SDK-adjacent deps.
    from apps.line.flex_renderer import alt_text_for, try_render_flex

    bubble = try_render_flex(text)
    if bubble is not None:
        return FlexReply(alt_text=alt_text_for(text), contents=bubble)

    cleaned = strip_markdown(text or "")
    chunks = split_for_line(cleaned)
    return TextReply(chunks=chunks)
