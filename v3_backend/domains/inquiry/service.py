"""Inquiry service — Phase 4 SupplierTemplate CRUD + Phase 5 state read.

The Phase 5 orchestrator + per-supplier worker live in
`domains.inquiry.orchestrator`; this module exposes the public read API
for the HTTP layer.
"""

from __future__ import annotations

import contextlib
import io
import uuid
from typing import Any

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from domains.inquiry import _state
from domains.inquiry import repository as repo
from domains.inquiry.errors import BadRequest, InquiryError, NotFound
from domains.inquiry.models import Inquiry, SupplierTemplate
from domains.inquiry.schemas import (
    InquiryState,
    SupplierTemplateCreate,
    SupplierTemplateUpdate,
)
from domains.inquiry.template_contract import (
    assert_source_workbook_matches,
    configuration_errors,
    normalized_contract,
)
from domains.settings import service as settings_service
from infrastructure.storage import get_storage

__all__ = [
    "InquiryError",
    "NotFound",
    "BadRequest",
    # SupplierTemplate (Phase 4)
    "list_supplier_templates",
    "create_supplier_template",
    "get_supplier_template",
    "update_supplier_template",
    "delete_supplier_template",
    "upload_supplier_template_file",
    "analyze_supplier_template",
    # Inquiry state (Phase 5)
    "read_inquiry_state",
    "list_group_inquiry_states",
    "get_inquiry_for_order",
]


# ─── SupplierTemplate (Phase 4) ───────────────────────────────


def list_supplier_templates(
    db: Session, *, supplier_id: int | None = None
) -> list[SupplierTemplate]:
    return repo.list_supplier_templates(db, supplier_id=supplier_id)


def create_supplier_template(
    db: Session, body: SupplierTemplateCreate, *, created_by: int
) -> SupplierTemplate:
    _validate_template_configuration(
        field_positions=body.field_positions,
        product_table_config=body.product_table_config,
        template_styles=body.template_styles,
    )
    tpl = SupplierTemplate(**body.model_dump(), created_by=created_by)
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


def get_supplier_template(db: Session, tpl_id: int) -> SupplierTemplate:
    tpl = repo.get_supplier_template(db, tpl_id)
    if tpl is None:
        raise NotFound("供应商模板不存在")
    return tpl


def update_supplier_template(
    db: Session, tpl_id: int, body: SupplierTemplateUpdate
) -> SupplierTemplate:
    tpl = get_supplier_template(db, tpl_id)
    changes = body.model_dump(exclude_unset=True)
    _validate_template_configuration(
        field_positions=changes.get("field_positions", tpl.field_positions),
        product_table_config=changes.get(
            "product_table_config", tpl.product_table_config
        ),
        template_styles=changes.get("template_styles", tpl.template_styles),
    )
    for key, val in changes.items():
        setattr(tpl, key, val)
    db.commit()
    db.refresh(tpl)
    return tpl


def delete_supplier_template(db: Session, tpl_id: int) -> None:
    tpl = get_supplier_template(db, tpl_id)
    if tpl.template_file_url:
        with contextlib.suppress(Exception):
            get_storage().delete(tpl.template_file_url)
    db.delete(tpl)
    db.commit()


