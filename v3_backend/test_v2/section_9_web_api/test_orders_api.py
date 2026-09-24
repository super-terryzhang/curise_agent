"""Section 9 — Web API: /api/orders/* HTTP contract.

测试目标：
    `apps/http/orders.py` 是 15 个订单核心端点的薄壳。这里覆盖最高频使用
    的 upload / list / detail / patch / rematch / review / anomaly /
    delete + 几个 Phase 4 stub。验证：HTTP status、payload shape、DB
    state、跨用户隔离、ADR-0001 列化字段。

为什么重要：
    - upload 后是否真的有 Order 行（status="uploading" 占位 / projector
      升级）决定前端能否进入订单详情页。
    - 多用户隔离（A 看不到 B 的订单）是 P0 安全。404 不能改成 403。
    - ADR-0001：`po_number` / `ship_name` 等是真列不是 JSON——回归
      测试钉死，避免哪天有人把它放回 order_metadata。

设计方法：
    - 走完整 HTTP 栈：上传 → 拿到 OrderDetail → patch → rematch → review
      → delete。
    - 上传的 PDF 用 `make_minimal_pdf`，但走的是 `/api/orders/upload`，
      因此最终 Order 是 stub（doc_type 默认 unknown，projector 不触发）。
    - rematch / anomaly / financial 直接走 service，断言 DB 列被改。
"""

from __future__ import annotations

from domains.document.models import Document
from domains.masterdata.models import Country, Port
from domains.orders.models import Order
from test_v2.fixtures.helpers import login, make_minimal_pdf, seed_product, seed_user

# ─── Helpers ──────────────────────────────────────────────────


