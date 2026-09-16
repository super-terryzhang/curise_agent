"""Section 5 — Inquiry: state helpers + repository + schema serialization.

测试目标：
    _state.py 是 orchestrator 写状态的唯一窗口（ensure_inquiry / upsert_supplier /
    reset_for_run），repository.py 是读侧的窗口，schemas.py 是给前端的 v2-shaped
    dict 翻译器。三层一起做"状态机"。

为什么重要：
    - ensure_inquiry 必须幂等（同一个 order 多次 pre_analyze 不能造成多行）。
    - upsert_supplier 必须 insert-or-update（同一 supplier 跑多次只更新一行）。
    - 状态在 ORM 和 schema 之间往返必须保留 template binding（v2 frontend 靠
      `suppliers["7"].template.selection_method` 路由）。
    - 状态字符串本身不被代码常量化，但所有支持的 transition 都要走通。

设计方法：
    用 db fixture 直接调 _state / repo / schema 函数，断言 ORM 行 + Pydantic
    模型 + legacy dict 三层一致。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from domains.inquiry import _state
from domains.inquiry import repository as repo
from domains.inquiry.models import Inquiry, InquirySupplier
from domains.inquiry.schemas import (
    InquiryState,
    InquirySupplierState,
    TemplateBinding,
)


# ─── ensure_inquiry ───────────────────────────────────────────


def test_ensure_inquiry_creates_when_missing(db):
    inquiry = _state.ensure_inquiry(db, order_id=42)
    db.commit()
    assert inquiry.id is not None
    assert inquiry.order_id == 42
    assert inquiry.status == "pending"


def test_ensure_inquiry_returns_existing_when_present(db):
    first = _state.ensure_inquiry(db, order_id=42)
    db.commit()
    second = _state.ensure_inquiry(db, order_id=42)
    assert second.id == first.id
    # Only one row in DB.
    rows = db.query(Inquiry).filter(Inquiry.order_id == 42).all()
    assert len(rows) == 1


def test_ensure_inquiry_isolated_per_order(db):
    a = _state.ensure_inquiry(db, order_id=1)
    b = _state.ensure_inquiry(db, order_id=2)
    db.commit()
    assert a.id != b.id


# ─── reset_for_run ────────────────────────────────────────────


def test_reset_for_run_clears_terminal_state(db):
    inquiry = _state.ensure_inquiry(db, order_id=7)
    inquiry.status = "completed"
    inquiry.completed_at = datetime.utcnow()
    inquiry.total_elapsed_seconds = 99.9
    inquiry.cancel_requested_at = datetime.utcnow()
    db.commit()

    _state.reset_for_run(
        db,
        inquiry,
        started_at=datetime(2026, 5, 11, 12, 0, 0),
        supplier_count=4,
        unassigned_count=1,
    )
    db.commit()
    db.refresh(inquiry)

    assert inquiry.status == "in_progress"
    assert inquiry.started_at == datetime(2026, 5, 11, 12, 0, 0)
    assert inquiry.completed_at is None
    assert inquiry.cancel_requested_at is None
    assert inquiry.total_elapsed_seconds is None
    assert inquiry.supplier_count == 4
    assert inquiry.unassigned_count == 1


# ─── upsert_supplier ──────────────────────────────────────────


def test_upsert_supplier_inserts_then_updates(db):
    inquiry = _state.ensure_inquiry(db, order_id=1)
    db.commit()

    row = _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=100,
        fields={"status": "pending", "product_count": 3, "currency": "USD"},
    )
    db.commit()
    assert row.id is not None
    assert row.status == "pending"
    assert row.product_count == 3

    # Second call updates the same row.
    same = _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=100,
        fields={"status": "generating", "product_count": 5},
    )
    db.commit()
    assert same.id == row.id
    assert same.status == "generating"
    assert same.product_count == 5
    # currency from the first call is unchanged (partial update).
    assert same.currency == "USD"

    rows = db.query(InquirySupplier).filter(
        InquirySupplier.inquiry_id == inquiry.id,
        InquirySupplier.supplier_id == 100,
    ).all()
    assert len(rows) == 1


def test_upsert_supplier_status_transitions(db):
    """All documented status values should round-trip through upsert."""
    inquiry = _state.ensure_inquiry(db, order_id=1)
    db.commit()
    for status in ("pending", "generating", "completed", "error", "cancelled"):
        row = _state.upsert_supplier(
            db,
            inquiry_id=inquiry.id,
            supplier_id=100,
            fields={"status": status},
        )
        db.commit()
        assert row.status == status


def test_upsert_supplier_isolated_per_supplier(db):
    inquiry = _state.ensure_inquiry(db, order_id=1)
    db.commit()
    a = _state.upsert_supplier(
        db, inquiry_id=inquiry.id, supplier_id=100, fields={"status": "pending"}
    )
    b = _state.upsert_supplier(
        db, inquiry_id=inquiry.id, supplier_id=200, fields={"status": "pending"}
    )
    db.commit()
    assert a.id != b.id


# ─── repository read paths ────────────────────────────────────


def test_repository_list_inquiry_suppliers_sorted_by_supplier_id(db):
    inquiry = _state.ensure_inquiry(db, order_id=1)
    db.commit()
    for sid in (300, 100, 200):
        _state.upsert_supplier(
            db, inquiry_id=inquiry.id, supplier_id=sid, fields={"status": "pending"}
        )
    db.commit()
    rows = repo.list_inquiry_suppliers(db, inquiry.id)
    assert [r.supplier_id for r in rows] == [100, 200, 300]


def test_repository_get_inquiry_supplier_returns_none_when_missing(db):
    inquiry = _state.ensure_inquiry(db, order_id=1)
    db.commit()
    assert repo.get_inquiry_supplier(db, inquiry.id, 999) is None


def test_repository_get_inquiry_by_order_returns_none_for_unknown_order(db):
    assert repo.get_inquiry_by_order(db, 999) is None


# ─── to_state (ORM → schema) ──────────────────────────────────


def test_to_state_roundtrip_serialization(db):
    """ORM rows → InquiryState schema with nested template binding intact."""
    inquiry = _state.ensure_inquiry(db, order_id=42)
    inquiry.status = "completed"
    inquiry.supplier_count = 1
    inquiry.total_elapsed_seconds = 12.5
    db.commit()

    _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=100,
        fields={
            "supplier_name": "Apple Inc",
            "supplier_info": {"email": "a@x.com"},
            "product_count": 3,
            "subtotal": 25.5,
            "currency": "USD",
            "template_id": 7,
            "template_name": "Apple Default",
            "template_selection_method": "exact",
            "status": "completed",
            "excel_file_url": "tests/uploads/foo.xlsx",
            "verify_results": [{"field": "po_number", "ok": True}],
            "elapsed_seconds": 4.0,
        },
    )
    db.commit()

    state = _state.to_state(db, inquiry)
    assert isinstance(state, InquiryState)
    assert state.id == inquiry.id
    assert state.order_id == 42
    assert state.status == "completed"
    assert state.total_elapsed_seconds == 12.5
    assert len(state.suppliers) == 1

    s = state.suppliers[0]
    assert isinstance(s, InquirySupplierState)
    assert s.supplier_id == 100
    assert s.supplier_name == "Apple Inc"
    assert s.product_count == 3
    # Template binding intact.
    assert isinstance(s.template, TemplateBinding)
    assert s.template.id == 7
    assert s.template.name == "Apple Default"
    assert s.template.method == "exact"
    assert s.excel_file_url.endswith("foo.xlsx")
    assert s.verify_results == [{"field": "po_number", "ok": True}]


def test_to_state_template_is_none_when_no_binding_fields(db):
    inquiry = _state.ensure_inquiry(db, order_id=1)
    db.commit()
    _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=100,
        fields={"status": "pending"},  # no template_* fields
    )
    db.commit()
    state = _state.to_state(db, inquiry)
    assert state.suppliers[0].template is None


# ─── to_legacy_dict (v2 frontend shape) ───────────────────────


def test_legacy_dict_shape_for_backwards_compat(db):
    """v2 frontend expects `suppliers["7"]` dict and a flat `generated_files`."""
    inquiry = _state.ensure_inquiry(db, order_id=42)
    inquiry.status = "completed"
    inquiry.supplier_count = 2
    inquiry.unassigned_count = 1
    inquiry.total_elapsed_seconds = 9.9
    db.commit()

    _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=7,
        fields={
            "supplier_name": "Lucky Seven",
            "supplier_info": {"contact": "X"},
            "product_count": 2,
            "subtotal": 10.0,
            "currency": "USD",
            "template_id": 1,
            "template_name": "T",
            "template_selection_method": "exact",
            "status": "completed",
            "excel_file_url": "tests/uploads/seven.xlsx",
            "preview_html_url": "tests/uploads/seven.html",
        },
    )
    _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=8,
        fields={
            "supplier_name": "Eight",
            "supplier_info": {},
            "product_count": 1,
            "currency": "USD",
            "status": "error",
            "error_message": "boom",
        },
    )
    db.commit()

    legacy = _state.to_state(db, inquiry).to_legacy_dict()

    assert legacy["status"] == "completed"
    assert legacy["supplier_count"] == 2
    assert legacy["unassigned_count"] == 1
    assert legacy["total_elapsed_seconds"] == 9.9

    # suppliers is a string-keyed dict (the v2 shape).
    assert set(legacy["suppliers"].keys()) == {"7", "8"}
    seven = legacy["suppliers"]["7"]
    assert seven["status"] == "completed"
    assert seven["supplier_name"] == "Lucky Seven"
    # Template ships both legacy v2 key (`selection_method`) and v3-frontend
    # key (`method`) so callers on either side keep working.
    assert seven["template"]["id"] == 1
    assert seven["template"]["name"] == "T"
    assert seven["template"]["selection_method"] == "exact"
    assert seven["template"]["method"] == "exact"
    # File ships legacy v2 keys (`url`, `preview_html_url`) AND v3 keys
    # (`filename`, `file_url`, `preview_url`). The v3 frontend keys off
    # `filename` to decide whether to show the download button.
    assert seven["file"]["url"] == "tests/uploads/seven.xlsx"
    assert seven["file"]["preview_html_url"] == "tests/uploads/seven.html"

    # generated_files only contains rows with an excel_file_url.
    assert len(legacy["generated_files"]) == 1
    assert legacy["generated_files"][0]["supplier_id"] == 7

    # Failed supplier gets an `error` field but no `file`.
    eight = legacy["suppliers"]["8"]
    assert eight["status"] == "error"
    assert eight["error"] == "boom"
    assert "file" not in eight


def test_legacy_dict_default_template_when_unbound(db):
    """Suppliers with no template_id get the explicit "unavailable" stub."""
    inquiry = _state.ensure_inquiry(db, order_id=1)
    db.commit()
    _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=100,
        fields={"status": "pending", "currency": "USD"},
    )
    db.commit()
    legacy = _state.to_state(db, inquiry).to_legacy_dict()
    tpl = legacy["suppliers"]["100"]["template"]
    assert tpl == {
        "id": None,
        "name": None,
        "selection_method": "unavailable",
        "method": "unavailable",
    }


def test_legacy_dict_supplier_name_default_uses_supplier_id(db):
    """No supplier_name → "供应商 #100" fallback (v2 contract)."""
    inquiry = _state.ensure_inquiry(db, order_id=1)
    db.commit()
    _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=100,
        fields={"status": "pending"},
    )
    db.commit()
    legacy = _state.to_state(db, inquiry).to_legacy_dict()
    assert legacy["suppliers"]["100"]["supplier_name"] == "供应商 #100"


# ─── v3 frontend contract (regression: 2026-05 stuck-at-pending bug) ──
#
# After the inquiry orchestrator marked suppliers `completed` the UI still
# rendered "待生成" with no download buttons. Root cause: every field that
# decides UI behaviour (status pill, download CTA, template tag) used a
# different name on the frontend than on the backend, and `to_legacy_dict`
# only emitted the legacy v2 names. These tests pin the v3 names so the
# contract can't quietly drift again.


def test_legacy_dict_supplier_has_v3_required_keys(db):
    """v3 page.tsx reads: gen_status, template.method, file.filename,
    file.file_url, file.preview_url. Missing any of these silently breaks UI."""
    inquiry = _state.ensure_inquiry(db, order_id=1)
    inquiry.status = "completed"
    db.commit()
    _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=42,
        fields={
            "supplier_name": "S",
            "product_count": 3,
            "currency": "JPY",
            "template_id": 5,
            "template_name": "Standard",
            "template_selection_method": "exact",
            "status": "completed",
            "excel_file_url": "inquiries/inquiry_1_42_abcd1234.xlsx",
            "preview_html_url": None,
        },
    )
    db.commit()
    legacy = _state.to_state(db, inquiry).to_legacy_dict()
    sup = legacy["suppliers"]["42"]

    # Status pill — frontend checks `gen_status === "completed"`
    assert sup["gen_status"] == "completed", (
        "v3 frontend keys status pill off `gen_status`; missing it → 'pending'"
    )

    # Template tag — frontend uses `template.method` (not `selection_method`)
    assert sup["template"]["method"] == "exact"

    # Download CTA — frontend renders button only when `file.filename` truthy
    assert sup["file"]["filename"] == "inquiry_1_42_abcd1234.xlsx", (
        "filename must be the storage-key basename, not the full storage key"
    )
    # Frontend uses `file_url` for the download href hint and `preview_url`
    # for the preview iframe.
    assert "file_url" in sup["file"]
    assert "preview_url" in sup["file"]


def test_legacy_dict_filename_is_basename_not_full_storage_key(db):
    """If we ever start writing storage keys like `inquiries/x/y/z.xlsx`,
    the basename extraction must still strip every directory layer — the
    frontend uses this as a URL slug and slashes would break routing."""
    inquiry = _state.ensure_inquiry(db, order_id=2)
    db.commit()
    _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=7,
        fields={
            "status": "completed",
            "excel_file_url": "inquiries/sub/path/inquiry_x.xlsx",
        },
    )
    db.commit()
    legacy = _state.to_state(db, inquiry).to_legacy_dict()
    assert legacy["suppliers"]["7"]["file"]["filename"] == "inquiry_x.xlsx"
