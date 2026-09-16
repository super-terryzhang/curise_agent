"""Price event lifecycle, existing channels and rollback conflict acceptance."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from domains.masterdata import price_history, service
from domains.masterdata.errors import Conflict
from domains.masterdata.models import Product, ProductPriceHistory
from domains.masterdata.schemas import ProductCreate, ProductUpdate
from domains.masterdata.upload import service as upload
from test_v2.fixtures.helpers import login, make_excel, seed_product, seed_user


def create(db, name="A", price=100, selling=150):
    return service.create_product(
        db,
        ProductCreate(
            product_name_en=name,
            code=name,
            price=price,
            contract_price=selling,
            currency="JPY",
            unit="KG",
        ),
        actor_id=1,
        source="http",
    )


def edit(db, product_id, **values):
    product = db.get(Product, product_id)
    return service.update_product(
        db,
        product_id,
        ProductUpdate(expected_revision=product.revision, **values),
        actor_id=1,
        source="http",
    )


def batch(db, rows):
    b = upload.parse_excel(db, file_bytes=make_excel(rows), filename="synthetic.xlsx", user_id=1)
    upload.resolve_and_score(db, batch_id=b.id, user_id=1)
    return b


def test_independent_products_and_one_event_for_two_prices(db):
    a, b = create(db), create(db, "B", 200, 250)
    for purchase, selling in [(110, 160), (120, 170), (130, 180)]:
        edit(db, a["id"], price=purchase, contract_price=selling)
    for purchase, selling in [(210, 260), (220, 270)]:
        edit(db, b["id"], price=purchase, contract_price=selling)
    ah, bh = price_history.list_history(db, a["id"]), price_history.list_history(db, b["id"])
    assert (ah["change_count"], bh["change_count"]) == (3, 2)
    assert [x["version"] for x in ah["items"]] == [3, 2, 1, 0]
    assert ah["items"][0]["before"]["price"] == "120.00"
    assert all(x["product_id"] == a["id"] for x in ah["items"])


def test_noop_zero_clear_and_currency_context(db):
    a = create(db)
    edit(db, a["id"], price=100, contract_price=150)
    assert price_history.list_history(db, a["id"])["change_count"] == 0
    edit(db, a["id"], price=0, contract_price=None)
    edit(db, a["id"], currency="USD")
    h = price_history.list_history(db, a["id"])
    assert h["change_count"] == 2
    assert h["items"][0]["before"]["currency"] == "JPY"
    assert h["items"][0]["after"]["price"] == "0.00"
    assert h["items"][1]["after"]["contract_price"] is None


def test_stale_http_version_and_real_actor(db, client):
    seed_user(db, email="ledger@example.test", role="admin")
    headers = login(client, "ledger@example.test")
    a = create(db)
    ok = client.patch(
        f"/api/data/products/{a['id']}",
        json={"price": 120, "expected_revision": a["revision"]},
        headers=headers,
    )
    assert ok.status_code == 200
    stale = client.patch(
        f"/api/data/products/{a['id']}",
        json={"price": 130, "expected_revision": a["revision"]},
        headers=headers,
    )
    assert stale.status_code == 409
    missing = client.patch(f"/api/data/products/{a['id']}", json={"price": 130}, headers=headers)
    assert missing.status_code == 400
    h = client.get(f"/api/data/products/{a['id']}/price-history", headers=headers).json()
    assert h["change_count"] == 1 and h["items"][0]["actor_id"] is not None


def test_upload_records_once_and_rollback_appends(db):
    p = seed_product(db, code="A", name="A", price=100)
    p.contract_price = Decimal(150)
    db.commit()
    b = batch(db, [{"product_name": "A", "product_code": "A", "price": 120, "contract_price": 180}])
    assert upload.commit_batch(db, batch_id=b.id, user_id=1)["updated"] == 1
    assert price_history.list_history(db, p.id)["change_count"] == 1
    result = upload.rollback_batch(db, batch_id=b.id, user_id=1)
    assert result == {"deleted": 0, "restored": 2, "skipped": 0}
    h = price_history.list_history(db, p.id)
    assert h["change_count"] == 2 and h["items"][0]["event_type"] == "restore"
    assert h["items"][0]["after"]["price"] == "100.00"
    assert upload.rollback_batch(db, batch_id=b.id, user_id=1) == {
        "deleted": 0,
        "restored": 0,
        "skipped": 0,
    }


def test_rollback_refuses_later_metadata_edit_even_if_prices_equal(db):
    p = seed_product(db, code="A", name="A", price=100)
    b = batch(db, [{"product_name": "A", "product_code": "A", "price": 120}])
    upload.commit_batch(db, batch_id=b.id, user_id=1)
    edit(db, p.id, brand="later")
    result = upload.rollback_batch(db, batch_id=b.id, user_id=1)
    assert result["skipped"] == 1 and result["conflicts"][0]["product_id"] == p.id
    db.refresh(p)
    assert p.price == 120


def test_stale_upload_refuses_and_re_resolve_refreshes(db):
    p = seed_product(db, code="A", name="A", price=100)
    b = batch(db, [{"product_name": "A", "product_code": "A", "price": 120}])
    edit(db, p.id, price=110)
    result = upload.commit_batch(db, batch_id=b.id, user_id=1)
    assert result["errors"] == 1
    assert price_history.list_history(db, p.id)["change_count"] == 1
    db.refresh(p)
    assert p.price == 110


def test_duplicate_product_rows_are_errors(db):
    p = seed_product(db, code="A", name="A", price=100)
    b = batch(
        db,
        [
            {"product_name": "A", "product_code": "A", "price": 120},
            {"product_name": "A", "product_code": "A", "price": 130},
        ],
    )
    r = upload.commit_batch(db, batch_id=b.id, user_id=1)
    assert r["errors"] == 2 and r["updated"] == 0
    assert db.get(Product, p.id).price == 100


def test_restore_snapshot_cross_product_rejected_and_delete_retains_history(db):
    a, b = create(db), create(db, "B")
    initial = price_history.list_history(db, a["id"])["items"][0]
    edit(db, a["id"], price=120)
    p = db.get(Product, a["id"])
    price_history.restore(db, p.id, initial["id"], p.revision, actor_id=1)
    assert p.price == 100
    with pytest.raises(Exception, match="不存在"):
        price_history.restore(db, b["id"], initial["id"], 1, actor_id=1)
    service.delete_product(db, p.id, actor_id=1)
    h = price_history.list_history(db, a["id"])
    assert h["deleted"] and h["items"][0]["event_type"] == "delete"


def test_stable_paging_excludes_newer_edits(db):
    a = create(db)
    edit(db, a["id"], price=110)
    edit(db, a["id"], price=120)
    page = price_history.list_history(db, a["id"], limit=1)
    edit(db, a["id"], price=130)
    next_page = price_history.list_history(
        db, a["id"], limit=2, offset=1, max_version=page["max_version"]
    )
    assert [x["version"] for x in next_page["items"]] == [1, 0]


def test_history_failure_rolls_back_current_price(db, monkeypatch):
    a = create(db)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic history failure")

    monkeypatch.setattr(price_history, "_event", fail)
    with pytest.raises(RuntimeError):
        edit(db, a["id"], price=120)
    assert db.get(Product, a["id"]).price == 100
    assert (
        db.scalar(
            select(ProductPriceHistory).where(ProductPriceHistory.product_id == a["id"])
        ).version
        == 0
    )


def test_ai_proposal_pins_server_revision_and_stale_approval_refuses(db):
    from agent.runtime.approvals import create_pending
    from agent.runtime.deps import V3Deps
    from agent.runtime.tools.masterdata import _dispatch_update_product_financial

    a = create(db)
    pending = create_pending(
        db,
        session_id="synthetic-ledger",
        user_id=1,
        action="update_product_financial",
        target_kind="product",
        target_id=a["id"],
        payload={"price": 120, "expected_revision": 999},
        summary="synthetic update",
    )
    assert pending.payload["expected_revision"] == a["revision"]
    edit(db, a["id"], price=110)
    with pytest.raises(Conflict):
        _dispatch_update_product_financial(V3Deps(db, 1), a["id"], pending.payload)
    assert db.get(Product, a["id"]).price == 110
    current = db.get(Product, a["id"])
    _dispatch_update_product_financial(
        V3Deps(db, 1), a["id"], {"price": 130, "expected_revision": current.revision}
    )
    assert price_history.list_history(db, a["id"])["items"][0]["source"] == "ai"
    with pytest.raises(ValueError, match="旧审批"):
        _dispatch_update_product_financial(V3Deps(db, 1), a["id"], {"price": 140})


def test_legacy_v2_arrays_dry_run_idempotence_and_unknown_context(db):
    import json

    from sqlalchemy import text

    from domains.masterdata.price_history_backfill import import_legacy

    a = create(db)
    db.execute(text("CREATE TABLE v2_upload_batches (id INTEGER PRIMARY KEY,status TEXT)"))
    db.execute(
        text(
            "CREATE TABLE v2_product_changelog (id INTEGER PRIMARY KEY,product_id INTEGER,batch_id INTEGER,change_type TEXT,field_changes JSON,changed_at TEXT,changed_by INTEGER)"
        )
    )
    db.execute(text("INSERT INTO v2_upload_batches VALUES (1,'completed')"))
    fields = [
        {"field": "price", "old_value": "10", "new_value": "20"},
        {"field": "contract_price", "old_value": None, "new_value": 0},
        {"field": "price", "old_value": "bad", "new_value": "30"},
    ]
    db.execute(
        text(
            "INSERT INTO v2_product_changelog VALUES (1,:p,1,'updated',:f,'2026-07-01 12:00:00',1)"
        ),
        {"p": a["id"], "f": json.dumps(fields)},
    )
    dry = import_legacy(db)
    assert dry == {"importable": 2, "already_imported": 0, "invalid": 1, "inserted": 0}
    first = import_legacy(db, apply=True)
    db.commit()
    assert first["inserted"] == 2
    repeat = import_legacy(db, apply=True)
    db.commit()
    assert repeat["inserted"] == 0 and repeat["already_imported"] == 2
    h = price_history.list_history(db, a["id"], legacy=True)
    assert h["total"] == 2 and h["change_count"] == 0
    assert h["items"][1]["after"]["contract_price"] == "0.00"
    assert "currency" not in h["items"][0]["after"]
    with pytest.raises(Exception, match="不能直接恢复"):
        price_history.restore(db, a["id"], h["items"][0]["id"], a["revision"], actor_id=1)


@pytest.mark.parametrize("role", ["employee", "finance", "admin", "superadmin"])
def test_price_history_reads_follow_current_product_visibility(client, db, role):
    seed_user(db, email=f"ledger-{role}@example.test", role=role)
    headers = login(client, f"ledger-{role}@example.test")
    a = create(db)
    response = client.get(f"/api/data/products/{a['id']}/price-history", headers=headers)
    assert response.status_code == 200, response.text
    if role in ("employee", "finance"):
        restore = client.post(
            f"/api/data/products/{a['id']}/price-history/restore",
            headers=headers,
            json={
                "event_id": response.json()["items"][0]["id"],
                "expected_revision": a["revision"],
            },
        )
        assert restore.status_code == 403


def test_aba_rollback_is_conflict_and_retry_after_partial_success_does_not_repeat(db):
    a = seed_product(db, code="A", name="A", price=100)
    b = seed_product(db, code="B", name="B", price=200)
    upload_batch = batch(
        db,
        [
            {"product_name": "A", "product_code": "A", "price": 120},
            {"product_name": "B", "product_code": "B", "price": 220},
        ],
    )
    upload.commit_batch(db, batch_id=upload_batch.id, user_id=1)
    edit(db, a.id, price=130)
    edit(db, a.id, price=120)
    result = upload.rollback_batch(db, batch_id=upload_batch.id, user_id=1)
    assert result["restored"] == 1 and result["skipped"] == 1
    second = upload.rollback_batch(db, batch_id=upload_batch.id, user_id=1)
    assert second["restored"] == 0 and second["skipped"] == 1
    assert price_history.list_history(db, b.id)["change_count"] == 2
    assert a.price == 120 and b.price == 200


def test_preview_flags_stale_revision_before_commit_and_resolve_refreshes(db):
    p = seed_product(db, code="A", name="A", price=100)
    b = batch(db, [{"product_name": "A", "product_code": "A", "price": 120}])
    edit(db, p.id, price=110)
    preview = upload.preview_changes(db, batch_id=b.id, user_id=1)
    assert preview["summary"]["error"] == 1
    assert preview["error"][0]["product_id"] == p.id
    upload.resolve_and_score(db, batch_id=b.id, user_id=1)
    assert upload.preview_changes(db, batch_id=b.id, user_id=1)["summary"]["update"] == 1
    assert upload.commit_batch(db, batch_id=b.id, user_id=1)["updated"] == 1


def test_history_date_bounds_validate_and_support_utc(db):
    from datetime import UTC, datetime

    from domains.masterdata.errors import BadRequest

    a = create(db)
    assert (
        price_history.list_history(
            db, a["id"], date_from=datetime(2020, 1, 1), date_to=datetime(2099, 1, 1, tzinfo=UTC)
        )["total"]
        == 1
    )
    with pytest.raises(BadRequest, match="开始日期"):
        price_history.list_history(
            db, a["id"], date_from=datetime(2099, 1, 1), date_to=datetime(2020, 1, 1)
        )


def test_product_edits_leave_existing_order_price_snapshot_unchanged(db):
    from copy import deepcopy

    from domains.orders.models import Order

    a = create(db)
    saved = [
        {
            "matched_product": {
                "id": a["id"],
                "price": 100,
                "contract_price": 150,
                "currency": "JPY",
            },
            "unit_price": 200,
            "quantity": 3,
        }
    ]
    order = Order(
        user_id=1, filename="synthetic.pdf", status="completed", match_results=deepcopy(saved)
    )
    db.add(order)
    db.commit()
    edit(db, a["id"], price=300, contract_price=400, currency="USD")
    db.refresh(order)
    assert order.match_results == saved


def test_named_ai_history_tool_is_registered_and_product_scoped(db):
    import json
    from pathlib import Path

    from agent.runtime.deps import V3Deps, inject_deps
    from agent.runtime.tools import enabled_v3_business_tool_names
    from general_agent import REGISTRY, ToolContext

    user = seed_user(db, email="history-reader@example.test", role="employee")
    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db, user.id))
    a = create(db)
    create(db, "B")
    edit(db, a["id"], price=110)
    assert "get_product_price_history" in enabled_v3_business_tool_names()
    # Decorated tool objects expose their registered callable via the registry.
    result = json.loads(
        REGISTRY.view(["get_product_price_history"]).dispatch(
            "get_product_price_history", {"product_id": a["id"]}, ctx=ctx
        )
    )
    assert result["change_count"] == 1
    assert all(row["product_id"] == a["id"] for row in result["items"])


def test_rollback_refuses_a_later_successful_batch(db):
    p = seed_product(db, code="A", name="A", price=100)
    first = batch(db, [{"product_name": "A", "product_code": "A", "price": 120}])
    upload.commit_batch(db, batch_id=first.id, user_id=1)
    later = batch(db, [{"product_name": "A", "product_code": "A", "price": 140}])
    upload.commit_batch(db, batch_id=later.id, user_id=1)
    result = upload.rollback_batch(db, batch_id=first.id, user_id=1)
    assert result["restored"] == 0 and result["skipped"] == 1
    db.refresh(p)
    assert p.price == 140
    assert price_history.list_history(db, p.id)["change_count"] == 2
