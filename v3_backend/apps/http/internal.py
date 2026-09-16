"""Internal endpoints — `/api/internal/*` — for trusted-caller cron jobs.

Currently exposes a single endpoint that Cloud Scheduler hits daily to
refresh the `v2_exchange_rates` table from open.er-api.com.

## Auth

Cloud Scheduler is configured with an OIDC token whose audience
matches our Cloud Run service URL. Cloud Run automatically validates
the token before forwarding the request (we don't have to verify it
ourselves — the platform does). We just need to make sure no
unauthenticated caller can reach this endpoint, which means it MUST
be hit through the Cloud Run service URL (which always requires
auth) — never through a custom domain that bypasses the platform's
auth layer.

Defense-in-depth: we also check an `X-Internal-Token` header against
an env-supplied shared secret (`INTERNAL_CRON_SECRET`). Belt + braces.
Skip the check entirely in dev (env var unset).

If someone reaches this endpoint without either signal, return 403.

## What it does

Fetches latest rates for a fixed list of currencies our customer base
actually uses (JPY, USD, CNY, AUD, EUR, GBP, KRW, THB, SGD, NZD —
matches the currency dropdown in the frontend). Upserts into
`v2_exchange_rates`. Returns a summary.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from apps.http._deps import DbDep
from domains.masterdata._exchange_rates_service import fetch_exchange_rates
from domains.masterdata.errors import UpstreamUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


_BASE_CURRENCIES = ["USD", "JPY", "CNY", "AUD", "EUR"]
_TARGETS = ["USD", "JPY", "CNY", "AUD", "EUR", "GBP", "KRW", "THB", "SGD", "NZD"]


def _require_internal_auth(x_internal_token: str | None) -> None:
    """Validate the shared-secret header. In dev (unset env) we skip."""
    expected = os.environ.get("INTERNAL_CRON_SECRET", "").strip()
    if not expected:
        # Dev mode — allow without token. Logged so it's visible.
        logger.warning(
            "INTERNAL_CRON_SECRET not set; allowing /api/internal/* without auth"
        )
        return
    if x_internal_token != expected:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "invalid or missing internal token"
        )


@router.post("/fx-refresh")
def fx_refresh(
    db: DbDep,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> dict[str, Any]:
    """Cron-triggered FX refresh. Iterates the base currencies and
    pulls latest pairs for each into `v2_exchange_rates`.

    Tolerant: a single base's failure (upstream 5xx, network blip)
    is logged but doesn't fail the others — partial success beats
    all-or-nothing for daily jobs.
    """
    _require_internal_auth(x_internal_token)

    results: list[dict[str, Any]] = []
    for base in _BASE_CURRENCIES:
        try:
            result = fetch_exchange_rates(db, base=base, targets=_TARGETS)
            results.append(
                {
                    "base": base,
                    "ok": True,
                    "created": result.created,
                    "updated": result.updated,
                    "date": result.date,
                }
            )
        except UpstreamUnavailable as exc:
            logger.exception("fx-refresh failed for base=%s", base)
            results.append({"base": base, "ok": False, "error": str(exc)})

    total_ok = sum(1 for r in results if r["ok"])
    return {
        "summary": f"{total_ok}/{len(results)} bases refreshed",
        "results": results,
    }


@router.post("/bulk-images/gc")
def bulk_image_gc(
    db: DbDep,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
    ttl_hours: int = 24,
    stuck_minutes: int = 15,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Cron-triggered garbage collection for bulk image upload batches.

    Closes two failure modes:
      1. Abandoned batches (`preview_ready`/`uploading`/`error`/
         `cancelled`) whose ZIP is still in GCS after `ttl_hours`.
      2. Stuck batches (`processing` with heartbeat > `stuck_minutes`
         old) — worker died mid-run.

    Both cases: best-effort delete the staged ZIP and close the batch.
    GCS bucket lifecycle (48h prefix TTL) is the hard failsafe.

    `dry_run=true` reports counts without mutating.
    """
    from domains.masterdata.images.bulk_service import sweep_stale_batches

    _require_internal_auth(x_internal_token)
    return sweep_stale_batches(
        db,
        ttl_hours=ttl_hours,
        stuck_minutes=stuck_minutes,
        dry_run=dry_run,
    )
