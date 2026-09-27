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
from domains.inquiry.models import Inquiry
from domains.inquiry.orchestrator import run_inquiry_for_group
from domains.masterdata.models import Country, Port, Supplier, UnitConversionRule
from domains.orders.groups.automation import auto_group_order
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


def _seed_pending_port_resolution_order(
    db,
    *,
    email: str = "port-review@example.com",
    role: str = "employee",
    resolution_status: str = "pending_review",
):
    user = seed_user(db, email=email, role=role)
    country_id, port_id = _seed_geo(db)
    order = Order(
        user_id=user.id,
        filename="port-review.pdf",
        file_type="pdf",
        status="ready",
        po_number="PO-PORT-REVIEW",
        ship_name="Test Ship",
        loading_date="2026-10-05",
        delivery_date="2026-10-05",
        country_id=country_id,
        port_id=port_id if resolution_status != "unresolved" else None,
        products=[],
        product_count=0,
        match_results=[],
        anomaly_data={
            "pipeline": [],
            "findings": [{"code": "LLM_PORT_REVIEW_REQUIRED"}],
        },
        port_resolution_method="llm",
        port_resolution_status=resolution_status,
        port_resolution_data={
            "source_destination": "TOKYO",
            "source_port_code": "TYO",
            "suggested_port_id": port_id,
            "final_port_id": port_id if resolution_status != "unresolved" else None,
            "model": "gemini-3.5-flash",
            "prompt_version": "oracle-port-resolution-v1",
            "decision_id": "decision-review-1",
            "reason": "Matched the listed Tokyo port",
            "decided_at": "2026-09-27T08:01:00Z",
        },
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return user, order, country_id, port_id


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


def test_order_list_exposes_pending_llm_port_resolution(client, db):
    user = seed_user(db, email="port-list@example.com", role="employee")
    pending = Order(
        user_id=user.id,
        filename="ai-port.pdf",
        file_type="pdf",
        status="ready",
        port_id=21,
        country_id=9,
        port_resolution_method="llm",
        port_resolution_status="pending_review",
        port_resolution_data={
            "source_destination": "OSAKA",
            "source_port_code": "OSA",
            "suggested_port_id": 21,
            "final_port_id": 21,
            "model": "gemini-3.5-flash",
            "prompt_version": "oracle-port-resolution-v1",
            "decision_id": "decision-list-1",
            "reason": "OSAKA corresponds to the listed Osaka port.",
            "decided_at": "2026-09-27T08:00:00Z",
        },
    )
    legacy = Order(
        user_id=user.id,
        filename="legacy-port.pdf",
        file_type="pdf",
        status="ready",
    )
    db.add_all([pending, legacy])
    db.commit()

    response = client.get("/api/orders", headers=login(client, user.email))

    assert response.status_code == 200, response.text
    items = {item["filename"]: item for item in response.json()["items"]}
    assert items["ai-port.pdf"]["port_resolution"] == {
        "method": "llm",
        "status": "pending_review",
        "source_destination": "OSAKA",
        "source_port_code": "OSA",
        "suggested_port_id": 21,
        "final_port_id": 21,
        "model": "gemini-3.5-flash",
        "prompt_version": "oracle-port-resolution-v1",
        "decision_id": "decision-list-1",
        "reason": "OSAKA corresponds to the listed Osaka port.",
        "decided_at": "2026-09-27T08:00:00Z",
        "failure_code": None,
        "reviewed_by": None,
        "reviewed_at": None,
    }
    assert items["legacy-port.pdf"]["port_resolution"] is None


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


def test_order_detail_exposes_pending_llm_port_resolution(client, db):
    user = seed_user(db, email="port-detail@example.com", role="employee")
    order = Order(
        user_id=user.id,
        filename="ai-port-detail.pdf",
        file_type="pdf",
        status="ready",
        port_id=21,
        country_id=9,
        port_resolution_method="llm",
        port_resolution_status="pending_review",
        port_resolution_data={
            "source_destination": "OSAKA",
            "source_port_code": "OSA",
            "suggested_port_id": 21,
            "final_port_id": 21,
            "model": "gemini-3.5-flash",
            "prompt_version": "oracle-port-resolution-v1",
            "decision_id": "decision-detail-1",
            "reason": "OSAKA corresponds to the listed Osaka port.",
            "decided_at": "2026-09-27T08:01:00Z",
        },
    )
    db.add(order)
    db.commit()

    response = client.get(
        f"/api/orders/{order.id}",
        headers=login(client, user.email),
    )

    assert response.status_code == 200, response.text
    assert response.json()["port_resolution"] == {
        "method": "llm",
        "status": "pending_review",
        "source_destination": "OSAKA",
        "source_port_code": "OSA",
        "suggested_port_id": 21,
        "final_port_id": 21,
        "model": "gemini-3.5-flash",
        "prompt_version": "oracle-port-resolution-v1",
        "decision_id": "decision-detail-1",
        "reason": "OSAKA corresponds to the listed Osaka port.",
        "decided_at": "2026-09-27T08:01:00Z",
        "failure_code": None,
        "reviewed_by": None,
        "reviewed_at": None,
    }


def test_port_resolution_confirm_is_owner_scoped_and_idempotent(client, db):
    owner, order, _country_id, _port_id = _seed_pending_port_resolution_order(db)
    stranger = seed_user(db, email="port-stranger@example.com", role="employee")
    before_inquiries = db.query(Inquiry).count()

    hidden = client.post(
        f"/api/orders/{order.id}/port-resolution/confirm",
        json={"decision_id": "decision-review-1"},
        headers=login(client, stranger.email),
    )
    assert hidden.status_code == 404

    first = client.post(
        f"/api/orders/{order.id}/port-resolution/confirm",
        json={"decision_id": "decision-review-1"},
        headers=login(client, owner.email),
    )
    assert first.status_code == 200, first.text
    assert first.json()["port_resolution"]["status"] == "confirmed"
    assert first.json()["port_resolution"]["reviewed_by"] == owner.id
    assert "LLM_PORT_REVIEW_REQUIRED" not in {
        item["code"] for item in first.json()["anomaly_data"]["findings"]
    }
    reviewed_at = first.json()["port_resolution"]["reviewed_at"]

    repeated = client.post(
        f"/api/orders/{order.id}/port-resolution/confirm",
        json={"decision_id": "decision-review-1"},
        headers=login(client, owner.email),
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["port_resolution"]["reviewed_at"] == reviewed_at
    assert db.query(Inquiry).count() == before_inquiries


def test_port_resolution_confirm_rejects_stale_decision_and_allows_admin(client, db):
    _owner, order, _country_id, _port_id = _seed_pending_port_resolution_order(db)
    admin = seed_user(db, email="port-admin@example.com", role="admin")

    stale = client.post(
        f"/api/orders/{order.id}/port-resolution/confirm",
        json={"decision_id": "older-decision"},
        headers=login(client, admin.email),
    )
    assert stale.status_code == 409
    db.refresh(order)
    assert order.port_resolution_status == "pending_review"

    accepted = client.post(
        f"/api/orders/{order.id}/port-resolution/confirm",
        json={"decision_id": "decision-review-1"},
        headers=login(client, admin.email),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["port_resolution"]["reviewed_by"] == admin.id


def test_port_resolution_override_validates_port_and_is_idempotent(
    client, db, monkeypatch
):
    owner, order, country_id, _port_id = _seed_pending_port_resolution_order(db)
    replacement = Port(name="Osaka", code="OSA", country_id=country_id, status=True)
    disabled = Port(name="Disabled", code="OFF", country_id=country_id, status=False)
    no_country = Port(name="No country", code="NONE", country_id=None, status=True)
    db.add_all([replacement, disabled, no_country])
    db.commit()
    calls = []
    monkeypatch.setattr(
        "domains.orders.automation.automatic_order_pipeline",
        lambda order_id: calls.append(order_id) or {},
    )
    headers = login(client, owner.email)

    for invalid_port in (disabled, no_country):
        rejected = client.post(
            f"/api/orders/{order.id}/port-resolution/override",
            json={
                "decision_id": "decision-review-1",
                "port_id": invalid_port.id,
            },
            headers=headers,
        )
        assert rejected.status_code == 400, rejected.text

    first = client.post(
        f"/api/orders/{order.id}/port-resolution/override",
        json={"decision_id": "decision-review-1", "port_id": replacement.id},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["port_id"] == replacement.id
    assert first.json()["country_id"] == country_id
    assert first.json()["port_resolution"]["status"] == "overridden"
    assert first.json()["port_resolution"]["suggested_port_id"] != replacement.id
    assert first.json()["port_resolution"]["final_port_id"] == replacement.id
    assert calls == [order.id]

    repeated = client.post(
        f"/api/orders/{order.id}/port-resolution/override",
        json={"decision_id": "decision-review-1", "port_id": replacement.id},
        headers=headers,
    )
    assert repeated.status_code == 200, repeated.text
    assert calls == [order.id]


def test_patch_pending_ai_port_routes_through_override_service(
    client, db, monkeypatch
):
    owner, order, country_id, _port_id = _seed_pending_port_resolution_order(db)
    replacement = Port(name="Yokohama", code="YOK", country_id=country_id, status=True)
    db.add(replacement)
    db.commit()
    calls = []
    monkeypatch.setattr(
        "domains.orders.automation.automatic_order_pipeline",
        lambda order_id: calls.append(order_id) or {},
    )
    headers = login(client, owner.email)

    missing_decision = client.patch(
        f"/api/orders/{order.id}",
        json={"port_id": replacement.id},
        headers=headers,
    )
    assert missing_decision.status_code == 400
    clearing_ai_port = client.patch(
        f"/api/orders/{order.id}",
        json={"port_id": None},
        headers=headers,
    )
    assert clearing_ai_port.status_code == 400
    changing_country_only = client.patch(
        f"/api/orders/{order.id}",
        json={"country_id": country_id},
        headers=headers,
    )
    assert changing_country_only.status_code == 400

    updated = client.patch(
        f"/api/orders/{order.id}",
        json={
            "port_id": replacement.id,
            "port_resolution_decision_id": "decision-review-1",
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["port_resolution"]["status"] == "overridden"
    assert updated.json()["port_id"] == replacement.id
    assert calls == [order.id]


def test_override_ai_port_preserves_old_inquiry_and_creates_one_new_version(
    client, db
):
    owner = seed_user(db, email="port-version@example.com", role="employee")
    country = Country(name="Japan port version", code="JPV", status=True)
    db.add(country)
    db.flush()
    first_port = Port(name="Tokyo version", code="TV1", country_id=country.id, status=True)
    second_port = Port(name="Osaka version", code="OV1", country_id=country.id, status=True)
    db.add_all([first_port, second_port])
    db.flush()
    seed_product(
        db,
        code="PORT-SKU",
        name="Port product Tokyo",
        country_id=country.id,
        port_id=first_port.id,
        unit="CA",
    )
    seed_product(
        db,
        code="PORT-SKU",
        name="Port product Osaka",
        country_id=country.id,
        port_id=second_port.id,
        unit="CA",
    )

    def make_order(po_number, port, *, pending=False):
        order = Order(
            user_id=owner.id,
            filename=f"{po_number}.pdf",
            file_type="pdf",
            status="ready",
            po_number=po_number,
            ship_name="Version Ship",
            loading_date="2026-10-05",
            delivery_date="2026-10-05",
            destination_port=port.name,
            country_id=country.id,
            port_id=port.id,
            products=[{
                "line_id": "line-00001",
                "product_code": "PORT-SKU",
                "product_name": "Port product",
                "quantity": "1",
                "unit": "CA",
            }],
            product_count=1,
            port_resolution_method="llm" if pending else None,
            port_resolution_status="pending_review" if pending else None,
            port_resolution_data={
                "source_destination": "TOKYO",
                "suggested_port_id": first_port.id,
                "final_port_id": first_port.id,
                "decision_id": "decision-version-1",
                "reason": "Initial AI decision",
                "decided_at": "2026-09-27T08:01:00Z",
            } if pending else None,
        )
        db.add(order)
        db.commit()
        auto_group_order(db, order.id)
        db.refresh(order)
        return order

    moving = make_order("PO-MOVE", first_port, pending=True)
    destination_member = make_order("PO-DESTINATION", second_port)
    old_group_id = moving.group_id
    destination_group_id = destination_member.group_id
    assert old_group_id and destination_group_id and old_group_id != destination_group_id
    run_inquiry_for_group(old_group_id, max_workers=1)
    run_inquiry_for_group(destination_group_id, max_workers=1)

    response = client.post(
        f"/api/orders/{moving.id}/port-resolution/override",
        json={"decision_id": "decision-version-1", "port_id": second_port.id},
        headers=login(client, owner.email),
    )

    assert response.status_code == 200, response.text
    assert response.json()["group_id"] == destination_group_id
    db.expire_all()
    old_versions = (
        db.query(Inquiry)
        .filter(Inquiry.group_id == old_group_id)
        .order_by(Inquiry.version.desc())
        .all()
    )
    destination_versions = (
        db.query(Inquiry)
        .filter(Inquiry.group_id == destination_group_id)
        .order_by(Inquiry.version.desc())
        .all()
    )
    assert [item.version for item in old_versions] == [1]
    assert [item.version for item in destination_versions] == [2, 1]

    repeated = client.post(
        f"/api/orders/{moving.id}/port-resolution/override",
        json={"decision_id": "decision-version-1", "port_id": second_port.id},
        headers=login(client, owner.email),
    )
    assert repeated.status_code == 200, repeated.text
    assert db.query(Inquiry).filter(Inquiry.group_id == destination_group_id).count() == 2


def test_get_order_enriches_current_match_with_supplier_name(client, db):
    """The PO detail must not depend on an old inquiry snapshot for names."""
    user = seed_user(db, email="supplier-name@example.com", role="employee")
    supplier = Supplier(name="株式会社 松武")
    db.add(supplier)
    db.flush()
    order = Order(
        user_id=user.id,
        filename="supplier-name.pdf",
        file_type="pdf",
        status="ready",
        products=[{
            "product_code": "PO-APPLE",
            "product_name": "APPLE GRANNY SMITH",
            "quantity": 150,
            "unit": "KG2.2",
        }],
        product_count=1,
        match_results=[{
            "product_code": "PO-APPLE",
            "product_name": "APPLE GRANNY SMITH",
            "quantity": 150,
            "unit": "KG2.2",
            "match_status": "matched",
            "matched_product": {
                "id": 100,
                "code": "99PRD010588",
                "product_name_en": "APPLE GRANNY SMITH US EXTRA FANCY 125CT/40LB",
                "supplier_id": supplier.id,
            },
        }],
        anomaly_data={},
    )
    db.add(order)
    db.commit()

    response = client.get(
        f"/api/orders/{order.id}",
        headers=login(client, user.email),
    )

    assert response.status_code == 200, response.text
    matched_product = response.json()["issue_overview"]["rows"][0]["matched_product"]
    assert matched_product["supplier_id"] == supplier.id
    assert matched_product["supplier_name"] == "株式会社 松武"


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
    order.products = [{
        **order.products[0],
        "rfq_quantity": 99,
        "rfq_unit": "OLD",
        "conversion_evidence": {"verified": True, "evidence": "旧商品的依据"},
    }]
    db.commit()
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
    assert "conversion_evidence" not in body["products"][0]


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


def test_admin_can_save_verified_product_pack_conversion(client, db):
    """Dropping the product fingerprint would reuse a decision after pack changes."""

    user, product, order = _seed_resolvable_order(
        db, email="resolver-product-admin@example.com"
    )
    user.role = "admin"
    product.unit_size = "10kg"
    order.products = [{**order.products[0], "manual_product_id": product.id}]
    db.commit()
    headers = login(client, user.email)

    response = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={
            "action": "record_conversion",
            "conversion_scope": "product",
            "source_quantity": 9,
            "source_unit": "CA24.0",
            "rfq_quantity": 10,
            "rfq_unit": "CA",
            "rule_source_quantity": 9,
            "rule_target_quantity": 10,
            "target_step": 1,
            "break_pack": None,
            "evidence": "供应商确认相同商品包装按 10 箱报价",
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    rule = db.query(UnitConversionRule).one()
    assert rule.status == "verified"
    assert rule.scope_type == "product"
    assert rule.product_id == product.id
    assert rule.pack_signature == '["CA","10KG",""]'
    row = response.json()["products"][0]
    assert row["conversion_evidence"]["rule_id"] == rule.id
    assert row["conversion_evidence"]["scope_type"] == "product"


def test_admin_can_save_exact_source_unit_conversion(client, db):
    """A source rule must retain the complete unit pair and no product scope."""

    user, product, order = _seed_resolvable_order(
        db, email="resolver-source-admin@example.com"
    )
    user.role = "admin"
    order.products = [{**order.products[0], "manual_product_id": product.id}]
    db.commit()
    headers = login(client, user.email)

    response = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={
            "action": "record_conversion",
            "conversion_scope": "source_unit",
            "source_quantity": 9,
            "source_unit": " ca24.0 ",
            "rfq_quantity": 10,
            "rfq_unit": "ca",
            "rule_source_quantity": 9,
            "rule_target_quantity": 10,
            "evidence": "该 Oracle 单位组合全局确认",
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    rule = db.query(UnitConversionRule).one()
    assert rule.scope_type == "source_unit"
    assert rule.product_id is None
    assert rule.pack_signature is None
    assert rule.source_unit == "CA24.0"
    assert rule.target_unit == "CA"


def test_employee_reusable_scope_is_forbidden_without_partial_writes(client, db):
    """Rejecting after a flush would leave a global rule or row evidence behind."""

    _user, product, order = _seed_resolvable_order(
        db, email="resolver-reuse-employee@example.com"
    )
    order.products = [{**order.products[0], "manual_product_id": product.id}]
    db.commit()
    headers = login(client, "resolver-reuse-employee@example.com")

    response = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={
            "action": "record_conversion",
            "conversion_scope": "product",
            "source_quantity": 9,
            "source_unit": "CA24.0",
            "rfq_quantity": 10,
            "rfq_unit": "CA",
            "rule_source_quantity": 9,
            "rule_target_quantity": 10,
            "evidence": "不应保存",
        },
        headers=headers,
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.query(UnitConversionRule).count() == 0
    assert "conversion_evidence" not in db.get(Order, order.id).products[0]


def test_reusable_relation_must_reproduce_entered_rfq_quantity(client, db):
    """Saving a basis unrelated to the current row would poison later orders."""

    user, product, order = _seed_resolvable_order(
        db, email="resolver-invalid-basis@example.com"
    )
    user.role = "admin"
    order.products = [{**order.products[0], "manual_product_id": product.id}]
    db.commit()
    headers = login(client, user.email)

    response = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={
            "action": "record_conversion",
            "conversion_scope": "product",
            "source_quantity": 9,
            "source_unit": "CA24.0",
            "rfq_quantity": 10,
            "rfq_unit": "CA",
            "rule_source_quantity": 1,
            "rule_target_quantity": 2,
            "evidence": "错误关系",
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert "不能得到当前询价数量" in response.json()["detail"]
    db.expire_all()
    assert db.query(UnitConversionRule).count() == 0
    assert "conversion_evidence" not in db.get(Order, order.id).products[0]


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


def test_resolve_row_rejects_conversion_until_product_is_matched(client, db):
    _user, _product, order = _seed_resolvable_order(db)
    headers = login(client, "resolver@example.com")

    response = client.patch(
        f"/api/orders/{order.id}/products/1/resolve",
        json={
            "action": "record_conversion",
            "source_quantity": 9,
            "source_unit": "CA24.0",
            "rfq_quantity": 10,
            "rfq_unit": "CA",
            "evidence": "人工确认",
        },
        headers=headers,
    )

    assert response.status_code == 400
    db.refresh(order)
    assert "conversion_evidence" not in order.products[0]


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