def upload_supplier_template_file(
    db: Session, tpl_id: int, *, filename: str, content: bytes
) -> SupplierTemplate:
    if not filename.lower().endswith(".xlsx"):
        raise BadRequest("请上传 .xlsx 文件")
    if not content:
        raise BadRequest("文件为空")
    if len(content) > 25 * 1024 * 1024:
        raise BadRequest("文件大小不能超过 25 MB")

    tpl = get_supplier_template(db, tpl_id)
    if normalized_contract(tpl)["is_dynamic"]:
        try:
            workbook = load_workbook(io.BytesIO(content), read_only=False)
            assert_source_workbook_matches(workbook.active, tpl)
        except BadRequest:
            raise
        except Exception as exc:
            raise BadRequest(f"模板文件与当前结构配置不匹配：{exc}") from exc

    storage = get_storage()
    if tpl.template_file_url:
        with contextlib.suppress(Exception):
            storage.delete(tpl.template_file_url)

    safe = f"template_{uuid.uuid4().hex[:8]}_{filename}"
    file_url = storage.upload(
        "templates",
        safe,
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    tpl.template_file_url = file_url
    db.commit()
    db.refresh(tpl)
    return tpl


def _validate_template_configuration(
    *,
    field_positions: dict[str, Any] | None,
    product_table_config: dict[str, Any] | None,
    template_styles: dict[str, Any] | None,
) -> None:
    errors = configuration_errors(
        field_positions=field_positions,
        product_table_config=product_table_config,
        template_styles=template_styles,
    )
    if errors:
        raise BadRequest("模板配置无效：" + "；".join(errors))


def analyze_supplier_template(
    *, filename: str, content: bytes, order_template_id: int | None = None
) -> dict[str, Any]:
    """Analyze an uploaded Excel template — Phase 4 deterministic implementation.

    Persists the upload to storage so the caller has a reusable URL, then
    runs `domains.settings.service.analyze_excel_template` for cell + field
    analysis. `order_template_id` is accepted for shape compat with v2; the
    Phase 4 analysis is order-context-agnostic — Phase 5 will use Gemini to
    produce a `field_mapping_preview` against the order template.
    """
    if not filename.lower().endswith((".xlsx", ".xls")):
        raise BadRequest("请上传 .xlsx 文件")
    if not content:
        raise BadRequest("文件为空")

    storage = get_storage()
    safe = f"template_{uuid.uuid4().hex[:8]}_{filename}"
    file_url = storage.upload(
        "templates",
        safe,
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    result = settings_service.analyze_excel_template(content)
    result["file_url"] = file_url
    if order_template_id is not None:
        result["order_template_id"] = order_template_id
    return result


# ─── Inquiry state (Phase 5) ──────────────────────────────────


def get_inquiry_for_order(db: Session, order_id: int) -> Inquiry | None:
    return repo.get_inquiry_by_order(db, order_id)


def read_inquiry_state(db: Session, order_id: int) -> InquiryState | None:
    """Return InquiryState for an order, or None if no inquiry has been kicked off."""
    inquiry = repo.get_inquiry_by_order(db, order_id)
    if inquiry is None:
        return None
    return _state.to_state(db, inquiry)


def list_group_inquiry_states(db: Session, group_id: int) -> list[InquiryState]:
    """Return all arrangement versions, newest first."""
    return [_state.to_state(db, row) for row in repo.list_inquiries_by_group(db, group_id)]


def delete_inquiry_for_order(db: Session, order_id: int) -> list[str]:
    """Cascade-delete the Inquiry + its InquirySupplier rows for an order.

    Returns the list of file URLs that the caller's storage layer should
    clean up (per-supplier inquiry Excel files). Storage cleanup stays on
    the caller because the orders domain owns the lifecycle of the
    Order's PO file alongside, and batching one storage round-trip is
    cheaper than two.

    Added 2026-06-16 (TD-1) so the orders domain can issue this cascade
    without importing `Inquiry` / `InquirySupplier` directly.
    """
    from domains.inquiry.models import Inquiry, InquirySupplier

    inquiries = db.query(Inquiry).filter(Inquiry.order_id == order_id).all()
    if not inquiries:
        return []
    file_urls: list[str] = []
    inquiry_ids = [inquiry.id for inquiry in inquiries]
    supplier_rows = (
        db.query(InquirySupplier)
        .filter(InquirySupplier.inquiry_id.in_(inquiry_ids))
        .all()
    )
    for row in supplier_rows:
        if row.excel_file_url:
            file_urls.append(row.excel_file_url)
        db.delete(row)
    db.flush()
    for inquiry in inquiries:
        db.delete(inquiry)
    db.flush()
    return file_urls
