"""Field-source inspection for the per-supplier inquiry settings panel.

When a user opens an order's inquiry tab they should see, per supplier, every
field the bound template declares (in `field_positions`) along with where each
value would come from at render time. Previously the only visibility was the
generated Excel itself, which made "why is this cell blank / wrong" debugging
impossible.

The inspection mirrors the precedence chain in
`orchestrator._order_metadata()`:

    1. order.order_metadata JSON                    → source="metadata"
    2. order column overrides (po_number/...)       → source="metadata"
    3. Port.location → delivery_address             → source="port_master"
    4. Supplier.{address,phone,fax,...}             → source="supplier_master"
    5. nothing                                      → source="empty"

The frontend uses `source` to render a badge (蓝/绿/青/灰) so the user knows
whether editing makes sense locally or whether they should update master data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from domains.inquiry.models import SupplierTemplate

# TD-1 (2026-06-16) — Removed direct imports of `Order` (orders domain)
# and `Port` / `Supplier` (masterdata domain). The ORM objects still
# flow through at runtime; we now type-annotate them as `Any` and call
# `masterdata.service.get_port()` / `get_supplier()` for any
# cross-domain lookup.


# Human-readable labels for the fields we see in production templates. Anything
# missing falls back to a humanised key — that's safe (better than crash) but
# we should update this map as new templates appear.
FIELD_LABELS_ZH: dict[str, str] = {
    "po_number": "PO 号",
    "ship_name": "船名",
    "ship_port_title": "船名与目标港口",
    "ship_name_jp": "船名（日文）",
    "ship_name_alt": "船名（备用）",
    "vendor_name": "供应商（PO 上）",
    "order_date": "订单日期",
    "delivery_date": "交货日期",
    "delivery_time_notes": "交货时间备注",
    "delivery_address": "交货地址",
    "delivery_contact": "交货联系人",
    "currency": "币种",
    "destination_port": "目的港",
    "destination": "目的地",
    "voyage": "航次",
    "supplier_name": "供应商名称",
    "supplier_address": "供应商地址",
    "supplier_zip_code": "供应商邮编",
    "supplier_tel": "供应商电话",
    "supplier_fax": "供应商传真",
    "supplier_email": "供应商邮箱",
    "supplier_contact": "供应商联系人",
    "payment_method": "付款方式",
    "payment_date": "付款期限",
    "invoice_number": "发票号",
    "generated_date": "询价单生成日期",
    "inquiry_reference": "询价单编号",
    "internal_contact": "内部联系人及电话",
}


# Which supplier master columns map to which template field keys. Order matters
# only for documentation — the dict is iterated whole during source detection.
SUPPLIER_FIELD_MAP: dict[str, str] = {
    "supplier_name": "name",
    "supplier_address": "address",
    "supplier_tel": "phone",
    "supplier_fax": "fax",
    "supplier_zip_code": "zip_code",
    "supplier_email": "email",
    "supplier_contact": "contact",
    "payment_method": "default_payment_method",
    "payment_date": "default_payment_terms",
}


# Keys whose semantic scope is *per-supplier*. User overrides for these MUST
# live under `order_metadata.supplier_overrides[supplier_id]` so two suppliers
# on the same order don't cross-contaminate each other's contact / payment
# fields. Top-level `order_metadata.<key>` is ignored for these (treat any
# legacy data there as stale).
#
# Everything else (delivery_address, ship_name_jp, voyage, etc.) is per-order
# and lives at the order_metadata top level.
SUPPLIER_LEVEL_KEYS: frozenset[str] = frozenset(SUPPLIER_FIELD_MAP.keys())


def supplier_overrides_for(
    order_metadata: dict | None, supplier_id: int | None
) -> dict:
    """Read supplier-scoped user overrides out of order metadata.

    Returns an empty dict if no overrides have been written. Tolerates the
    legacy shape where `supplier_overrides` is missing entirely."""
    if order_metadata is None or supplier_id is None:
        return {}
    nested = order_metadata.get("supplier_overrides")
    if not isinstance(nested, dict):
        return {}
    block = nested.get(str(supplier_id)) or nested.get(supplier_id)
    return block if isinstance(block, dict) else {}


@dataclass
class FieldInfo:
    key: str
    label: str
    position: str
    value: str | None
    source: str  # "metadata" | "port_master" | "supplier_master" | "empty"
    description: str | None
    sortkey: tuple[int, int]  # (row, col_index) for stable cell-order display

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "position": self.position,
            "value": self.value,
            "source": self.source,
            "description": self.description,
        }


_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _parse_position(pos: str) -> tuple[int, int]:
    """Return (row, col_index) so we can sort fields in visual reading order.

    Unparseable positions sort last (cell position is non-essential for UI
    correctness — only for stable visual order — so a bad position degrades
    gracefully)."""
    m = _CELL_RE.match((pos or "").upper())
    if not m:
        return (10_000, 10_000)
    col_letters, row_str = m.groups()
    col_index = 0
    for ch in col_letters:
        col_index = col_index * 26 + (ord(ch) - ord("A") + 1)
    return (int(row_str), col_index)


def _humanize(key: str) -> str:
    return key.replace("_", " ").title()


def _metadata_with_columns(order: Any) -> dict[str, Any]:
    """Order-level metadata: order_metadata JSON + typed-column overrides.

    Supplier-level keys (`supplier_tel`, `supplier_address`, ...) are
    stripped here on purpose. They were never meant to live at the top
    level — having them there causes cross-supplier contamination, see
    `SUPPLIER_LEVEL_KEYS`. Per-supplier overrides come from
    `supplier_overrides_for(...)` instead.
    """
    raw = order.order_metadata or {}
    base: dict[str, Any] = {
        k: v
        for k, v in raw.items()
        if k != "supplier_overrides" and k not in SUPPLIER_LEVEL_KEYS
    }
    column_overrides = {
        "po_number": order.po_number,
        "ship_name": order.ship_name,
        "vendor_name": order.vendor_name,
        "order_date": order.order_date,
        "delivery_date": order.delivery_date,
        "loading_date": order.loading_date,
        "invoice_number": order.invoice_number,
        "currency": order.currency,
        "destination_port": order.destination_port,
    }
    for k, v in column_overrides.items():
        if v not in (None, ""):
            base[k] = v
    ship_name = str(base.get("ship_name") or "").strip()
    destination_port = str(base.get("destination_port") or "").strip()
    base["ship_port_title"] = (
        f"{ship_name}［{destination_port}］" if destination_port else ship_name
    )
    return base


def compute_field_sources(
    *,
    order: Any,
    template: SupplierTemplate,
    supplier_id: int | None,
    db: Session,
) -> list[FieldInfo]:
    """Return one FieldInfo per declared cell, sorted by cell visual order.

    Templates that have empty field_positions yield an empty list — the UI
    renders an empty panel with a "this template has no header fields"
    message rather than a guess.
    """
    # TD-1 — cross-domain reads via masterdata public service (dicts) so
    # this module no longer reaches into masterdata.models. `supplier` and
    # `port` are plain dicts below; attribute access changes to dict
    # indexing.
    from domains.masterdata import service as masterdata_service

    field_positions = template.field_positions or {}
    metadata = _metadata_with_columns(order)
    sup_overrides = supplier_overrides_for(order.order_metadata, supplier_id)

    supplier = (
        masterdata_service.get_supplier(db, supplier_id)
        if supplier_id is not None
        else None
    )
    port = (
        masterdata_service.get_port(db, order.port_id) if order.port_id else None
    )

    out: list[FieldInfo] = []
    for key, pos_info in field_positions.items():
        if isinstance(pos_info, dict):
            position = pos_info.get("position", "")
            description = pos_info.get("description")
            source_key = str(pos_info.get("source") or key)
        else:
            position = pos_info or ""
            description = None
            source_key = key

        # Source detection follows the same precedence as the renderer:
        #   supplier-level override → order-level metadata → port master →
        #   supplier master → empty.
        # Supplier-level keys never read from the top-level metadata to
        # prevent cross-supplier contamination (see SUPPLIER_LEVEL_KEYS).
        if source_key in SUPPLIER_LEVEL_KEYS:
            override_val = sup_overrides.get(source_key)
            if override_val not in (None, ""):
                source = "metadata"
                value = override_val
            elif source_key in SUPPLIER_FIELD_MAP and supplier is not None:
                # `supplier` is a dict (masterdata.service.get_supplier),
                # so attribute lookups become dict lookups. The map values
                # are unchanged — same keys that _serialize_supplier emits.
                attr = SUPPLIER_FIELD_MAP[source_key]
                supplier_val = supplier.get(attr)
                if supplier_val not in (None, ""):
                    source = "supplier_master"
                    value = supplier_val
                else:
                    source = "empty"
                    value = None
            else:
                source = "empty"
                value = None
        else:
            metadata_val = metadata.get(source_key)
            if metadata_val not in (None, ""):
                source = "metadata"
                value = metadata_val
            elif (
                source_key == "delivery_address"
                and port is not None
                and port.get("location")
            ):
                source = "port_master"
                value = port["location"]
            else:
                source = "empty"
                value = None

        out.append(
            FieldInfo(
                key=key,
                label=FIELD_LABELS_ZH.get(key, _humanize(key)),
                position=position,
                value=str(value) if value is not None else None,
                source=source,
                description=description,
                sortkey=_parse_position(position),
            )
        )

    out.sort(key=lambda f: f.sortkey)
    return out
