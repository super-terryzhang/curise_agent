"""Section 9 — Supplier letterhead convergence (2026-05-28).

测试目标：
    上次的"两个入口管同一张 suppliers 表"被收敛到 `/api/data/suppliers`
    一份。本文件锁住四个契约：

      1. PATCH /api/data/suppliers/{id} 接受 5 个新字段（address /
         zip_code / fax / default_payment_method / default_payment_terms）
         并把它们写入 DB，下一次 GET 能拿回来。
      2. partial PATCH 不抹掉其他字段（exclude_unset 路径）。
      3. 旧端点 GET /api/settings/suppliers 已删除 → 404。
      4. inquiry-readiness 在每个 supplier 行返回 `supplier_letterhead`
         长度恰好 9，每条带 {key, column, label, value, filled}，并且
         "filled" 跟 supplier 行的当前列值一致。

为什么重要：
    Bug A 已经修过（multi-port 静默改写）。但 supplier 信头那 5 个
    字段历史上只有 settings tab 能写——前端收敛之后唯一写入路径变
    成 /api/data/suppliers，如果后端 schema 没扩字段，生产就会出现
    "前端能填、后端忽略"的诡异 bug。这里用真实 HTTP round-trip 守住。

    同时 supplier_letterhead 是询价生成卡片的核心契约：前端按这个
    数组渲染 9 行 ✅/⚠️ 状态。如果数组缺一项或者 filled 标错，用户
    要么看不到该补的字段、要么生成出来 Excel 缺料还以为正常。

设计方法：
    复用 conftest 的 `client` + `seed_user` (superadmin) + `auth_tokens`
    (employee)。inquiry-readiness 测试沿用 test_inquiry_api.py 的
    Order + Supplier + match_results 套路。
"""

from __future__ import annotations

from typing import Any

from domains.inquiry._supplier_worker import supplier_letterhead_status
from domains.inquiry.field_inspection import SUPPLIER_FIELD_MAP
from test_v2.fixtures.helpers import login


def _admin_headers(client) -> dict[str, str]:
    return login(client, "admin@example.com", "password123")


# ─── Pure helper: supplier_letterhead_status ─────────────────


def test_letterhead_status_returns_nine_entries_for_any_input():
    """合约：永远返回 9 条记录，顺序稳定，shape 不变。

    若 supplier_dict 缺字段、传 None、传空 dict，前端都能渲染同一个
    9 行表格而不要写防御代码。
    """
    for sd in [None, {}, {"name": "X"}, {"name": "X", "address": "tokyo"}]:
        rows = supplier_letterhead_status(sd)
        assert len(rows) == 9
        assert [r["key"] for r in rows] == list(SUPPLIER_FIELD_MAP.keys())
        for r in rows:
            assert set(r.keys()) == {"key", "column", "label", "value", "filled"}


def test_letterhead_status_filled_flag_treats_whitespace_as_empty():
    """空字符串、纯空白都算 NOT filled —— 前端用 filled 切换颜色。"""
    rows = supplier_letterhead_status(
        {
            "name": "Supplier A",
            "address": "",          # empty → not filled
            "phone": "   ",         # whitespace → not filled
            "fax": "03-1234-5678",  # real value → filled
            "email": None,          # None → not filled
        }
    )
    by_key = {r["key"]: r for r in rows}
    assert by_key["supplier_name"]["filled"] is True
    assert by_key["supplier_name"]["value"] == "Supplier A"
    assert by_key["supplier_address"]["filled"] is False
    assert by_key["supplier_address"]["value"] is None
    assert by_key["supplier_tel"]["filled"] is False
    assert by_key["supplier_fax"]["filled"] is True
    assert by_key["supplier_fax"]["value"] == "03-1234-5678"
    assert by_key["supplier_email"]["filled"] is False


def test_letterhead_status_columns_match_supplier_table():
    """`column` 指向 Supplier 表的实际列名 —— 前端用它做 PATCH 的 key.
    如果这个映射漂移，前端 inline-edit 会把数据写错列。"""
    rows = supplier_letterhead_status({"name": "Z"})
    for r in rows:
        assert r["column"] == SUPPLIER_FIELD_MAP[r["key"]], (
            f"column drift for key {r['key']}: got {r['column']}, "
            f"expected {SUPPLIER_FIELD_MAP[r['key']]}"
        )


# ─── PATCH /api/data/suppliers/{id} — 5 new fields ──────────


