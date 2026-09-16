"""Per-supplier inquiry worker — private to the inquiry domain.

Runs the data-load → template-select → render → save flow for one supplier.
Wrapped by `_run_supplier_safe` so failures are recorded on the row rather
than propagating up to crash the orchestrator's thread pool.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from domains.inquiry import _state
from domains.inquiry import repository as repo
from domains.inquiry import service as inquiry_service
from domains.inquiry.models import Inquiry, SupplierTemplate
from domains.inquiry.sinks import InquiryProgressSink
from domains.inquiry.template_contract import normalized_contract
from domains.inquiry.template_engine import render_inquiry_excel
from domains.inquiry.template_selector import select_template
from domains.masterdata import service as masterdata_service
from infrastructure.db import session as session_module
from infrastructure.storage import get_storage

logger = logging.getLogger(__name__)


def run_supplier_safe(
    *,
    inquiry_id: int,
    supplier_id: int,
    products: list[dict[str, Any]],
    metadata: dict[str, Any],
    sink: InquiryProgressSink,
    template_id_override: int | None,
) -> None:
    """Execute one supplier's inquiry; record outcome on the row."""
    started = time.time()
    db = session_module.SessionLocal()
    try:
        if sink.should_cancel():
            _state.upsert_supplier(
                db,
                inquiry_id=inquiry_id,
                supplier_id=supplier_id,
                fields={"status": "cancelled", "started_at": datetime.utcnow()},
            )
            db.commit()
            sink.emit({"type": "supplier_done", "supplier_id": supplier_id, "status": "cancelled"})
            return

        sink.emit(
            {
                "type": "supplier_start",
                "supplier_id": supplier_id,
                "product_count": len(products),
            }
        )
        try:
            _run_supplier(
                db,
                inquiry_id=inquiry_id,
                supplier_id=supplier_id,
                products=products,
                metadata=metadata,
                sink=sink,
                template_id_override=template_id_override,
            )
            row = repo.get_inquiry_supplier(db, inquiry_id, supplier_id)
            elapsed = round(time.time() - started, 1)
            if row is not None:
                row.elapsed_seconds = elapsed
                row.completed_at = datetime.utcnow()
                if not row.status or row.status == "generating":
                    row.status = "completed"
            db.commit()
            sink.emit(
                {
                    "type": "supplier_done",
                    "supplier_id": supplier_id,
                    "status": row.status if row else "completed",
                    "elapsed_seconds": elapsed,
                }
            )
        except Exception as exc:
            elapsed = round(time.time() - started, 1)
            logger.error("inquiry: supplier %d failed: %s", supplier_id, exc, exc_info=True)
            _state.upsert_supplier(
                db,
                inquiry_id=inquiry_id,
                supplier_id=supplier_id,
                fields={
                    "status": "error",
                    "error_message": str(exc),
                    "elapsed_seconds": elapsed,
                    "completed_at": datetime.utcnow(),
                },
            )
            db.commit()
            sink.emit(
                {
                    "type": "supplier_done",
                    "supplier_id": supplier_id,
                    "status": "error",
                    "error": str(exc),
                    "elapsed_seconds": elapsed,
                }
            )
    finally:
        db.close()


