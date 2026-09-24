"""Section 5 — Inquiry: HTTP endpoint contract tests.

测试目标：
    三个相关的 prod bug 在一次会话里全暴露：下载用的 URL/方法在后端不存在，
    inquiry-preview 给了 JSON 但前端期望 HTML，inquiry-data-preview 把数据
    包了一层 {data: ...} 让前端 `preview.template.name` 直接报
    `Cannot read properties of undefined`。

    domain 层单元测试抓不到这些 —— 这是 contract-shaped 失败，发生在
    FastAPI handler 边界。所以这个文件在边界上测，用真 TestClient + 真存储
    + 真 ORM 行。

为什么重要：
    每一条 bug 都让用户看到"询价完成"但实际功能用不了（按钮不出现 / 看到
    乱码 / 弹窗白屏）。回归同样契约的代价远低于用户工单。

设计方法：
    - `_seed_order_with_inquiry` 准备一个完整可用的 Order + Inquiry +
      supplier 行 + 真实写入 storage 的 xlsx。
    - 用 superadmin 登录绕过 order ownership 检查（与功能无关）。
    - 每个测试只校验一条 contract 字段或一个 HTTP 行为，方便回归定位。
"""

from __future__ import annotations

import io

from openpyxl import Workbook

from domains.inquiry import _state
from domains.inquiry.models import SupplierTemplate
from domains.orders.models import Order
from test_v2.fixtures.helpers import login, seed_user

# ─── Helpers ──────────────────────────────────────────────────


def _make_xlsx_bytes() -> bytes:
    """Tiny but real xlsx — enough that openpyxl reopens it cleanly in
    the preview-HTML rendering path."""
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "test"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_pdf_bytes() -> bytes:
    """Bare-bones PDF header — content unimportant, we only care that the
    download endpoint returns a 200 with PDF content-type for the original
    order document path."""
    return b"%PDF-1.4\n%fake test pdf\n%%EOF"


