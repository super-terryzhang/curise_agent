"""Exact product-code matching (zero false positives)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from domains.masterdata import Product


def load_candidate_pool(
    db: Session,
    *,
    country_id: int | None,
    port_id: int | None,
    delivery_date: datetime | None,
    price_date: datetime | None = None,
) -> list[Product]:
    """Return Products in scope for this order — filtered by geo + effective window.

    We keep the pool loaded once so both code_first and llm_refine operate on
    the same snapshot (consistent with v2 semantics).
    """
    stmt = db.query(Product).filter(Product.status.is_(True))
    if country_id is not None:
        stmt = stmt.filter(Product.country_id == country_id)
    if port_id is not None:
        stmt = stmt.filter(Product.port_id == port_id)
    if delivery_date is not None:
        stmt = stmt.filter(
            or_(Product.effective_from.is_(None), Product.effective_from <= delivery_date)
        ).filter(or_(Product.effective_to.is_(None), Product.effective_to >= delivery_date))
    products = list(stmt.all())
    from domains.masterdata import service as masterdata_service

    effective_price_day = (price_date or delivery_date)
    masterdata_service.resolve_effective_product_prices(
        db,
        products,
        effective_price_day.date() if effective_price_day is not None else None,
    )
    return products


def match_by_code(
    inputs: list[dict[str, Any]], pool: list[Product]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Match each input against the pool by exact code (case-insensitive).

    Returns (all_results, unmatched_inputs) where:
    - `all_results` is a list of match dicts in the SAME ORDER as `inputs`
    - `unmatched_inputs` is a subset of the original inputs that had no code hit
    """
    by_code: dict[str, list[Product]] = {}
    by_id: dict[int, Product] = {}
    for p in pool:
        by_id[p.id] = p
        if p.code:
            by_code.setdefault(p.code.strip().upper(), []).append(p)

    all_results: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for prod in inputs:
        item_code = (prod.get("product_code") or "").strip()
        product_name = (prod.get("product_name") or "").strip()

        match_dict = {
            **prod,
            "product_code": item_code,
            "product_name": product_name,
            "quantity": prod.get("quantity"),
            "unit": prod.get("unit"),
            "unit_price": prod.get("unit_price"),
        }

        manual_product_id = prod.get("manual_product_id")
        if manual_product_id is not None:
            try:
                manual_candidate = by_id.get(int(manual_product_id))
            except (TypeError, ValueError):
                manual_candidate = None
            if manual_candidate is not None:
                match_dict.update(
                    {
                        "match_status": "matched",
                        "match_score": 1.0,
                        "match_reason": "人工关联商品",
                        "matched_product": serialize_db_product(manual_candidate),
                    }
                )
            else:
                match_dict.update(
                    {
                        "match_status": "not_matched",
                        "match_score": 0.0,
                        "match_reason": "人工关联商品不在当前港口或有效期候选范围内",
                        "matched_product": None,
                    }
                )
            all_results.append(match_dict)
            # A deliberate human choice must never silently fall through to a
            # different exact-code or fuzzy candidate.
            continue

        candidates = by_code.get(item_code.upper(), []) if item_code else []
        if len(candidates) == 1:
            dbp = candidates[0]
            match_dict.update(
                {
                    "match_status": "matched",
                    "match_score": 1.0,
                    "match_reason": "产品代码完全匹配",
                    "matched_product": serialize_db_product(dbp),
                }
            )
        elif len(candidates) > 1:
            match_dict.update(
                match_status="not_matched", match_score=0.0,
                match_reason="同编码存在多个商品候选，需要确认",
                matched_product=None, candidate_ids=sorted(p.id for p in candidates),
            )
            # Do not let fuzzy matching turn an exact-code conflict into a choice.
        else:
            match_dict.update(
                {
                    "match_status": "not_matched",
                    "match_score": 0.0,
                    "match_reason": "",
                    "matched_product": None,
                }
            )
            unmatched.append(prod)

        all_results.append(match_dict)

    return all_results, unmatched


def serialize_db_product(p: Product) -> dict[str, Any]:
    resolved = getattr(p, "_effective_prices", {})
    purchase = resolved.get("purchase")
    selling = resolved.get("selling")
    return {
        "id": p.id,
        "code": p.code,
        "product_name_en": p.product_name_en,
        "product_name_jp": p.product_name_jp,
        "price": (
            purchase.get("amount")
            if purchase is not None
            else (float(p.price) if p.price is not None else None)
        ),
        # contract_price — surfaced in match_results so the frontend can
        # compare it to the cruise PO's unit_price (R2 / Felix 2026-06-06).
        "contract_price": (
            selling.get("amount")
            if selling is not None
            else (float(p.contract_price) if p.contract_price is not None else None)
        ),
        "purchase_price_period": purchase,
        "selling_price_period": selling,
        "currency": p.currency,
        "supplier_id": p.supplier_id,
        "category_id": p.category_id,
        "pack_size": p.pack_size,
        "unit_size": p.unit_size,
        "unit": p.unit,
    }


# Backward-compatible private name used by existing focused tests.
_serialize_db_product = serialize_db_product
