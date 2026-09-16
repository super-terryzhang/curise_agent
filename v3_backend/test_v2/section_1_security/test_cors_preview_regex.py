"""Section 1 — explicitly opted-in historical CORS preview-URL regex.

2026-09-08: production no longer enables this historical team pattern by default.
These tests validate the optional pattern when explicitly selected; the main app
uses CORS_PREVIEW_ORIGIN_REGEX. Default isolation is tested in test_session_security.py.

测试目标:
    Vercel preview 部署每次生成随机 host (`cruise-v3-frontend-<hash>-terryzhang-jps-projects.vercel.app`),
    后端 ALLOWED_ORIGINS 是精确列表无法覆盖,所以我们用一个锚定团队 slug 的正则
    `allow_origin_regex` 一次性放行所有合法 preview,同时拒绝看起来相似但 host 不属于
    我们 Vercel 团队的伪造域名。

为什么重要:
    1. 每发一次 preview 就要改 Cloud Run env vars 是机械活,工程效率差。
    2. 但 CORS 过松 → 浏览器在恶意页面里携带用户 cookie 调我们的 API → CSRF 风险。
       正则必须是 "scale-up of safety", 不是 "trade-off of safety"。

测试维度:
    A. 纯模式匹配 (pure regex, no FastAPI) — 锚点、字符集、长度边界。
    B. 真实 CORSMiddleware 集成 (minimal FastAPI app with the same allow rule)
       — 模拟浏览器 preflight, 断言 access-control-allow-origin 头的存在/缺失。

设计方法:
    不依赖 main.app fixture (conftest 把 ENV=development 锁死了, 跑出来是 DEBUG=True 的
    allow_origins 分支), 而是把 main.py 里的正则常量 `_PREVIEW_HOST_RE` 抽出来,
    用一个本地构造的 minimal FastAPI 显式启用历史规则,
    不是 dev fallback。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from main import _PREVIEW_HOST_RE  # noqa: E402

# ─── A. Pure regex contract ─────────────────────────────────────


class TestPreviewRegexShape:
    """Pin the regex pattern. If you change `_PREVIEW_HOST_RE` and one of these
    fails, ask: did I just open a CORS hole, or did I really mean to?"""

    @pytest.mark.parametrize(
        "origin",
        [
            # Random hash deploys (the bread-and-butter preview shape).
            "https://cruise-v3-frontend-7vehsu7du-terryzhang-jps-projects.vercel.app",
            "https://cruise-v3-frontend-o3g4y2q6l-terryzhang-jps-projects.vercel.app",
            # Branch-based preview (Vercel uses lowercased branch slug).
            "https://cruise-v3-frontend-git-feature-foo-terryzhang-jps-projects.vercel.app",
            # PR-based preview slug.
            "https://cruise-v3-frontend-pr-123-test-terryzhang-jps-projects.vercel.app",
            # Single-char hash (theoretical minimum).
            "https://cruise-v3-frontend-a-terryzhang-jps-projects.vercel.app",
        ],
    )
    def test_legitimate_preview_origins_match(self, origin: str) -> None:
        assert _PREVIEW_HOST_RE.fullmatch(origin) is not None, (
            f"Expected regex to allow legitimate Vercel preview {origin!r}"
        )

    @pytest.mark.parametrize(
        "origin",
        [
            # Wrong scheme — never allow plain HTTP for CORS-credentialed requests.
            "http://cruise-v3-frontend-abc-terryzhang-jps-projects.vercel.app",
            # Attacker spoof: looks like our project but lives on a different TLD.
            "https://cruise-v3-frontend-abc-terryzhang-jps-projects.attacker.com",
            # Attacker spoof: hyphenated host as a subdomain trick.
            "https://cruise-v3-frontend-abc-terryzhang-jps-projects.vercel.app.attacker.com",
            # Different Vercel team — `terryzhang-jps-projects` is OUR team slug.
            "https://cruise-v3-frontend-abc-other-team.vercel.app",
            # Wrong project, same team — production-frontend is allowed elsewhere
            # via explicit allow-list; this regex is ONLY for preview hosts of
            # cruise-v3-frontend.
            "https://other-project-abc-terryzhang-jps-projects.vercel.app",
            # Path injection attempt (Origin header should never carry a path,
            # but our regex still needs `$` anchor).
            "https://cruise-v3-frontend-abc-terryzhang-jps-projects.vercel.app/admin",
            # Query-string injection.
            "https://cruise-v3-frontend-abc-terryzhang-jps-projects.vercel.app?x=1",
            # Production URL — NOT a preview. Production is allowed via the
            # explicit list, never via this regex.
            "https://cruise-v3-frontend.vercel.app",
            # Empty hash.
            "https://cruise-v3-frontend--terryzhang-jps-projects.vercel.app",
            # Uppercase smuggling (Vercel lowercases all preview hostnames).
            "https://cruise-v3-frontend-ABC-terryzhang-jps-projects.vercel.app",
            # Localhost (dev origin is handled by a different code path).
            "http://localhost:3002",
        ],
    )
    def test_illegitimate_origins_do_not_match(self, origin: str) -> None:
        assert _PREVIEW_HOST_RE.fullmatch(origin) is None, (
            f"Regex unexpectedly allowed {origin!r} — CORS hole?"
        )


# ─── B. Live CORSMiddleware integration ─────────────────────────


def _make_prod_like_app() -> FastAPI:
    """A FastAPI app wired with the SAME CORS rules as production main.py.

    We don't reuse `main.app` because conftest forces ENV=development, which
    activates the dev-only `_dev_origins` branch. Production goes through
    `settings.ALLOWED_ORIGINS` (explicit list) + `_PREVIEW_HOST_RE` (regex).
    To test the production branch we replicate it here. If main.py's
    middleware call changes, this helper has to follow.
    """
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://cruise-v3-frontend.vercel.app",
            "https://cruise-v3-mobile.vercel.app",
        ],
        allow_origin_regex=_PREVIEW_HOST_RE.pattern,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/ping")
    def _ping() -> dict[str, bool]:
        return {"ok": True}

    return app


def _preflight(client: TestClient, origin: str) -> tuple[int, str | None]:
    """Send a CORS preflight; return (status, allow-origin header or None)."""
    resp = client.options(
        "/ping",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    return resp.status_code, resp.headers.get("access-control-allow-origin")


@pytest.fixture
def prod_client() -> TestClient:
    return TestClient(_make_prod_like_app())


class TestCorsLiveBehavior:
    """End-to-end via TestClient: the regex must actually take effect through
    Starlette's CORSMiddleware, not just match in isolation."""

    def test_legitimate_preview_url_gets_allow_origin_header(
        self, prod_client: TestClient
    ) -> None:
        origin = (
            "https://cruise-v3-frontend-abc123-terryzhang-jps-projects.vercel.app"
        )
        status, allow = _preflight(prod_client, origin)
        assert status == 200
        assert allow == origin, (
            f"Preview origin should be echoed back exactly; got {allow!r}"
        )

    def test_production_url_still_allowed_via_explicit_list(
        self, prod_client: TestClient
    ) -> None:
        """Regression guard: adding regex must NOT break the explicit list."""
        origin = "https://cruise-v3-frontend.vercel.app"
        status, allow = _preflight(prod_client, origin)
        assert status == 200
        assert allow == origin

    def test_mobile_url_still_allowed_via_explicit_list(
        self, prod_client: TestClient
    ) -> None:
        origin = "https://cruise-v3-mobile.vercel.app"
        status, allow = _preflight(prod_client, origin)
        assert status == 200
        assert allow == origin

    @pytest.mark.parametrize(
        "evil_origin",
        [
            "https://cruise-v3-frontend-abc-terryzhang-jps-projects.attacker.com",
            "https://cruise-v3-frontend-abc-other-team.vercel.app",
            "http://cruise-v3-frontend-abc-terryzhang-jps-projects.vercel.app",  # http
            "https://attacker.com",
        ],
    )
    def test_attacker_origins_do_not_get_allow_origin_header(
        self, prod_client: TestClient, evil_origin: str
    ) -> None:
        """Starlette returns 400 OR returns 200 without the allow-origin header,
        depending on version. Either is safe — the browser only accepts the
        response when the header matches. We assert: header is absent or
        differs from the evil origin."""
        _status, allow = _preflight(prod_client, evil_origin)
        assert allow != evil_origin, (
            f"CORS hole: attacker origin {evil_origin!r} got echoed back"
        )

    def test_actual_request_after_preflight_carries_allow_origin(
        self, prod_client: TestClient
    ) -> None:
        """A real GET after preflight must also carry allow-origin so the browser
        delivers the body to JS. Preflight passing alone isn't enough."""
        origin = (
            "https://cruise-v3-frontend-deadbeef-terryzhang-jps-projects.vercel.app"
        )
        resp = prod_client.get("/ping", headers={"Origin": origin})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == origin
        assert resp.json() == {"ok": True}
