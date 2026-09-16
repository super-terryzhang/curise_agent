"""Pin web_fetch's truncation marker contract.

Why: prod 2026-05-19 — agent asked for hourly weather, web_fetch returned
6000 chars (default cap), weather page was 30K+. Agent processed the
partial content as if it were complete; never noticed truncation; gave
the user 6 hours instead of 24. The fix: append an explicit
`[TRUNCATED at N chars — call again with max_chars=<larger>]` marker
when truncation occurs, with the next-step hint inlined. Tests pin:

  1. Marker appears IFF response exceeds cap
  2. Marker tells agent the exact next-call parameter
  3. Default 6000 cap is documented (don't change defaults silently —
     the system prompt now teaches agent to override when needed)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_agent.toolkit.web import web_fetch


class _FakeResponse:
    def __init__(self, text: str, content_type: str = "text/html; charset=utf-8"):
        self.text = text
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        return None


def _fake_get(text: str):
    return lambda url, **kw: _FakeResponse(text)


def test_web_fetch_no_truncation_for_short_content():
    """Short content (< cap) returns as-is, no marker appended."""
    body = "<html><body><p>Short content here.</p></body></html>"
    with patch("general_agent.toolkit.web.requests.get", _fake_get(body)):
        out = web_fetch("http://example.com")
    assert "TRUNCATED" not in out
    assert "Short content" in out


def test_web_fetch_appends_truncation_marker_when_capped():
    """Long content gets sliced to max_chars + marker telling agent how
    to recover. The marker is the BRIDGE that turns silent failure into
    a loud recovery prompt."""
    # Build content guaranteed to exceed default 6000 chars
    body = "<html><body>" + ("Hourly forecast row. " * 1000) + "</body></html>"
    with patch("general_agent.toolkit.web.requests.get", _fake_get(body)):
        out = web_fetch("http://example.com")
    assert "TRUNCATED" in out
    assert "max_chars" in out
    # Must tell agent the EXACT parameter to use next (not vague advice)
    assert "max_chars=" in out


def test_web_fetch_truncation_marker_includes_total_length():
    """Agent needs to know how MUCH was hidden so it can decide if
    one more fetch is enough or if it needs a different strategy
    (e.g. paginate, narrow query)."""
    body = "<html><body>" + ("X" * 50_000) + "</body></html>"
    with patch("general_agent.toolkit.web.requests.get", _fake_get(body)):
        out = web_fetch("http://example.com", max_chars=6000)
    assert "TRUNCATED" in out
    # The total length should be mentioned so agent knows the gap size
    assert "of " in out  # "TRUNCATED at 6000 chars of N total"


def test_web_fetch_custom_max_chars_respected():
    """User explicitly raised max_chars — confirm the cap moves."""
    body = "<html><body>" + ("Y" * 10_000) + "</body></html>"
    with patch("general_agent.toolkit.web.requests.get", _fake_get(body)):
        small = web_fetch("http://example.com", max_chars=2000)
        big = web_fetch("http://example.com", max_chars=15000)
    assert len(small) < len(big)
    # Small one truncates; big one doesn't
    assert "TRUNCATED" in small
    assert "TRUNCATED" not in big


def test_web_fetch_failure_keeps_old_error_shape():
    """Don't regress the existing [tool-error] prefix that callers rely
    on to detect HTTP / network failures."""
    import requests

    def _fail(url, **kw):
        raise requests.exceptions.RequestException("boom")

    with patch("general_agent.toolkit.web.requests.get", _fail):
        out = web_fetch("http://example.com")
    assert out.startswith("[tool-error]")
    assert "boom" in out
