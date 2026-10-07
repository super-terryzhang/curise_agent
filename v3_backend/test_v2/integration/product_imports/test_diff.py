from datetime import date
from decimal import Decimal

from domains.product_imports.diff import (
    apply_sparse_patch,
    compare_price_period,
    normalize_product_key,
)


def test_product_key_ignores_code_case_and_edge_whitespace_but_keeps_port():
    assert normalize_product_key(" AbC ", 3) == ("abc", 3)
    assert normalize_product_key("ABC", 4) != normalize_product_key("abc", 3)


def test_sparse_patch_keeps_blanks_and_only_clear_marker_deletes():
    assert apply_sparse_patch(
        {"name": "APPLE", "brand": "OLD", "unit": "KG"},
        {"name": "", "brand": "__CLEAR__", "unit": "CT"},
    ) == {"name": "APPLE", "brand": None, "unit": "CT"}


def test_exact_price_bounds_skip_or_update_while_partial_overlap_blocks():
    existing = {
        "effective_from": date(2026, 1, 1),
        "effective_to": date(2026, 1, 31),
        "amount": Decimal("100"),
        "currency": "JPY",
    }
    assert compare_price_period(existing, {**existing}) == "skip"
    assert compare_price_period(existing, {**existing, "amount": Decimal("110")}) == "update"
    assert (
        compare_price_period(
            existing,
            {
                **existing,
                "effective_from": date(2026, 1, 20),
                "effective_to": date(2026, 2, 10),
            },
        )
        == "overlap"
    )
    assert (
        compare_price_period(
            existing,
            {
                **existing,
                "effective_from": date(2026, 2, 1),
                "effective_to": date(2026, 2, 28),
            },
        )
        == "disjoint"
    )
