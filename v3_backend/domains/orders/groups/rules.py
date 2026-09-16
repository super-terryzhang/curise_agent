"""Deterministic vessel/date/destination identity; no fuzzy or LLM grouping."""
from __future__ import annotations

import unicodedata
from datetime import datetime

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d-%b-%Y",
                 "%B %d %Y", "%d-%m-%Y")


def normalized(value):
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).split()).upper()


def normalized_date(value):
    if not isinstance(value, str):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def grouping_identity(order, ports):
    """Return the deterministic supply-arrangement identity.

    Business rule (2026-09-15): automatic grouping uses only the confirmed
    loading day and the selected master-data port. Ship name remains display
    metadata and never blocks or separates an arrangement.
    """
    metadata = order.order_metadata or {}

    def value(field):
        return getattr(order, field, None) or metadata.get(field)

    ship = normalized(value("ship_name"))
    if ship in {"UNKNOWN", "N/A", "TBD", "未知"}:
        ship = ""
    day = normalized_date(value("loading_date"))
    reasons = []
    if not day:
        reasons.append("缺少或无法识别装船日")
    # The selected master-data port is the sole business destination.
    # Extracted text remains source evidence, never a competing grouping key.
    port = ports.get(order.port_id)
    if port is None:
        reasons.append("请选择目标港口")
    if reasons:
        return None, "；".join(reasons)
    port_key, port_label = f"port:{port.id}", port.name
    return {"ship": ship, "day": day, "date_basis": "loading_date",
            "port_id": port.id, "port_key": port_key, "port_label": port_label}, None


def identity_key(identity):
    return (identity["day"], identity["port_key"])
