"""Web tools — search + fetch. Toolset: "web"."""

from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser

import requests

from ..tools import ToolContext, tool


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "iframe"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "iframe") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0 and data.strip():
            self.chunks.append(data.strip())


def _html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        return html
    return re.sub(r"\s+", " ", " ".join(p.chunks)).strip()


@tool(toolset="web", requires_env=["SERPER_API_KEY"], emoji="🔍")
def web_search(query: str, num_results: int = 5) -> str:
    """Search the web via Serper (Google). Use this for up-to-date facts, docs, or anything you're unsure about.

    Args:
        query: The search query, written like a Google search.
        num_results: How many organic results to return (1-10).
    """
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        return "[tool-error] SERPER_API_KEY not set"
    n = max(1, min(10, int(num_results)))
    try:
        r = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            data=json.dumps({"q": query, "num": n}),
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        return f"[tool-error] search failed: {e}"

    lines: list[str] = []
    if ans := data.get("answerBox"):
        text = ans.get("answer") or ans.get("snippet") or ""
        if text:
            lines.append(f"ANSWER BOX: {text}")
    if kg := data.get("knowledgeGraph"):
        desc = kg.get("description") or ""
        if desc:
            lines.append(f"KNOWLEDGE: {kg.get('title','')} — {desc}")
    for i, hit in enumerate(data.get("organic", [])[:n], 1):
        lines.append(
            f"[{i}] {hit.get('title','')}\n"
            f"    {hit.get('link','')}\n"
            f"    {hit.get('snippet','')}"
        )
    return "\n".join(lines) if lines else "(no results)"


@tool(toolset="web", emoji="🌐")
def web_fetch(url: str, max_chars: int = 6000) -> str:
    """Fetch a URL and return its text content (HTML stripped).

    Use after `web_search` to get full page detail.

    Size cap behavior (IMPORTANT — read before deciding `max_chars`):
      - Default cap is **6000 chars** to keep typical chat context lean.
      - Most weather / news / e-commerce pages are 30K-100K chars after
        HTML strip. The default WILL truncate them.
      - If you need a FULL hourly forecast, complete product listing,
        full article, etc., pass `max_chars=20000` or `40000`.
      - When the response was truncated, the last line of the output
        is `[TRUNCATED at N chars — call again with max_chars=<larger>
        to read more]`. If you see this and the user asked for
        completeness (24 hours, all rows, full article), you MUST
        call again with a larger cap before answering.

    Args:
        url: Full URL including scheme.
        max_chars: Cap on returned text. Default 6000 (chat-friendly).
            Use 20000 for "give me full data" tasks, 40000 for very
            long articles.
    """
    try:
        r = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36 general-agent/0.2"
                )
            },
            allow_redirects=True,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return f"[tool-error] fetch failed: {e}"
    ctype = r.headers.get("content-type", "").lower()
    text = _html_to_text(r.text) if "html" in ctype else r.text
    cap = max(500, int(max_chars))
    if len(text) > cap:
        # Hard signal to the agent: truncation occurred, here's how to
        # recover. Empirically (prod session 2026-05-19) agents that
        # got silently-truncated text proceeded to summarize the partial
        # data without noticing — wasted the user's request for full
        # coverage. The marker turns the silent failure into a loud one.
        suggested_next = min(40000, cap * 4)
        return (
            text[:cap]
            + f"\n\n[TRUNCATED at {cap} chars of {len(text)} total. "
            + f"Call web_fetch again with max_chars={suggested_next} "
            + "if the user asked for full / complete data.]"
        )
    return text
