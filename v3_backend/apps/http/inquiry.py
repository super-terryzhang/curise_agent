"""Inquiry HTTP endpoints — `/api/orders/{id}/...` routes for Phase 5.

Replaces the 8 stubs that lived in `apps/http/orders.py`. Talks to
`domains.inquiry.orchestrator` (sync entry points) via the background job
runner, and to `domains.inquiry.service` for read-only state.

SSE: `POST /generate-inquiry` registers a stream handle (asyncio.Queue +
CancelEvent), then submits the orchestrator job. `GET /inquiry-stream`
attaches to that handle and forwards events to the client until a
terminal event is observed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse, StreamingResponse

from apps.http import _inquiry_streams
from apps.http._deps import CurrentUser, DbDep, Writer
from apps.jobs.runner import get_job_runner
from domains.inquiry import orchestrator
from domains.inquiry import service as inquiry_service
from domains.inquiry.errors import BadRequest, InquiryError, NotFound
from domains.inquiry.template_contract import normalized_contract
from domains.orders.errors import NotFound as OrderNotFound
from domains.orders.errors import OrderError
from domains.orders.service import get_order

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["inquiry"])


def _is_admin(user: CurrentUser) -> bool:
    return user.role in ("superadmin", "admin")


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound | OrderNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, BadRequest):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    if isinstance(exc, InquiryError | OrderError):
        return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


def _ensure_order_visible(db: Any, *, order_id: int, user: CurrentUser) -> None:
    """Translate `domains.orders.service.get_order` errors into HTTP early —
    keeps each inquiry endpoint from re-implementing the auth check."""
    try:
        get_order(db, order_id=order_id, user_id=user.id, is_admin=_is_admin(user))
    except OrderError as exc:
        raise _translate(exc) from exc


# ─── Read-only endpoints ──────────────────────────────────────


@router.get("/{order_id}/inquiry-readiness")
def inquiry_readiness(order_id: int, db: DbDep, user: CurrentUser) -> dict[str, Any]:
    """Pre-analysis: group products by supplier + resolve templates.

    Returns the v2-shaped `inquiry_data` dict plus `status='pending'`.
    Idempotent — re-running on a completed inquiry resets to pending.
    """
    _ensure_order_visible(db, order_id=order_id, user=user)
    try:
        state = orchestrator.pre_analyze(db, order_id)
    except (InquiryError, OrderError) as exc:
        raise _translate(exc) from exc
    return state.to_legacy_dict()


@router.get("/{order_id}/inquiry-fields/{supplier_id}")
def inquiry_fields(
    order_id: int,
    supplier_id: int,
    db: DbDep,
    user: CurrentUser,
    template_id: int | None = Query(default=None),
) -> dict[str, Any]:
    """List every header field the supplier's template declares, with its
    current value and the source (metadata / port_master / supplier_master /
    empty). Used by the inquiry tab's "field map" panel so customers can see
    *why* a particular cell in the generated Excel has the value it does — and
    can override it per-order without editing master data.

    `template_id` query param lets the frontend preview a non-bound template's
    field map BEFORE the user clicks "重做". Without it, switching templates
    in the picker would show stale fields from the previously generated
    template until regeneration completes.
    """
    from domains.inquiry import field_inspection
    from domains.inquiry.models import SupplierTemplate
    from domains.orders.models import Order

    _ensure_order_visible(db, order_id=order_id, user=user)

    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")

    # Resolve which template to inspect — explicit query param wins so the
    # picker can preview any template, falling back to whatever the inquiry
    # currently bound.
    effective_template_id = template_id
    if effective_template_id is None:
        state = inquiry_service.read_inquiry_state(db, order_id)
        if state is None:
            return {"fields": [], "template": None, "reason": "尚未生成询价"}
        supplier_state = next(
            (s for s in state.suppliers if s.supplier_id == supplier_id), None
        )
        if supplier_state is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "该供应商不在询价中")
        effective_template_id = (
            supplier_state.template.id if supplier_state.template else None
        )

    template = (
        db.get(SupplierTemplate, effective_template_id)
        if effective_template_id
        else None
    )
    if template is None or not (template.field_positions or {}):
        return {
            "fields": [],
            "template": (
                {"id": template.id, "name": template.template_name}
                if template
                else None
            ),
            "reason": "此模板未声明字段位置（如韩国/Jeju 模板使用区域渲染，无需逐字段填写）",
        }

    fields = field_inspection.compute_field_sources(
        order=order,
        template=template,
        supplier_id=supplier_id,
        db=db,
    )
    return {
        "fields": [f.to_dict() for f in fields],
        "template": {"id": template.id, "name": template.template_name},
        "reason": None,
    }


@router.patch("/{order_id}/inquiry-fields/{supplier_id}")
def patch_inquiry_fields(
    order_id: int,
    supplier_id: int,
    body: dict[str, Any],
    db: DbDep,
    user: Writer,
) -> dict[str, Any]:
    """Save user-edited field values, routing each key to the right scope.

    Supplier-level fields (supplier_address/tel/fax/.../payment_method) land
    under `order_metadata.supplier_overrides[supplier_id]` so they only affect
    this one supplier's inquiry. All other fields go to the top-level
    `order_metadata` (per-order, shared by all suppliers).

    Body shape: `{"<field_key>": "<value>", ...}` — value `""` deletes the
    override (revert to default).
    """
    from sqlalchemy.orm.attributes import flag_modified

    from domains.inquiry.field_inspection import SUPPLIER_LEVEL_KEYS
    from domains.orders.models import Order

    _ensure_order_visible(db, order_id=order_id, user=user)

    edits = body if isinstance(body, dict) else {}
    if not edits:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "无字段改动")

    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")

    metadata = dict(order.order_metadata or {})
    supplier_overrides_root = dict(metadata.get("supplier_overrides") or {})
    sid_key = str(supplier_id)
    supplier_block = dict(supplier_overrides_root.get(sid_key) or {})

    saved_order_keys: list[str] = []
    saved_supplier_keys: list[str] = []
    for key, raw_val in edits.items():
        value = (raw_val if isinstance(raw_val, str) else str(raw_val or "")).strip()
        if key in SUPPLIER_LEVEL_KEYS:
            if value == "":
                supplier_block.pop(key, None)
            else:
                supplier_block[key] = value
            saved_supplier_keys.append(key)
        else:
            if value == "":
                metadata.pop(key, None)
            else:
                metadata[key] = value
            saved_order_keys.append(key)

    if supplier_block:
        supplier_overrides_root[sid_key] = supplier_block
    else:
        supplier_overrides_root.pop(sid_key, None)

    if supplier_overrides_root:
        metadata["supplier_overrides"] = supplier_overrides_root
    else:
        metadata.pop("supplier_overrides", None)

    order.order_metadata = metadata
    flag_modified(order, "order_metadata")
    db.commit()

    return {
        "ok": True,
        "saved_order_keys": saved_order_keys,
        "saved_supplier_keys": saved_supplier_keys,
    }


@router.get("/{order_id}/inquiry-preview/{supplier_id}", response_class=HTMLResponse)
def inquiry_preview(
    order_id: int, supplier_id: int, db: DbDep, user: CurrentUser
) -> HTMLResponse:
    """Render an HTML preview of the supplier's inquiry Excel.

    The v3 frontend's `getInquiryPreview()` reads `res.text()` and stuffs it
    straight into a modal's `dangerouslySetInnerHTML`-equivalent — i.e. it
    expects an HTML body, not metadata JSON. Previously this returned
    `{"excel_file_url": "...", "preview_html_url": null, ...}` and the
    frontend rendered the raw JSON string as "HTML", which the user saw as
    a wall of `{"supplier_id":2,…}`.

    Strategy: load the xlsx from storage, render a small HTML table with
    the product rows + totals. No openpyxl→HTML magic — we control the
    layout. Cheap, works offline, no external converter dependency.
    """
    _ensure_order_visible(db, order_id=order_id, user=user)
    state = inquiry_service.read_inquiry_state(db, order_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "尚未生成询价")
    row = next((s for s in state.suppliers if s.supplier_id == supplier_id), None)
    if row is None or not row.excel_file_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未找到该供应商的询价文件")

    html = _render_inquiry_preview_html(db, supplier_state=row)
    return HTMLResponse(content=html)


def _render_inquiry_preview_html(db: Any, *, supplier_state: Any) -> str:
    """Build a self-contained HTML preview from the stored xlsx + DB state.

    Strips the template's surrounding labels — we just want the totals
    band + the product table for at-a-glance verification. If reading the
    xlsx fails for any reason (storage hiccup, unsupported format), fall
    back to a plain text card so the modal still renders something useful.
    """
    import html as _html
    import io

    from infrastructure.storage import get_storage

    name = supplier_state.supplier_name or f"供应商 #{supplier_state.supplier_id}"
    subtotal = supplier_state.subtotal
    currency = supplier_state.currency or ""
    product_count = supplier_state.product_count or 0

    try:
        from openpyxl import load_workbook

        content = get_storage().download(supplier_state.excel_file_url)
        wb = load_workbook(io.BytesIO(content), data_only=False)
        ws = wb.active
    except Exception as exc:
        logger.warning("preview render: cannot open xlsx: %s", exc)
        return _fallback_preview_html(
            name=name,
            product_count=product_count,
            subtotal=subtotal,
            currency=currency,
            reason="无法读取已生成的 Excel 文件",
        )

    # Find the product band heuristically: scan column D for non-empty rows
    # (the renderer fills D=product_name_en) starting somewhere reasonable.
    rows: list[tuple[Any, Any, Any, Any, Any, Any]] = []
    for r in range(10, 250):
        name_cell = ws.cell(row=r, column=4).value
        if not name_cell:
            if rows:  # we already found data — first gap ends the band
                break
            continue
        rows.append(
            (
                ws.cell(row=r, column=1).value,  # No
                ws.cell(row=r, column=3).value,  # code
                name_cell,  # name
                ws.cell(row=r, column=8).value,  # qty
                ws.cell(row=r, column=9).value,  # unit
                ws.cell(row=r, column=11).value,  # unit_price
            )
        )

    rows_html = "".join(
        f"<tr><td>{_html.escape(str(r[0] or ''))}</td>"
        f"<td>{_html.escape(str(r[1] or ''))}</td>"
        f"<td>{_html.escape(str(r[2] or ''))}</td>"
        f"<td class='num'>{_html.escape(str(r[3] or ''))}</td>"
        f"<td>{_html.escape(str(r[4] or ''))}</td>"
        f"<td class='num'>{_html.escape(str(r[5] or ''))}</td></tr>"
        for r in rows
    ) or "<tr><td colspan='6' class='empty'>暂无产品数据</td></tr>"

    subtotal_str = (
        f"{currency} {subtotal:,.2f}" if isinstance(subtotal, (int, float)) else "—"
    )

    return f"""<div class="inquiry-preview">
  <style>
    .inquiry-preview {{font:13px/1.5 system-ui,sans-serif;color:#1f2937;}}
    .inquiry-preview h3 {{margin:0 0 6px;font-size:14px;}}
    .inquiry-preview .meta {{color:#6b7280;font-size:12px;margin-bottom:12px;}}
    .inquiry-preview table {{width:100%;border-collapse:collapse;font-size:12px;}}
    .inquiry-preview th,.inquiry-preview td {{padding:6px 8px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;}}
    .inquiry-preview thead th {{background:#f3f4f6;font-weight:600;}}
    .inquiry-preview td.num {{text-align:right;font-variant-numeric:tabular-nums;}}
    .inquiry-preview td.empty {{text-align:center;color:#9ca3af;padding:18px 0;}}
    .inquiry-preview .totals {{margin-top:10px;text-align:right;font-size:13px;}}
    .inquiry-preview .totals strong {{margin-left:8px;}}
  </style>
  <h3>{_html.escape(name)}</h3>
  <div class="meta">产品 {product_count} 项 · 模板: {_html.escape((supplier_state.template.name if supplier_state.template else None) or '未绑定')}</div>
  <table>
    <thead><tr><th>#</th><th>产品代码</th><th>品名</th><th>数量</th><th>单位</th><th>单价</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  <div class="totals">小计 <strong>{_html.escape(subtotal_str)}</strong></div>
</div>"""


def _fallback_preview_html(
    *, name: str, product_count: int, subtotal: Any, currency: str, reason: str
) -> str:
    import html as _html

    subtotal_str = (
        f"{currency} {subtotal:,.2f}" if isinstance(subtotal, (int, float)) else "—"
    )
    return f"""<div style="font:13px system-ui,sans-serif;color:#1f2937;">
  <h3 style="margin:0 0 6px;">{_html.escape(name)}</h3>
  <p style="margin:0 0 6px;color:#6b7280;">产品 {product_count} 项 · 小计 {_html.escape(subtotal_str)}</p>
  <p style="color:#dc2626;">{_html.escape(reason)} — 请点击「下载」获取完整 Excel。</p>
</div>"""


@router.get("/{order_id}/inquiry-data-preview/{supplier_id}")
def inquiry_data_preview(
    order_id: int,
    supplier_id: int,
    db: DbDep,
    user: CurrentUser,
    template_id: int | None = Query(None),
) -> dict[str, Any]:
    """Return a flat, frontend-shaped JSON describing what would go into the
    supplier's inquiry sheet — products, template, formula columns, totals.

    The v3 frontend's `InquiryDataPreview` type assumes a flat schema; the
    earlier `{data: {...}}` wrapper made every `.template.name` access
    throw "Cannot read properties of undefined (reading 'name')".
    """
    _ensure_order_visible(db, order_id=order_id, user=user)
    state = inquiry_service.read_inquiry_state(db, order_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "尚未生成询价")
    supplier_state = next(
        (s for s in state.suppliers if s.supplier_id == supplier_id), None
    )
    if supplier_state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未找到该供应商的询价数据")

    # Late imports — these are heavier modules and only needed on the preview
    # path. The endpoint is otherwise a thin reader.
    from domains.inquiry.models import SupplierTemplate
    from domains.orders.models import Order

    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")

    # Honour the explicit template override the frontend passed (user picked a
    # different template in the dropdown). Otherwise use whichever template
    # the supplier was last generated with.
    bound_template_id = (
        template_id
        if template_id is not None
        else (supplier_state.template.id if supplier_state.template else None)
    )
    template = (
        db.get(SupplierTemplate, bound_template_id) if bound_template_id else None
    )
    template_contract = normalized_contract(template) if template else None
    columns_map: dict[str, str] = (
        template_contract["columns"] if template_contract else {}
    )
    formula_cols: list[str] = sorted(
        template_contract["formula_columns"] if template_contract else []
    )

    inquiry = inquiry_service.get_inquiry_for_order(db, order_id)
    source_results = (
        inquiry.match_snapshot
        if inquiry is not None and inquiry.group_id is not None
        else order.match_results
    ) or []

    # Materialise the per-supplier product list from the exact inquiry-version
    # snapshot (or legacy order match_results). Each
    # row mirrors what the renderer would have access to, flattened so the
    # frontend's `p[field]` lookups (where `field` ∈ columns_map.values())
    # find what they expect without descending into `matched_product`.
    products: list[dict[str, Any]] = []
    for mr in source_results:
        if not isinstance(mr, dict):
            continue
        matched = mr.get("matched_product") or {}
        if not isinstance(matched, dict):
            continue
        if matched.get("supplier_id") != supplier_id:
            continue
        idx = len(products) + 1
        products.append(
            {
                "_index": idx,
                "line_number": idx,
                "po_number": (
                    mr.get("source_po_number")
                    or mr.get("po_number")
                    or order.po_number
                    or ""
                ),
                "product_code": mr.get("product_code") or matched.get("code") or "",
                "product_name": (
                    mr.get("product_name") or matched.get("product_name_en") or ""
                ),
                "product_name_en": (
                    mr.get("product_name") or matched.get("product_name_en") or ""
                ),
                "product_name_jp": matched.get("product_name_jp") or "",
                "description": matched.get("pack_size") or mr.get("description") or "",
                "quantity": mr.get("quantity"),
                "unit": matched.get("unit") or mr.get("unit") or "",
                "unit_price": matched.get("price"),
                "currency": (
                    mr.get("currency")
                    or matched.get("currency")
                    or order.currency
                    or ""
                ),
                "item_amount": None,  # filled by template formula `=H*K`, not us
            }
        )

    has_zone_config = bool(
        template
        and template.template_styles
        and isinstance(template.template_styles, dict)
        and template.template_styles.get("zones")
    )

    return {
        "supplier_id": supplier_id,
        "supplier_name": supplier_state.supplier_name
        or f"供应商 #{supplier_id}",
        "template": {
            "id": template.id if template else None,
            "name": template.template_name if template else None,
            "method": (
                supplier_state.template.method
                if supplier_state.template
                else "unavailable"
            ),
            "has_zone_config": has_zone_config,
        },
        "header_fields": [],
        "field_overrides": {},
        "product_columns": (
            sorted(columns_map.items()) if columns_map else None
        ),
        "formula_columns": formula_cols or None,
        "summary_formulas": (
            (template_contract["styles"].get("summary_formulas") or [])
            if template_contract
            else None
        ),
        "products": products,
        "total_products": len(products),
        "warnings": [],
        "order_metadata": {
            "po_number": order.po_number or "",
            "ship_name": order.ship_name or "",
            "delivery_date": order.delivery_date or "",
            "currency": order.currency or "",
        },
    }


# ─── Mutating endpoints ───────────────────────────────────────


@router.post("/{order_id}/generate-inquiry")
async def generate_inquiry(order_id: int, db: DbDep, user: Writer) -> dict[str, Any]:
    """Kick off a full inquiry run. Returns 202 immediately — clients should
    open `GET /inquiry-stream` to observe progress."""
    _ensure_order_visible(db, order_id=order_id, user=user)

    handle = _inquiry_streams.register(order_id)
    handle.attach_loop(asyncio.get_running_loop())
    sink = handle.make_sink()

    async def _job() -> None:
        try:
            await asyncio.to_thread(orchestrator.run_inquiry, order_id, sink=sink)
        except Exception as exc:
            logger.exception("inquiry: order %d failed: %s", order_id, exc)
            sink.emit({"type": "run_error", "order_id": order_id, "error": str(exc)})
        finally:
            handle.completed = True

    job_id = get_job_runner().submit(_job, job_id=f"inquiry-{order_id}")
    return {"ok": True, "order_id": order_id, "job_id": job_id, "status": "in_progress"}


@router.post("/{order_id}/generate-inquiry/{supplier_id}")
async def generate_inquiry_for_supplier(
    order_id: int,
    supplier_id: int,
    db: DbDep,
    user: Writer,
    template_id: int | None = None,
) -> dict[str, Any]:
    """Re-do a single supplier's inquiry. Same SSE stream as the full run."""
    _ensure_order_visible(db, order_id=order_id, user=user)

    handle = _inquiry_streams.register(order_id)
    handle.attach_loop(asyncio.get_running_loop())
    sink = handle.make_sink()

    async def _job() -> None:
        try:
            await asyncio.to_thread(
                orchestrator.run_inquiry_for_supplier,
                order_id,
                supplier_id,
                sink=sink,
                template_id=template_id,
            )
            sink.emit({"type": "run_completed", "order_id": order_id})
        except Exception as exc:
            logger.exception(
                "inquiry: order %d / supplier %d failed: %s", order_id, supplier_id, exc
            )
            sink.emit({"type": "run_error", "order_id": order_id, "error": str(exc)})
        finally:
            handle.completed = True

    job_id = get_job_runner().submit(_job, job_id=f"inquiry-{order_id}-{supplier_id}")
    return {
        "ok": True,
        "order_id": order_id,
        "supplier_id": supplier_id,
        "job_id": job_id,
        "status": "in_progress",
    }


@router.post("/{order_id}/cancel-inquiry")
def cancel_inquiry(order_id: int, db: DbDep, user: Writer) -> dict[str, Any]:
    """Signal the in-flight orchestrator to stop. Persists `cancel_requested_at`."""
    _ensure_order_visible(db, order_id=order_id, user=user)
    found = _inquiry_streams.cancel(order_id)
    try:
        orchestrator.request_cancel(db, order_id)
    except NotFound:
        if not found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "询价尚未启动") from None
    return {"ok": True, "order_id": order_id, "stream_found": found}


@router.post("/{order_id}/inquiry-field-overrides/{supplier_id}")
def inquiry_field_overrides(
    order_id: int,
    supplier_id: int,
    db: DbDep,
    user: Writer,  # noqa: ARG001
    overrides: dict[str, Any] | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    """Phase 5 stub — placeholder for per-supplier field overrides.

    The frontend posts a JSON dict of {field_key: value} that should override
    the orchestrator's defaults on the next regenerate. We accept the call to
    keep the API contract; persistence happens once the order_metadata edit
    flow is integrated (Phase 5b).
    """
    _ensure_order_visible(db, order_id=order_id, user=user)
    return {
        "ok": True,
        "order_id": order_id,
        "supplier_id": supplier_id,
        "applied": False,
        "note": "字段覆盖将在下一次生成时生效（Phase 5b）",
    }


# ─── SSE ──────────────────────────────────────────────────────


@router.get("/{order_id}/inquiry-stream")
async def inquiry_stream(order_id: int, db: DbDep, user: CurrentUser) -> StreamingResponse:
    """SSE endpoint — streams events from the active orchestrator run.

    If no run is active, emits a final `run_idle` event and closes.
    """
    _ensure_order_visible(db, order_id=order_id, user=user)

    async def _events() -> Any:
        handle = _inquiry_streams.get(order_id)
        if handle is None:
            yield _sse({"type": "run_idle", "order_id": order_id})
            return
        try:
            while True:
                try:
                    event = await asyncio.wait_for(handle.queue.get(), timeout=30.0)
                except TimeoutError:
                    yield _sse({"type": "ping"})
                    if handle.completed:
                        yield _sse({"type": "run_idle", "order_id": order_id})
                        return
                    continue
                yield _sse(event)
                if _inquiry_streams.is_terminal(event):
                    return
        finally:
            _inquiry_streams.remove(order_id)

    return StreamingResponse(_events(), media_type="text/event-stream")


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
