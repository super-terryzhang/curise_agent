"""Exchange-rate admin endpoints — list + create.

The legacy `POST /api/orders/{id}/financial-analysis` endpoint (and its
4 e2e tests) was removed 2026-05-27 when the per-order cost-items P&L
system replaced it. Tests for the new flow live in:
    test_v2/section_4_orders/test_financial_currency.py
    test_v2/section_4_orders/test_financial_computation.py
    test_v2/section_4_orders/test_financial_excel.py

This file only retains the masterdata exchange-rate coverage that was
incidentally bundled here.
"""

from __future__ import annotations

from decimal import Decimal

from domains.masterdata.models import ExchangeRate


def test_admin_can_list_exchange_rates(client, admin_auth, db):
    """Admin lists exchange rates via /api/data/exchange-rates.

    The DB column is `base_currency`/`target_currency` but the API maps these
    to the canonical `from_currency`/`to_currency` field names in responses.
    """
    from datetime import date

    db.add(
        ExchangeRate(
            from_currency="USD",
            to_currency="JPY",
            rate=Decimal("148.5"),
            effective_date=date(2026, 5, 13),
        )
    )
    db.add(
        ExchangeRate(
            from_currency="USD",
            to_currency="SGD",
            rate=Decimal("1.35"),
            effective_date=date(2026, 5, 13),
        )
    )
    db.commit()

    r = client.get("/api/data/exchange-rates", headers=admin_auth)
    assert r.status_code == 200
    rates = r.json()
    assert isinstance(rates, list)
    pairs = {(rt["from_currency"], rt["to_currency"]): rt for rt in rates}
    assert ("USD", "JPY") in pairs
    assert float(pairs[("USD", "JPY")]["rate"]) == 148.5
    assert ("USD", "SGD") in pairs


def test_admin_can_create_exchange_rate(client, admin_auth):
    r = client.post(
        "/api/data/exchange-rates",
        json={
            "from_currency": "EUR",
            "to_currency": "USD",
            "rate": 1.08,
            "effective_date": "2026-05-13",
        },
        headers=admin_auth,
    )
    assert r.status_code in {200, 201}, r.text
    body = r.json()
    assert body["from_currency"] == "EUR"
    assert body["to_currency"] == "USD"
    assert float(body["rate"]) == 1.08


def test_employee_cannot_create_exchange_rate(client, employee_auth):
    """Only admin/superadmin can write masterdata."""
    r = client.post(
        "/api/data/exchange-rates",
        json={
            "from_currency": "EUR",
            "to_currency": "USD",
            "rate": 1.08,
            "effective_date": "2026-05-13",
        },
        headers=employee_auth,
    )
    assert r.status_code == 403
