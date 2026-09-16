"""Resolve country_id / port_id / delivery_date from Order hints.

Strategy (in order):
  1. `destination_port` column → exact port name / code match
  2. `currency` → country via currency→country map
  3. Scan `order_metadata` values for port / country names
  4. (optional) LLM fallback — not implemented in Phase 3; returns whatever was found

All lookups are against `domains.masterdata` (via its repository, not ORM
directly — respects ADR-0006).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from domains.masterdata import Country, Port
from domains.masterdata import repository as md_repo
from domains.orders.models import Order

logger = logging.getLogger(__name__)


# Currency (or currency text) → ISO-3 country code.
# Ambiguous currencies (EUR) map to None so we don't guess.
_CURRENCY_TO_COUNTRY: dict[str, str | None] = {
    "JPY": "JPN",
    "YEN": "JPN",
    "円": "JPN",
    "AUD": "AUS",
    "USD": "USA",
    "THB": "THA",
    "CNY": "CHN",
    "RMB": "CHN",
    "VND": "VNM",
    "GBP": "GBR",
    "CAD": "CAN",
    "NZD": "NZL",
    "KRW": "KOR",
    "TWD": "TWN",
    "INR": "IND",
    "IDR": "IDN",
    "MYR": "MYS",
    "PHP": "PHL",
    "HKD": "HKG",
    "SGD": "SGP",
    "EUR": None,
}


def resolve_geo(order: Order, db: Session) -> dict[str, Any]:
    """Return {country_id, port_id, delivery_date} — any or all may be None."""
    countries = md_repo.list_countries(db)
    ports = md_repo.list_ports(db)

    metadata = order.order_metadata or {}
    raw_extras = metadata.get("extra_fields") if isinstance(metadata, dict) else None
    extras: dict[str, Any] = raw_extras if isinstance(raw_extras, dict) else {}

    all_text = _join_metadata_text(order, metadata, extras)

    matched_port = _match_port(order, metadata, extras, all_text, ports)
    matched_country = _match_country(order, metadata, matched_port, all_text, countries)

    delivery_date = order.delivery_date or metadata.get("delivery_date")
    if not delivery_date:
        for key in ("loading_date", "deliver_on_date"):
            if value := extras.get(key):
                delivery_date = str(value).strip()
                break

    return {
        "country_id": matched_country.id if matched_country else order.country_id,
        "port_id": matched_port.id if matched_port else order.port_id,
        "delivery_date": delivery_date,
    }


# ─── Port matching ────────────────────────────────────────────


def _match_port(
    order: Order,
    metadata: dict[str, Any],
    extras: dict[str, Any],
    all_text: str,
    ports: list[Port],
) -> Port | None:
    # Priority 0 (2026-05-29): if `order.port_id` is already set, it
    # is the source of truth — either it was chosen by a prior match
    # (re-deriving from the string would either return the same answer
    # or, worse, silently flip to a different port if the string and
    # the prior match disagreed) or it was overridden by the user via
    # the edit UI (re-deriving silently reverts the user's correction —
    # the original bug). The empirical reproduction lives in
    # test_v2/section_4_orders/test_rematch_root_cause_probe.py.
    #
    # Defensive: if the stored id no longer exists in the pool
    # (deleted port, etc.), fall through to the string priorities
    # instead of returning None — better to guess than to strand the
    # order with a broken FK.
    if order.port_id:
        for p in ports:
            if p.id == order.port_id:
                return p

    # Priority 1: `destination_port` direct match (name substring)
    dest = (order.destination_port or metadata.get("destination_port") or "").strip().upper()
    if dest:
        for p in ports:
            name = (p.name or "").upper()
            if name and (dest in name or name in dest):
                return p

    # Priority 2: `extras.port_code`
    port_code = extras.get("port_code") if extras else None
    if port_code:
        for p in ports:
            if p.code and p.code.upper() == str(port_code).strip().upper():
                return p

    # Priority 3: any port name substring in the combined text
    upper_text = all_text.upper()
    for p in ports:
        name = (p.name or "").upper()
        if name and len(name) >= 4 and name in upper_text:
            return p

    return None


# ─── Country matching ─────────────────────────────────────────


def _match_country(
    order: Order,
    metadata: dict[str, Any],
    matched_port: Port | None,
    all_text: str,
    countries: list[Country],
) -> Country | None:
    # Priority 0 (2026-05-29): same rationale as `_match_port` —
    # respect an already-set `order.country_id` so user overrides
    # survive rematch. If the matched port lives in a different
    # country (rare cross-country move via separate port edit), we
    # still honour the explicit country choice; the cross-country
    # mismatch is a UX concern for the edit form (logged follow-up),
    # not the matcher's call to make.
    if order.country_id:
        for c in countries:
            if c.id == order.country_id:
                return c

    if matched_port is not None:
        for c in countries:
            if c.id == matched_port.country_id:
                return c

    currency = (order.currency or metadata.get("currency") or "").strip()
    if currency:
        code = _CURRENCY_TO_COUNTRY.get(currency) or _CURRENCY_TO_COUNTRY.get(currency.upper())
        if code:
            for c in countries:
                if c.code == code:
                    return c

    upper_text = all_text.upper()
    for c in countries:
        name = (c.name or "").upper()
        if name and len(name) >= 4 and name in upper_text:
            return c

    return None


# ─── Helpers ──────────────────────────────────────────────────


def _join_metadata_text(order: Order, metadata: dict[str, Any], extras: dict[str, Any]) -> str:
    parts: list[str] = []
    for value in (
        order.po_number,
        order.ship_name,
        order.vendor_name,
        order.destination_port,
        order.currency,
    ):
        if value:
            parts.append(str(value))
    for value in metadata.values():
        if value is None or isinstance(value, (dict, list)):
            continue
        parts.append(str(value))
    for value in extras.values():
        if value is None:
            continue
        parts.append(str(value))
    return " ".join(parts)
