"""Pure-function tests for the FX converter.

No DB. No fixtures. Just feed FxRate lists in, assert ConversionResult
out. If these go red, callers that depend on convert() (compute_pnl,
the agent tools, the Excel exporter) will all be wrong simultaneously
in subtle ways, so keep these tests above suspicion of clever testing
tricks — they're the rosetta stone for the whole financials module.
"""

from __future__ import annotations

from domains.orders.financials.currency import FxRate, convert


def _rates(*pairs: tuple[str, str, float]) -> list[FxRate]:
    return [FxRate(f, t, r) for (f, t, r) in pairs]


def test_same_currency_passthrough():
    r = convert(100.0, "JPY", "JPY", [])
    assert r.ok and r.amount == 100.0 and r.rate_used == 1.0 and r.via_pivot is None


def test_direct_pair_wins():
    r = convert(100.0, "USD", "JPY", _rates(("USD", "JPY", 150.0)))
    assert r.ok and r.amount == 15_000.0 and r.rate_used == 150.0


def test_inverse_pair_when_only_reverse_exists():
    # Table has CNY->USD but caller wants USD->CNY
    r = convert(100.0, "USD", "CNY", _rates(("CNY", "USD", 0.14)))
    assert r.ok
    # 100 / 0.14 ≈ 714.29
    assert abs(r.amount - (100.0 / 0.14)) < 1e-6
    assert r.via_pivot is None


def test_pivot_via_usd():
    # CNY->JPY needs both CNY->USD and USD->JPY (or inverses)
    r = convert(
        100.0,
        "CNY",
        "JPY",
        _rates(("CNY", "USD", 0.14), ("USD", "JPY", 150.0)),
    )
    assert r.ok and r.via_pivot == "USD"
    assert abs(r.amount - 100.0 * 0.14 * 150.0) < 1e-6


def test_pivot_via_usd_uses_inverses_too():
    # Only USD->CNY and JPY->USD exist; we need CNY->JPY
    r = convert(
        100.0,
        "CNY",
        "JPY",
        _rates(("USD", "CNY", 7.1), ("JPY", "USD", 1 / 150.0)),
    )
    assert r.ok and r.via_pivot == "USD"
    # CNY->USD = 1/7.1; USD->JPY = 1/(1/150) = 150
    expected = 100.0 * (1 / 7.1) * 150.0
    assert abs(r.amount - expected) < 1e-6


def test_no_rate_available_returns_not_ok_with_reason():
    r = convert(100.0, "XOF", "MZN", _rates(("USD", "JPY", 150.0)))
    assert not r.ok
    assert r.amount == 100.0  # passthrough on failure — caller decides
    assert "XOF" in r.reason and "MZN" in r.reason


def test_missing_currency_code_fails_cleanly():
    r = convert(100.0, "", "JPY", _rates(("USD", "JPY", 150.0)))
    assert not r.ok and "missing currency" in r.reason
    r2 = convert(100.0, "USD", None, _rates(("USD", "JPY", 150.0)))  # type: ignore[arg-type]
    assert not r2.ok


def test_case_insensitive_currency_codes():
    r = convert(100.0, "usd", "jpy", _rates(("USD", "JPY", 150.0)))
    assert r.ok and r.amount == 15_000.0


def test_zero_or_negative_rate_in_table_is_ignored():
    # Bad data (0 or negative rate) should not produce divisions or
    # silently zero-out conversions. Treat as missing.
    r = convert(100.0, "USD", "JPY", _rates(("USD", "JPY", 0.0)))
    assert not r.ok
    r2 = convert(100.0, "USD", "JPY", _rates(("USD", "JPY", -150.0)))
    assert not r2.ok


def test_duplicate_pair_last_wins():
    # Service-layer code that pre-filters to latest-per-pair is the
    # ideal contract, but the pure converter should still handle the
    # case where two rows for the same pair sneak in.
    r = convert(
        100.0,
        "USD",
        "JPY",
        _rates(("USD", "JPY", 100.0), ("USD", "JPY", 150.0)),
    )
    assert r.ok and r.rate_used == 150.0
