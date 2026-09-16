"""FastAPI application entry point — the ONE place where layers are wired.

Rules enforced by `scripts/check_arch.py`:
- Only `main.py` may import across domains + agent simultaneously.
- Routers mount at `/api` to match v2's URL contract.
"""

from __future__ import annotations

import logging
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Register LINE domain models so create_all picks them up in tests + so
# Alembic's online migrations have them in scope. No side effects.
import domains.line.models  # noqa: F401, E402

# Registering the bulk image upload models so create_all picks up
# v3_bulk_image_batches + v3_bulk_image_staging in tests.
import domains.masterdata.images.bulk_models  # noqa: F401, E402

# Registering the masterdata upload models so SQLAlchemy creates the
# v3_upload_batches / v3_staging_products / v3_product_changelog tables.
import domains.masterdata.upload.models  # noqa: F401, E402

# Registering the orders domain wires the PO classifier rule + projector
# into the document pipeline. Import for side effects only.
import domains.orders  # noqa: F401, E402
from apps.http.artifacts import router as artifacts_router
from apps.http.auth import router as auth_router
from apps.http.chat import router as chat_router
from apps.http.data_upload import router as data_upload_router
from apps.http.document_folders import router as document_folders_router
from apps.http.documents import router as documents_router
from apps.http.excel import router as excel_router
from apps.http.files import router as files_router
from apps.http.inquiry import router as inquiry_router
from apps.http.internal import router as internal_router
from apps.http.line_bind import router as line_bind_router
from apps.http.masterdata import router as masterdata_router
from apps.http.oracle_scan import router as oracle_scan_router
from apps.http.order_financials import router as order_financials_router
from apps.http.order_groups import router as order_groups_router
from apps.http.orders import router as orders_router
from apps.http.security_context import SecurityContextMiddleware
from apps.http.settings import router as settings_router
from apps.http.settings_phase6_stubs import router as settings_phase6_stubs_router
from apps.http.startup import lifespan
from apps.http.users import router as users_router
from apps.line.webhook import router as line_webhook_router
from infrastructure.config import settings
from infrastructure.log_redaction import install as _install_log_redaction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

_install_log_redaction()

# pydantic-settings reads .env into the `settings` object but does NOT
# export to os.environ. general-agent's LLM client reads keys via
# os.environ directly (e.g. GOOGLE_API_KEY for the Gemini OpenAI-compat
# endpoint), so we propagate the relevant secrets here at startup.
import os as _os  # noqa: E402

for _key in ("GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "SERPER_API_KEY"):
    _val = getattr(settings, _key, "")
    if _val and not _os.environ.get(_key):
        _os.environ[_key] = _val

app = FastAPI(
    title="Cruise Backend v3",
    version="3.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    lifespan=lifespan,
)

# Auth limits are route-scoped in apps/http/auth_limits.py.
app.add_middleware(SecurityContextMiddleware)

# ─── CORS ─────────────────────────────────────────────────────
_dev_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",      # v3-frontend
    "http://localhost:3003",      # mobile-frontend
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:3002",
    "http://127.0.0.1:3003",
]
# Vercel preview deployments for the v3-frontend project land on hosts of the
# form `cruise-v3-frontend-<deployment-hash>-terryzhang-jps-projects.vercel.app`.
# The team slug `terryzhang-jps-projects` is owned by our Vercel team and not
# spoofable from outside, so a regex anchored on it lets every legitimate
# preview through when explicitly configured. This historical example is
# retained for its regex tests; it is NOT an implicit trust rule for a new
# environment. Set CORS_PREVIEW_ORIGIN_REGEX only for a verified target team,
# or enumerate exact preview origins in ALLOWED_ORIGINS.
_PREVIEW_HOST_RE = re.compile(
    r"^https://cruise-v3-frontend-[a-z0-9-]+-terryzhang-jps-projects\.vercel\.app$"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_dev_origins if settings.DEBUG else settings.ALLOWED_ORIGINS,
    allow_origin_regex=None if settings.DEBUG else settings.CORS_PREVIEW_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ─── Routers ──────────────────────────────────────────────────
app.include_router(auth_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(masterdata_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(document_folders_router, prefix="/api")
app.include_router(orders_router, prefix="/api")
app.include_router(oracle_scan_router, prefix="/api")
app.include_router(order_financials_router, prefix="/api")
app.include_router(order_groups_router, prefix="/api")
app.include_router(inquiry_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(settings_phase6_stubs_router, prefix="/api")
app.include_router(excel_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(data_upload_router, prefix="/api")
app.include_router(artifacts_router, prefix="/api")
app.include_router(line_bind_router, prefix="/api")
app.include_router(internal_router, prefix="/api")

# LINE webhook — root mount, no /api prefix. The path is /line/webhook;
# this is what we register in the LINE Developers console.
app.include_router(line_webhook_router)

# File serving — root mount (no /api prefix) to honor LocalFileStorage's
# `/uploads/{key}` contract. See apps/http/files.py for the full rationale.
app.include_router(files_router)


@app.get("/health")
def health() -> dict[str, str]:
    # Cloud Run injects K_SERVICE / K_REVISION automatically. Surfacing
    # them lets ops and users correlate a bug report to the exact
    # deployment without having to grep gcloud logs.
    import os

    return {
        "status": "ok",
        "version": "3.0.0",
        "service": os.getenv("K_SERVICE", "local"),
        "revision": os.getenv("K_REVISION", "local"),
    }