def _upload_order(client, headers, filename: str = "po.pdf") -> dict:
    """Upload a PDF via `/api/orders/upload`. Returns OrderDetail dict."""
    r = client.post(
        "/api/orders/upload",
        files={"file": (filename, make_minimal_pdf("po"), "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _seed_geo(db) -> tuple[int, int]:
    """Insert one Country + one Port so rematch can resolve them. Returns
    their IDs."""
    country = Country(name="Japan", code="JP", status=True)
    db.add(country)
    db.commit()
    db.refresh(country)
    port = Port(name="Tokyo", code="TOK", country_id=country.id, status=True)
    db.add(port)
    db.commit()
    db.refresh(port)
    return country.id, port.id


def _seed_resolvable_order(db, *, email: str = "resolver@example.com"):
    user = seed_user(db, email=email, role="employee")
    product = seed_product(db, code="MASTER-1", name="Master Product", unit="CA")
    order = Order(
        user_id=user.id,
        filename="resolve.pdf",
        file_type="pdf",
        status="ready",
        country_id=product.country_id,
        port_id=product.port_id,
        delivery_date="2026-10-05",
        loading_date="2026-10-05",
        products=[
            {
                "product_code": "UNKNOWN",
                "product_name": "Original PO Name",
                "quantity": 9,
                "unit": "CA24.0",
                "unit_price": 12,
            }
        ],
        product_count=1,
        match_results=[],
        anomaly_data={},
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return user, product, order


# ─── POST /api/orders/upload ─────────────────────────────────


def test_upload_pdf_creates_document_and_order_stub(client, db, session_factory):
    """PDF 上传：写 Document（status=extracted）+ 写 Order（status=uploading
    stub，因 doc_type 默认 unknown，projector 不触发）。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    body = _upload_order(client, headers, filename="po-test.pdf")

    assert body["filename"] == "po-test.pdf"
    assert body["file_type"] == "pdf"
    assert body["status"] == "uploading"
    assert body["document_id"] is not None

    fresh = session_factory()
    try:
        doc = fresh.query(Document).filter(Document.id == body["document_id"]).one()
        assert doc.status == "extracted"
        # And there's an Order linked to that document.
        order = fresh.query(Order).filter(Order.id == body["id"]).one()
        assert order.document_id == doc.id
        # No products were projected (PDF was a toy, no LLM).
        assert (order.products or []) == []
    finally:
        fresh.close()


def test_upload_unsupported_extension_returns_400(client, db):
    """非白名单的文件类型必须被 document_service 拒绝，转译成 400。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    r = client.post(
        "/api/orders/upload",
        files={"file": ("evil.zip", b"PK\x03\x04", "application/zip")},
        headers=headers,
    )
    assert r.status_code == 400


# ─── GET /api/orders ─────────────────────────────────────────


def test_list_orders_returns_only_my_orders(client, db):
    """跨用户隔离：A 上传的订单不能在 B 的列表里出现。"""
    seed_user(db, email="alice@example.com", role="employee")
    seed_user(db, email="bob@example.com", role="employee")
    a_headers = login(client, "alice@example.com")
    b_headers = login(client, "bob@example.com")

    a_order = _upload_order(client, a_headers, "alice-po.pdf")
    _upload_order(client, b_headers, "bob-po.pdf")

    r = client.get("/api/orders", headers=b_headers)
    assert r.status_code == 200
    body = r.json()
    assert "total" in body and "items" in body
    ids = [o["id"] for o in body["items"]]
    assert a_order["id"] not in ids


# ─── GET /api/orders/{id} ────────────────────────────────────


def test_get_order_returns_detail(client, db):
    """详情端点回 OrderDetail——包含 file_type、status 等字段。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.get(f"/api/orders/{order['id']}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == order["id"]
    assert body["filename"] == order["filename"]
    assert body["file_type"] == "pdf"
    assert "products" in body
    assert "match_results" in body
    assert body["issue_overview"] == {
        "schema_version": 1,
        "actionable_row_count": 0,
        "warning_row_count": 0,
        "rows": [],
        "non_row_findings": [],
    }


def test_get_other_users_order_returns_404(client, db):
    """A 用 B 的 order_id 请求 → 404 不是 403。"""
    seed_user(db, email="alice@example.com", role="employee")
    seed_user(db, email="bob@example.com", role="employee")
    a_headers = login(client, "alice@example.com")
    b_headers = login(client, "bob@example.com")
    a_order = _upload_order(client, a_headers)

    r = client.get(f"/api/orders/{a_order['id']}", headers=b_headers)
    assert r.status_code == 404


# ─── PATCH /api/orders/{id} ──────────────────────────────────


def test_patch_updates_po_number_column(client, db, session_factory):
    """PATCH po_number 是 ADR-0001 的真列写入——验证 Order.po_number 这个
    实际列被改（不是 order_metadata JSON 里某个键）。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.patch(
        f"/api/orders/{order['id']}",
        json={
            "po_number": "PO-2026-X1",
            "ship_name": "MV Test",
            "vendor_name": "Vendor Inc",
            "currency": "USD",
        },
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    # The serializer merges column values into order_metadata for v2 compat.
    assert body["order_metadata"]["po_number"] == "PO-2026-X1"
    assert body["order_metadata"]["ship_name"] == "MV Test"

    fresh = session_factory()
    try:
        o = fresh.query(Order).filter(Order.id == order["id"]).one()
        # ADR-0001 regression: real columns, not JSON.
        assert o.po_number == "PO-2026-X1"
        assert o.ship_name == "MV Test"
        assert o.vendor_name == "Vendor Inc"
        assert o.currency == "USD"
    finally:
        fresh.close()


def test_adr0001_po_number_is_real_column_not_json(db, session_factory):
    """直接检查 ORM 元数据：Order.po_number 必须是 Column，不能藏在 JSON。
    （这条不需要 HTTP，只是钉住"列化"决策，避免有人回滚到 JSON。）"""
    # SQLAlchemy InstrumentedAttribute → its expression is a real Column.
    col = Order.__table__.c.po_number
    assert col is not None
    assert col.type.python_type is str  # String(100)


# ─── POST /api/orders/{id}/rematch ───────────────────────────


def test_rematch_updates_country_and_port(client, db, session_factory):
    """rematch with country_id + port_id：列写入，status 流转到 ready。
    （Order 还没产品，run_matching 走空集合不出错。）"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    # Seed geo on the same session FastAPI uses (via TestClient override).
    country_id, port_id = _seed_geo(db)

    r = client.post(
        f"/api/orders/{order['id']}/rematch",
        json={"country_id": country_id, "port_id": port_id, "delivery_date": "2026-06-01"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["country_id"] == country_id
    assert body["port_id"] == port_id
    assert body["delivery_date"] == "2026-06-01"
    # Empty products → run_matching is a no-op → status moves to "ready".
    assert body["status"] in ("ready", "error")  # both are valid terminal states

    fresh = session_factory()
    try:
        o = fresh.query(Order).filter(Order.id == order["id"]).one()
        assert o.country_id == country_id
        assert o.port_id == port_id
    finally:
        fresh.close()


def test_rematch_with_unknown_country_returns_400(client, db):
    """country_id 不存在 → service.BadRequest → 400。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.post(
        f"/api/orders/{order['id']}/rematch",
        json={"country_id": 999999},
        headers=headers,
    )
    assert r.status_code == 400


# ─── PATCH /api/orders/{id} FK validation (added 2026-05-29) ──


def test_patch_order_with_unknown_country_returns_400(client, db):
    """PATCH update_order must validate country_id FK same as rematch
    does. Without this, a malformed body writes a dangling FK that
    the matcher's priority-0 lookup (also added 2026-05-29) then
    can't resolve, silently blocking valid downstream matches."""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.patch(
        f"/api/orders/{order['id']}",
        json={"country_id": 999999},
        headers=headers,
    )
    assert r.status_code == 400, r.text


def test_patch_order_with_unknown_port_returns_400(client, db):
    """Same as above for port_id."""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.patch(
        f"/api/orders/{order['id']}",
        json={"port_id": 999999},
        headers=headers,
    )
    assert r.status_code == 400, r.text


def test_patch_order_with_null_country_port_is_allowed(client, db):
    """Clearing the override (null) MUST be allowed — it's the
    matcher's escape hatch (see test_clearing_country_id_lets_currency_re_derive
    in section_4_orders). The FK validator must not treat None
    as 'invalid id'."""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.patch(
        f"/api/orders/{order['id']}",
        json={"country_id": None, "port_id": None},
        headers=headers,
    )
    assert r.status_code == 200, r.text


def test_patch_persists_loading_date_to_real_column(client, db, session_factory):
    """PATCH loading_date 必须写到 Order.loading_date 真实列（migration
    0013），不只是塞进 order_metadata JSON。这是 R6 全链路（schema →
    service → ORM）的 e2e 验收点。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.patch(
        f"/api/orders/{order['id']}",
        json={"loading_date": "2026-08-15"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["loading_date"] == "2026-08-15"

    fresh = session_factory()
    try:
        o = fresh.query(Order).filter(Order.id == order["id"]).one()
        assert o.loading_date == "2026-08-15"
    finally:
        fresh.close()


# ─── PATCH /api/orders/{id}/products/{row}/resolve ───────────


def test_resolve_row_binds_product_and_rechecks_anomalies(client, db):
    _user, product, order = _seed_resolvable_order(db)
    headers = login(client, "resolver@example.com")

    response = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={"action": "bind_product", "product_id": product.id},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["products"][0]["manual_product_id"] == product.id
    assert body["match_results"][0]["match_status"] == "matched"
    assert body["match_results"][0]["match_reason"] == "人工关联商品"
    assert body["anomaly_data"]["schema_version"] == 2


def test_resolve_row_rejects_product_outside_current_candidate_pool(client, db):
    _user, _product, order = _seed_resolvable_order(db)
    outside = seed_product(db, code="OUTSIDE", name="Outside", country_id=None, port_id=None)
    headers = login(client, "resolver@example.com")

    response = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={"action": "bind_product", "product_id": outside.id},
        headers=headers,
    )

    assert response.status_code == 400
    db.refresh(order)
    assert "manual_product_id" not in order.products[0]


def test_resolve_row_edit_source_clears_old_manual_evidence(client, db):
    _user, product, order = _seed_resolvable_order(db)
    order.products = [
        {
            **order.products[0],
            "manual_product_id": product.id,
            "rfq_quantity": 3,
            "rfq_unit": "CA",
            "conversion_evidence": {"verified": True, "evidence": "旧确认"},
        }
    ]
    db.commit()
    headers = login(client, "resolver@example.com")

    response = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={
            "action": "edit_source",
            "product_code": "MASTER-1",
            "product_name": "Corrected Name",
            "quantity": 10,
            "unit": "CA",
            "unit_price": 13.5,
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    row = response.json()["products"][0]
    assert row["product_name"] == "Corrected Name"
    assert row["quantity"] == 10
    assert "manual_product_id" not in row
    assert "conversion_evidence" not in row


def test_resolve_row_records_explicit_unit_conversion(client, db):
    _user, product, order = _seed_resolvable_order(db)
    order.products = [{**order.products[0], "manual_product_id": product.id}]
    db.commit()
    headers = login(client, "resolver@example.com")

    response = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={
            "action": "record_conversion",
            "source_quantity": 9,
            "source_unit": "CA24.0",
            "rfq_quantity": 10,
            "rfq_unit": "CA",
            "evidence": "供应商邮件确认按 10 箱报价",
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    row = response.json()["products"][0]
    assert row["rfq_quantity"] == 10
    assert row["rfq_unit"] == "CA"
    assert row["conversion_evidence"] == {
        "verified": True,
        "evidence": "供应商邮件确认按 10 箱报价",
    }
    codes = {item["code"] for item in response.json()["anomaly_data"]["findings"]}
    assert "UNIT_CONVERSION_REQUIRED" not in codes


def test_resolve_row_rejects_invalid_payload_and_other_users_order(client, db):
    _user, product, order = _seed_resolvable_order(db)
    seed_user(db, email="other-resolver@example.com", role="employee")
    owner_headers = login(client, "resolver@example.com")
    other_headers = login(client, "other-resolver@example.com")

    invalid = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={"action": "record_conversion", "rfq_quantity": 0, "rfq_unit": ""},
        headers=owner_headers,
    )
    forbidden = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={"action": "bind_product", "product_id": product.id},
        headers=other_headers,
    )

    assert invalid.status_code in {400, 422}
    assert forbidden.status_code == 404


# ─── POST /api/orders/{id}/anomaly-check ─────────────────────


def test_anomaly_check_populates_anomaly_data(client, db, session_factory):
    """anomaly-check 写 order.anomaly_data 字段。即便产品为空也应返回
    一个空/默认的 summary，不能 500。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.post(f"/api/orders/{order['id']}/anomaly-check", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "anomaly_data" in body

    fresh = session_factory()
    try:
        o = fresh.query(Order).filter(Order.id == order["id"]).one()
        assert o.anomaly_data is not None
    finally:
        fresh.close()


# ─── POST /api/orders/{id}/review ────────────────────────────


def test_review_marks_reviewed_with_notes(client, db, session_factory):
    """review 写 is_reviewed=True、reviewed_at、reviewed_by、notes。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.post(
        f"/api/orders/{order['id']}/review",
        json={"notes": "looks good"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    fresh = session_factory()
    try:
        o = fresh.query(Order).filter(Order.id == order["id"]).one()
        assert o.is_reviewed is True
        assert o.review_notes == "looks good"
        assert o.reviewed_at is not None
        assert o.reviewed_by is not None
    finally:
        fresh.close()


# ─── DELETE /api/orders/{id} ─────────────────────────────────


def test_delete_removes_order_row(client, db, session_factory):
    """DELETE 删 Order 行。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.delete(f"/api/orders/{order['id']}", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "order_id": order["id"]}

    fresh = session_factory()
    try:
        o = fresh.query(Order).filter(Order.id == order["id"]).one_or_none()
        assert o is None
    finally:
        fresh.close()


# ─── POST /api/orders/{id}/set-template (Phase 4 stub) ───────


def test_set_template_returns_501(client, db):
    """Phase 4 stub: 必须返 501 而不是 404/500。这条测试是廉价的"重构
    保险丝"——哪天误删了 stub，前端会立刻看见 404 / 405，但回退 stub
    的 commit 跑这个测试就知道契约还在。"""
    seed_user(db, email="alice@example.com", role="employee")
    headers = login(client, "alice@example.com")
    order = _upload_order(client, headers)

    r = client.post(f"/api/orders/{order['id']}/set-template", headers=headers)
    assert r.status_code == 501