def test_patch_supplier_writes_letterhead_fields(client, seed_user, db):
    """PATCH 5 个新字段都能写入并在 GET 读到。

    pre-2026-05-28：SupplierUpdate 不带这 5 个字段，FastAPI 默默丢弃，
    前端表单看着保存了但 DB 没动。这条 round-trip 守住扩展契约。
    """
    from domains.masterdata.models import Supplier

    headers = _admin_headers(client)
    s = Supplier(name="LetterheadCo", status=True)
    db.add(s)
    db.commit()
    db.refresh(s)

    payload = {
        "address": "東京都中央区晴海3-1-1",
        "zip_code": "104-0053",
        "fax": "03-1234-5678",
        "default_payment_method": "T/T",
        "default_payment_terms": "Net 30",
    }
    r = client.patch(f"/api/data/suppliers/{s.id}", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    for k, v in payload.items():
        assert body[k] == v, f"PATCH didn't echo {k!r}: got {body.get(k)!r}, expected {v!r}"

    # Cross-session verify — independent SELECT confirms persistence (not
    # just the response shape).
    db.expire_all()
    fresh = db.get(Supplier, s.id)
    assert fresh is not None
    assert fresh.address == payload["address"]
    assert fresh.zip_code == payload["zip_code"]
    assert fresh.fax == payload["fax"]
    assert fresh.default_payment_method == payload["default_payment_method"]
    assert fresh.default_payment_terms == payload["default_payment_terms"]


def test_patch_supplier_partial_update_preserves_other_fields(client, seed_user, db):
    """PATCH 只发一个字段 → 其余字段保留原值（exclude_unset 路径）。

    若有人误把 SupplierUpdate 字段去掉了默认 None，PATCH 就会把空字段
    写成 NULL 抹掉。这条防回归。
    """
    from domains.masterdata.models import Supplier

    headers = _admin_headers(client)
    s = Supplier(
        name="PartialCo",
        contact="Alice",
        email="alice@example.com",
        address="old address",
        status=True,
    )
    db.add(s)
    db.commit()
    db.refresh(s)

    r = client.patch(
        f"/api/data/suppliers/{s.id}",
        json={"address": "new address"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    fresh = db.get(Supplier, s.id)
    assert fresh.address == "new address"
    assert fresh.contact == "Alice", "partial PATCH must not clear contact"
    assert fresh.email == "alice@example.com", "partial PATCH must not clear email"


# ─── GET /api/data/suppliers shape ───────────────────────────


def test_get_suppliers_list_returns_5_new_letterhead_fields(client, seed_user, db):
    """GET /api/data/suppliers returns the 5 letterhead fields per row.

    `list_suppliers()` uses an inline batched dict instead of
    `_serialize_supplier()` (perf — single country/cat batch lookup vs
    N+1). The two paths can drift; this test pins that the list-view
    contract matches the detail-view contract on the 5 new fields.
    Without it the inquiry-card letterhead status looks empty even
    though the supplier row actually has data.
    """
    from domains.masterdata.models import Supplier

    headers = _admin_headers(client)
    db.add(
        Supplier(
            name="ListShapeProbe",
            address="dummy address",
            zip_code="000-0000",
            fax="00-0000-0000",
            default_payment_method="T/T",
            default_payment_terms="Net 30",
            status=True,
        )
    )
    db.commit()
    r = client.get("/api/data/suppliers", headers=headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    probe = next((s for s in rows if s["name"] == "ListShapeProbe"), None)
    assert probe is not None, "ListShapeProbe missing from GET response"
    for k in (
        "address",
        "zip_code",
        "fax",
        "default_payment_method",
        "default_payment_terms",
    ):
        assert k in probe, f"GET /api/data/suppliers shape missing key {k!r}"
    assert probe["address"] == "dummy address"
    assert probe["zip_code"] == "000-0000"
    assert probe["default_payment_method"] == "T/T"


def test_post_supplier_creates_with_5_new_letterhead_fields(client, seed_user, db):
    """POST /api/data/suppliers persists all 5 new fields.

    `create_supplier()` historically wrote only contact/email/phone — if
    create misses a field, the user can fill it in the form but the row
    saves without it. This test catches that drift at the HTTP boundary.
    """
    from domains.masterdata.models import Supplier

    headers = _admin_headers(client)
    r = client.post(
        "/api/data/suppliers",
        json={
            "name": "CreateShapeProbe",
            "address": "東京都",
            "zip_code": "100-0001",
            "fax": "03-0000-0000",
            "default_payment_method": "L/C",
            "default_payment_terms": "Net 60",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["address"] == "東京都"
    assert body["zip_code"] == "100-0001"
    assert body["default_payment_terms"] == "Net 60"
    # Verify persisted (cross-session check)
    db.expire_all()
    persisted = db.query(Supplier).filter_by(name="CreateShapeProbe").one()
    assert persisted.address == "東京都"
    assert persisted.fax == "03-0000-0000"


# ─── /api/settings/suppliers — deleted ───────────────────────


def test_settings_suppliers_endpoint_is_gone(client, seed_user):
    """旧 endpoint 2026-05-28 删除 → 404。

    任何老前端 cached 页面试图打它都应该明确失败，而不是 sneakily
    返回上次的数据。
    """
    headers = _admin_headers(client)
    r = client.get("/api/settings/suppliers", headers=headers)
    assert r.status_code == 404, (
        f"/api/settings/suppliers should be deleted, got {r.status_code}"
    )


# ─── inquiry-readiness includes supplier_letterhead ──────────


def _make_order_with_supplier(
    db: Any,
    *,
    user_id: int,
    supplier_id: int = 700,
    supplier_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Build one Order with 1 matched product at one supplier.

    `supplier_kwargs` lets the test inject letterhead values onto the
    Supplier row before pre_analyze reads them back.
    """
    from domains.masterdata.models import Supplier
    from domains.orders.models import Order

    s_kwargs: dict[str, Any] = {
        "id": supplier_id,
        "name": "Letterhead Apple Inc",
        "contact": "Tanaka",
        "email": "tanaka@example.jp",
        "phone": "03-0000-0000",
        "status": True,
    }
    s_kwargs.update(supplier_kwargs or {})
    db.add(Supplier(**s_kwargs))

    order = Order(
        user_id=user_id,
        filename="po.pdf",
        file_type="pdf",
        status="ready_for_review",
        po_number="PO-LH-001",
        ship_name="MV Test",
        currency="JPY",
        delivery_date="2026-06-15",
        match_results=[
            {
                "product_code": "L1",
                "product_name": "Letterhead Apple",
                "quantity": 5,
                "unit_price": 2.0,
                "matched_product": {
                    "id": 9001,
                    "supplier_id": supplier_id,
                    "code": "L1",
                    "unit": "kg",
                },
            },
        ],
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_readiness_includes_supplier_letterhead_nine_fields(client, seed_user, db):
    """GET inquiry-readiness 每个 supplier 行返回 `supplier_letterhead`
    长度恰好 9，filled 标位准确反映 DB 当前列值。
    """
    headers = _admin_headers(client)
    order = _make_order_with_supplier(
        db,
        user_id=seed_user.id,
        supplier_id=701,
        supplier_kwargs={
            "name": "TestSupplier",
            "address": "東京都港区赤坂",
            "phone": "03-1111-2222",
            # Explicit NULL on the rest so the helper's defaults don't leak
            # and confuse the mixed-state assertion below.
            "contact": None,
            "email": None,
        },
    )

    r = client.get(f"/api/orders/{order.id}/inquiry-readiness", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    sup = body["suppliers"].get("701")
    assert sup is not None, f"supplier 701 missing from readiness: {body!r}"

    letterhead = sup.get("supplier_letterhead")
    assert isinstance(letterhead, list), "supplier_letterhead must be a list"
    assert len(letterhead) == 9, f"expected 9 letterhead entries, got {len(letterhead)}"

    by_key = {r["key"]: r for r in letterhead}
    # Filled fields
    assert by_key["supplier_name"]["filled"] is True
    assert by_key["supplier_name"]["value"] == "TestSupplier"
    assert by_key["supplier_address"]["filled"] is True
    assert by_key["supplier_address"]["value"] == "東京都港区赤坂"
    assert by_key["supplier_tel"]["filled"] is True
    assert by_key["supplier_tel"]["value"] == "03-1111-2222"
    # Empty fields
    assert by_key["supplier_email"]["filled"] is False
    assert by_key["supplier_email"]["value"] is None
    assert by_key["supplier_fax"]["filled"] is False
    assert by_key["supplier_zip_code"]["filled"] is False
    assert by_key["payment_method"]["filled"] is False
    assert by_key["payment_date"]["filled"] is False

    # Each entry carries the "column" key the frontend uses as the PATCH
    # payload key. If this drifts, inline edit on the inquiry card silently
    # writes to the wrong column.
    for entry in letterhead:
        assert entry["column"] == SUPPLIER_FIELD_MAP[entry["key"]]
