"""Pure currency conversion — no DB, no network.

Callers (compute_pnl, what_if agent tool, Excel exporter) all share
this single function. The DB-touching wrapper lives in service.py and
loads the rate snapshot once per request before passing it in here.

Design choices:

  - **Direct pair first**, USD-pivot fallback second. The
    `v2_exchange_rates` table stores from→to pairs (665 rows in prod,
    populated by auto-fetch from open.er-api.com daily). For any
    real-world request the direct pair almost always exists; the pivot
    is for exotic chains we haven't refreshed yet.

  - **Same-currency passthrough returns the input unchanged**. Don't
    look up CNY→CNY in the table — it's both wasteful and a likely
    source of "missing rate" warnings on what's tautologically 1.0.

  - **Failures return None + reason**, never raise. P&L computation
    should degrade gracefully (skip the row + flag a warning) rather
    than abort the entire view because one stale currency couldn't
    convert. Callers decide whether to surface or swallow.

  - **No decimal/rounding here.** Return raw float. Display layer
    rounds. Aggregation in compute_pnl preserves precision through the
    chain.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FxRate:
    """One direction of a currency pair, as loaded from `v2_exchange_rates`."""

    from_currency: str
    to_currency: str
    rate: float


@dataclass(frozen=True)
class ConversionResult:
    """Outcome of a `convert()` call. Always populated — `amount` is
    the input on same-currency or rate-not-found paths; `ok` + `reason`
    let callers detect and surface the latter."""

    amount: float
    ok: bool
    rate_used: float
    via_pivot: str | None  # "USD" if pivot path was taken; None otherwise
    reason: str  # human-readable; "" on success


# Currencies we attempt as a pivot when no direct pair exists. USD is
# universal; EUR is a fallback if the source lacks USD (rare for our
# table since open.er-api.com is USD-rooted, but defensive).
_PIVOT_CANDIDATES = ("USD", "EUR")


def _norm(ccy: str | None) -> str:
    return (ccy or "").strip().upper()


def _index(rates: list[FxRate]) -> dict[tuple[str, str], float]:
    """One-pass build of an O(1) lookup. Last entry per pair wins —
    `service.load_rates_for_conversion` already filters to latest per
    pair, but de-duplicate here too for safety."""
    out: dict[tuple[str, str], float] = {}
    for r in rates:
        key = (_norm(r.from_currency), _norm(r.to_currency))
        if r.rate and r.rate > 0:
            out[key] = float(r.rate)
    return out


def convert(
    amount: float,
    from_currency: str,
    to_currency: str,
    rates: list[FxRate],
) -> ConversionResult:
    """Convert `amount` from `from_currency` to `to_currency` using the
    supplied rate snapshot. See module docstring for design notes.
    """
    src = _norm(from_currency)
    dst = _norm(to_currency)

    if not src or not dst:
        return ConversionResult(amount, False, 1.0, None, "missing currency code")

    if src == dst:
        return ConversionResult(amount, True, 1.0, None, "")

    idx = _index(rates)

    # Direct pair
    direct = idx.get((src, dst))
    if direct is not None:
        return ConversionResult(amount * direct, True, direct, None, "")

    # Inverse pair (we have to→from, invert it). open.er-api.com gives
    # USD-rooted so e.g. CNY→USD may exist but not USD→CNY directly.
    inverse = idx.get((dst, src))
    if inverse is not None and inverse != 0:
        rate = 1.0 / inverse
        return ConversionResult(amount * rate, True, rate, None, "")

    # Pivot via USD/EUR
    for pivot in _PIVOT_CANDIDATES:
        if pivot in (src, dst):
            continue  # already handled by direct/inverse above
        src_to_pivot = idx.get((src, pivot))
        if src_to_pivot is None:
            inv = idx.get((pivot, src))
            src_to_pivot = (1.0 / inv) if inv and inv != 0 else None
        pivot_to_dst = idx.get((pivot, dst))
        if pivot_to_dst is None:
            inv = idx.get((dst, pivot))
            pivot_to_dst = (1.0 / inv) if inv and inv != 0 else None
        if src_to_pivot is not None and pivot_to_dst is not None:
            rate = src_to_pivot * pivot_to_dst
            return ConversionResult(amount * rate, True, rate, pivot, "")

    return ConversionResult(
        amount,
        False,
        1.0,
        None,
        f"no rate available for {src}->{dst} (tried direct, inverse, USD/EUR pivot)",
    )