def _run_supplier(
    db: Session,
    *,
    inquiry_id: int,
    supplier_id: int,
    products: list[dict[str, Any]],
    metadata: dict[str, Any],
    sink: InquiryProgressSink,
    template_id_override: int | None,
) -> None:
    metadata = dict(metadata)
    metadata["generated_date"] = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    metadata["inquiry_reference"] = f"RFQ-{inquiry_id:06d}-{supplier_id:04d}"
    ship_name = str(metadata.get("ship_name") or "").strip()
    destination_port = str(metadata.get("destination_port") or "").strip()
    metadata["ship_port_title"] = (
        f"{ship_name}［{destination_port}］" if destination_port else ship_name
    )
    supplier_dict = masterdata_service.get_supplier(db, supplier_id) or {}
    supplier_info = _supplier_info(supplier_dict)
    missing = _missing_supplier_fields(supplier_dict)
    subtotal = _compute_subtotal(products)
    currency = metadata.get("currency", "")

    all_templates = inquiry_service.list_supplier_templates(db)
    template, method, _candidates = select_template(
        supplier_id, all_templates, template_id_override=template_id_override
    )
    field_mapping = _field_mapping_from_template(template)

    _state.upsert_supplier(
        db,
        inquiry_id=inquiry_id,
        supplier_id=supplier_id,
        fields={
            "status": "generating",
            "started_at": datetime.utcnow(),
            "supplier_name": supplier_dict.get("name") or f"供应商 #{supplier_id}",
            "supplier_info": supplier_info,
            "missing_fields": missing,
            "product_count": len(products),
            "subtotal": subtotal,
            "currency": currency,
            "template_id": template.id if template else None,
            "template_name": template.template_name if template else None,
            "template_selection_method": method,
            "excel_file_url": None,
            "preview_html_url": None,
            "verify_results": None,
            "error_message": None,
        },
    )
    db.commit()

    template_bytes = _read_template_bytes(template)
    inquiry = db.get(Inquiry, inquiry_id)
    if inquiry is not None and inquiry.group_id is not None:
        if template is None:
            raise ValueError("该供应商未配置可用的询价模板")
        if template_bytes is None:
            raise ValueError(f"询价模板“{template.template_name}”的原始文件不可用")
    strict_output = bool(
        products
        and template is not None
        and normalized_contract(template)["is_dynamic"]
    )
    render_products = (
        [_with_rfq_defaults(product) for product in products]
        if strict_output
        else products
    )
    if strict_output and template_bytes is None:
        raise ValueError("AUTOMATION_TEMPLATE_FILE_UNAVAILABLE")
    excel_bytes = render_inquiry_excel(
        template,
        metadata=metadata,
        products=render_products,
        supplier_id=supplier_id,
        template_file_bytes=template_bytes,
        field_mapping=field_mapping,
    )
    if strict_output:
        from domains.inquiry.workbook_quality import validate_output
        validate_output(excel_bytes, template, render_products, metadata)

    storage = get_storage()
    safe = f"inquiry_{inquiry_id}_{supplier_id}_{uuid.uuid4().hex[:8]}.xlsx"
    file_url = storage.upload(
        "inquiries",
        safe,
        excel_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    row = repo.get_inquiry_supplier(db, inquiry_id, supplier_id)
    if row is not None:
        row.excel_file_url = file_url
        row.status = "completed"
        db.commit()
    sink.emit({"type": "supplier_saved", "supplier_id": supplier_id, "url": file_url})


# ─── Pure helpers ─────────────────────────────────────────────


def compute_subtotal(products: list[dict[str, Any]]) -> float:
    return _compute_subtotal(products)


def _with_rfq_defaults(product: dict[str, Any]) -> dict[str, Any]:
    """Give contract-backed outputs one stable supplier-facing row shape.

    Arrangement matching already excludes unsafe unit conversions. Older
    arrangement snapshots, however, store safe same-unit rows as
    ``quantity``/``unit`` rather than ``rfq_quantity``/``rfq_unit``. Normalize
    only the rendering copy so the strict workbook validator and formula-cache
    path always run without rewriting the immutable inquiry snapshot.
    """

    item = dict(product)
    matched = item.get("matched_product") or {}
    if "rfq_quantity" not in item:
        item["rfq_quantity"] = item.get("quantity")
    if not item.get("rfq_unit"):
        item["rfq_unit"] = matched.get("unit") or item.get("unit")
    item.setdefault("source_quantity", item.get("quantity"))
    item.setdefault("source_unit", item.get("unit"))
    return item


def _compute_subtotal(products: list[dict[str, Any]]) -> float:
    """Sum line totals using DB catalog price only (v44 invariant).

    Rows whose matched DB product has no price contribute nothing — the
    inquiry sheet will show empty unit_price cells for those rows and
    the subtotal will reflect only the priced lines. Customer PO prices
    in `product.unit_price` are deliberately NOT used here; they're
    reserved for anomaly detection / audit, not supplier-facing math.
    """
    total = 0.0
    for p in products:
        matched = p.get("matched_product") or {}
        qty = p.get("rfq_quantity", p.get("quantity")) or 0
        price = matched.get("price")
        if price is None:
            continue
        try:
            total += float(qty) * float(price)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def supplier_info(supplier_dict: dict[str, Any]) -> dict[str, Any]:
    return _supplier_info(supplier_dict)


def _supplier_info(supplier_dict: dict[str, Any]) -> dict[str, Any]:
    keys = ("contact", "email", "phone", "country_name")
    info: dict[str, Any] = {k: supplier_dict.get(k, "") for k in keys}
    # Embed per-field letterhead status so the inquiry page can render
    # all 9 supplier letterhead cells with a filled/empty indicator —
    # see `supplier_letterhead_status` for the entry schema.
    # Lives inside `supplier_info` (the existing JSON column on
    # InquirySupplier) so no schema migration is needed.
    info["letterhead"] = supplier_letterhead_status(supplier_dict)
    return info


def missing_supplier_fields(supplier_dict: dict[str, Any]) -> list[str] | None:
    return _missing_supplier_fields(supplier_dict)


def _missing_supplier_fields(supplier_dict: dict[str, Any]) -> list[str] | None:
    if not supplier_dict:
        return ["supplier_record"]
    missing = [k for k in ("contact", "email", "phone") if not supplier_dict.get(k)]
    return missing or None


def supplier_letterhead_status(supplier_dict: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per-field fill status for the 9 supplier letterhead fields.

    Returned regardless of whether the supplier's bound template uses each
    field — the inquiry page surfaces this on every supplier card so the
    user sees up-front which letterhead cells will print blank on the
    generated Excel.

    Each entry:
        - `key`     : template field key (e.g. `"supplier_address"`)
        - `column`  : the matching `Supplier` column (e.g. `"address"`).
                      Frontend uses this as the PATCH payload key when the
                      user inline-edits the value.
        - `label`   : Chinese display label
        - `value`   : current value or `None`
        - `filled`  : true iff value is non-empty after `str().strip()`

    Order is stable (matches SUPPLIER_FIELD_MAP iteration). Empty dict /
    None inputs yield 9 entries all `filled=False` so the frontend renders
    a "supplier record missing" state consistently.
    """
    # Local import — field_inspection is a sibling module but importing at
    # module top would create a cyclic risk if field_inspection ever needs
    # _supplier_worker helpers. Import here, lazily, mirroring the same
    # pattern used by orchestrator._order_metadata().
    from domains.inquiry.field_inspection import FIELD_LABELS_ZH, SUPPLIER_FIELD_MAP

    sd = supplier_dict or {}
    out: list[dict[str, Any]] = []
    for tpl_key, col in SUPPLIER_FIELD_MAP.items():
        raw = sd.get(col)
        # Treat any non-string value (e.g. numeric IDs) as a value too,
        # but strip strings for the "filled" check so whitespace-only
        # cells don't pass.
        if raw is None:
            value: Any = None
            filled = False
        elif isinstance(raw, str):
            stripped = raw.strip()
            value = stripped if stripped else None
            filled = bool(stripped)
        else:
            value = raw
            filled = True
        out.append(
            {
                "key": tpl_key,
                "column": col,
                "label": FIELD_LABELS_ZH.get(tpl_key, tpl_key),
                "value": value,
                "filled": filled,
            }
        )
    return out


def _read_template_bytes(template: SupplierTemplate | None) -> bytes | None:
    if template is None or not template.template_file_url:
        return None
    try:
        return get_storage().download(template.template_file_url)
    except Exception as exc:
        logger.warning("inquiry: template %d file unavailable, falling back: %s", template.id, exc)
        return None


def _field_mapping_from_template(template: SupplierTemplate | None) -> dict[str, str] | None:
    if template is None:
        return None
    meta = template.field_mapping_metadata
    if not isinstance(meta, dict):
        return None
    raw = meta.get("field_mapping")
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}
    return None


__all__ = [
    "run_supplier_safe",
    "compute_subtotal",
    "supplier_info",
    "missing_supplier_fields",
    "supplier_letterhead_status",
]