def _seed_order_with_inquiry(
    db,
    storage,
    *,
    user_id: int,
    supplier_id: int = 100,
    template: SupplierTemplate | None = None,
):
    """Set up: Order + original-doc-in-storage + Inquiry + 1 completed
    supplier row + that supplier's xlsx in storage. Returns
    (order, inquiry_id, inquiry_filename, original_filename).
    """
    # 1) Stash an "original" PDF and a generated inquiry xlsx in storage.
    orig_key = storage.upload("orders", "po-test.pdf", _make_pdf_bytes(), "application/pdf")
    inquiry_key = storage.upload(
        "inquiries",
        f"inquiry_X_{supplier_id}.xlsx",
        _make_xlsx_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # 2) Order row pointing at the original.
    order = Order(
        user_id=user_id,
        filename="po-test.pdf",
        file_type="pdf",
        file_url=orig_key,
        po_number="PO-T",
        ship_name="MV Test",
        currency="USD",
        delivery_date="2026-05-30",
        match_results=[
            {
                "product_code": "A1",
                "product_name": "Apple",
                "quantity": 5,
                "unit_price": 2.0,
                "matched_product": {
                    "id": 11,
                    "supplier_id": supplier_id,
                    "code": "A1",
                    "unit": "kg",
                    "price": 2.0,
                },
            }
        ],
    )
    db.add(order)
    if template is not None:
        db.add(template)
    db.commit()
    db.refresh(order)

    # 3) Inquiry + supplier row already in `completed` state.
    inquiry = _state.ensure_inquiry(db, order_id=order.id)
    inquiry.status = "completed"
    inquiry.supplier_count = 1
    db.commit()
    _state.upsert_supplier(
        db,
        inquiry_id=inquiry.id,
        supplier_id=supplier_id,
        fields={
            "supplier_name": "Apple Inc",
            "supplier_info": {},
            "product_count": 1,
            "subtotal": 10.0,
            "currency": "USD",
            "template_id": template.id if template else None,
            "template_name": template.template_name if template else None,
            "template_selection_method": "exact",
            "status": "completed",
            "excel_file_url": inquiry_key,
            "preview_html_url": None,
        },
    )
    db.commit()

    return order, inquiry.id, inquiry_key.rsplit("/", 1)[-1], order.filename


# ─── POST /files/{filename}/download (the missing endpoint) ────


def test_post_files_download_streams_inquiry_excel(client, db, _local_storage):
    """The v3 frontend hits `POST .../files/{name}/download` to download
    inquiry Excels. Pre-fix the route didn't exist at all (404), and even
    when added it only matched the original order doc — not the per-
    supplier inquiry xlsx."""
    admin = seed_user(db, email="admin@example.com", role="superadmin")
    headers = login(client, "admin@example.com")
    order, _, inquiry_filename, _ = _seed_order_with_inquiry(
        db, _local_storage, user_id=admin.id
    )

    r = client.post(
        f"/api/orders/{order.id}/files/{inquiry_filename}/download", headers=headers
    )
    assert r.status_code == 200, r.text
    ctype = r.headers["content-type"]
    assert "spreadsheetml" in ctype, f"expected xlsx mime, got {ctype}"
    # Real xlsx → magic bytes are PK (zip).
    assert r.content[:2] == b"PK", "response body is not a valid xlsx"


def test_post_files_download_also_works_for_original_order_doc(
    client, db, _local_storage
):
    """Same POST endpoint must still resolve the ORIGINAL filename — we
    can't have shipped the inquiry path at the cost of breaking original
    document downloads."""
    admin = seed_user(db, email="admin@example.com", role="superadmin")
    headers = login(client, "admin@example.com")
    order, _, _, orig_filename = _seed_order_with_inquiry(
        db, _local_storage, user_id=admin.id
    )

    r = client.post(
        f"/api/orders/{order.id}/files/{orig_filename}/download", headers=headers
    )
    assert r.status_code == 200
    assert "pdf" in r.headers["content-type"]


def test_files_download_unknown_filename_returns_404(client, db, _local_storage):
    """Filename not matching original OR any inquiry supplier → 404, not
    500 — important because the frontend shows a toast based on this."""
    admin = seed_user(db, email="admin@example.com", role="superadmin")
    headers = login(client, "admin@example.com")
    order, _, _, _ = _seed_order_with_inquiry(db, _local_storage, user_id=admin.id)

    r = client.post(
        f"/api/orders/{order.id}/files/does_not_exist.xlsx/download", headers=headers
    )
    assert r.status_code == 404


# ─── GET /inquiry-preview/{sid} — must be HTML, not JSON ──────


def test_inquiry_preview_returns_html_not_json(client, db, _local_storage):
    """The frontend's `getInquiryPreview` reads `res.text()` and renders
    it as HTML inside a modal. If the backend returns JSON the user sees
    a raw JSON string in the modal (real prod symptom 2026-05)."""
    admin = seed_user(db, email="admin@example.com", role="superadmin")
    headers = login(client, "admin@example.com")
    order, _, _, _ = _seed_order_with_inquiry(db, _local_storage, user_id=admin.id)

    r = client.get(f"/api/orders/{order.id}/inquiry-preview/100", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html"), (
        f"preview must be HTML; got content-type={r.headers['content-type']}"
    )
    body = r.text.lstrip()
    # JSON would start with `{` — fast smell test.
    assert not body.startswith("{"), (
        "preview body looks like JSON; frontend will render it as raw text"
    )
    # Loose check the response is plausibly HTML.
    assert "<" in body


def test_inquiry_preview_404_when_supplier_unknown(client, db, _local_storage):
    """Asking for preview of a supplier we never generated → 404."""
    admin = seed_user(db, email="admin@example.com", role="superadmin")
    headers = login(client, "admin@example.com")
    order, _, _, _ = _seed_order_with_inquiry(db, _local_storage, user_id=admin.id)

    r = client.get(f"/api/orders/{order.id}/inquiry-preview/9999", headers=headers)
    assert r.status_code == 404


# ─── GET /inquiry-data-preview/{sid} — must be FLAT ──────────


def test_inquiry_data_preview_returns_flat_shape(client, db, _local_storage):
    """The v3 frontend's `InquiryDataPreview` TS type expects fields at
    the top level. The earlier `{data: {...}}` wrapper made
    `preview.template.name` throw because `preview.template` was
    undefined. This pins the flat schema."""
    admin = seed_user(db, email="admin@example.com", role="superadmin")
    headers = login(client, "admin@example.com")
    tpl = SupplierTemplate(
        template_name="Standard",
        supplier_ids=[100],
        field_positions={"po_number": "A1"},
        product_table_config={
            "start_row": 22,
            "columns": {"A": "line_number", "C": "product_code", "H": "quantity"},
            "formula_columns": ["M"],
        },
        template_styles={"zones": {"meta": {}}},
        has_product_table=True,
    )
    order, _, _, _ = _seed_order_with_inquiry(
        db, _local_storage, user_id=admin.id, template=tpl
    )

    r = client.get(f"/api/orders/{order.id}/inquiry-data-preview/100", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    # The fatal regression sentinel: NO `data` wrapper.
    assert "data" not in body, (
        "response is wrapped in `data` again — frontend `.template.name` "
        "will throw `Cannot read properties of undefined`"
    )

    # All keys the frontend's InquiryDataPreview interface requires must
    # be present at the top level (value can be empty / null, but the key
    # must exist so `.length` / `.map` etc. don't crash).
    required_top_level = (
        "supplier_id",
        "supplier_name",
        "template",
        "header_fields",
        "field_overrides",
        "product_columns",
        "formula_columns",
        "summary_formulas",
        "products",
        "total_products",
        "warnings",
        "order_metadata",
    )
    for key in required_top_level:
        assert key in body, f"missing top-level key: {key!r}"


def test_inquiry_data_preview_template_has_name_and_method(client, db, _local_storage):
    """`template.name` (the .name access that exploded in prod) and
    `template.method` (used to render the binding-type badge)."""
    admin = seed_user(db, email="admin@example.com", role="superadmin")
    headers = login(client, "admin@example.com")
    tpl = SupplierTemplate(
        template_name="Bound Template",
        supplier_ids=[100],
        field_positions={"po_number": "A1"},
        product_table_config={"start_row": 19, "columns": {}},
        template_styles={"zones": {"meta": {}}},
        has_product_table=True,
    )
    order, _, _, _ = _seed_order_with_inquiry(
        db, _local_storage, user_id=admin.id, template=tpl
    )

    r = client.get(f"/api/orders/{order.id}/inquiry-data-preview/100", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["template"]["name"] == "Bound Template"
    assert body["template"]["method"] == "exact"
    # has_zone_config drives a UI badge — must always be a bool, never missing.
    assert isinstance(body["template"]["has_zone_config"], bool)


def test_inquiry_data_preview_products_have_required_fields(
    client, db, _local_storage
):
    """Each product must carry the fields the dialog's table cells read:
    _index for the row number, plus a key per `product_columns` mapping
    field (product_code / quantity / unit_price …)."""
    admin = seed_user(db, email="admin@example.com", role="superadmin")
    headers = login(client, "admin@example.com")
    order, _, _, _ = _seed_order_with_inquiry(db, _local_storage, user_id=admin.id)

    r = client.get(f"/api/orders/{order.id}/inquiry-data-preview/100", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total_products"] == 1
    assert len(body["products"]) == 1
    p = body["products"][0]
    # Row index — page.tsx renders this in the first column.
    assert p["_index"] == 1
    # Field keys the column map points at — table cells do `p[field]`.
    assert p["product_code"] == "A1"
    assert p["quantity"] == 5
    assert p["unit_price"] == 2.0


def test_inquiry_data_preview_returns_saved_row_warning_messages(
    client, db, _local_storage
):
    admin = seed_user(db, email="admin@example.com", role="superadmin")
    headers = login(client, "admin@example.com")
    order, _, _, _ = _seed_order_with_inquiry(
        db, _local_storage, user_id=admin.id
    )
    order.match_results = [
        {
            **order.match_results[0],
            "inquiry_warnings": [
                {
                    "code": "SELLING_PRICE_DEVIATION",
                    "severity": "warning",
                    "message": "客户 PO 单价与有效卖价偏差较大",
                }
            ],
        }
    ]
    db.commit()

    response = client.get(
        f"/api/orders/{order.id}/inquiry-data-preview/100", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["warnings"] == ["客户 PO 单价与有效卖价偏差较大"]


def test_inquiry_data_preview_404_when_supplier_unknown(client, db, _local_storage):
    admin = seed_user(db, email="admin@example.com", role="superadmin")
    headers = login(client, "admin@example.com")
    order, _, _, _ = _seed_order_with_inquiry(db, _local_storage, user_id=admin.id)

    r = client.get(f"/api/orders/{order.id}/inquiry-data-preview/9999", headers=headers)
    assert r.status_code == 404
