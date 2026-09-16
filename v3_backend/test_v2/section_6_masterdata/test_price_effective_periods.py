"""Purchase and selling prices have independent validity periods."""

from datetime import datetime

from domains.masterdata import price_history
from domains.masterdata.models import Product
from domains.masterdata.upload import service as upload
from test_v2.fixtures.helpers import login, make_excel, seed_product, seed_user

PRICE_PERIOD_FIELDS = {
    "purchase_price_effective_from",
    "purchase_price_effective_to",
    "selling_price_effective_from",
    "selling_price_effective_to",
}


def test_product_model_has_four_independent_price_period_columns():
    assert PRICE_PERIOD_FIELDS.issubset(Product.__table__.c.keys())


def test_http_edit_persists_price_periods_without_reusing_product_period(client, db):
    seed_user(db, email="price-period@example.com", role="admin")
    headers = login(client, "price-period@example.com")
    product = seed_product(db, code="PERIOD-1", name="Period Product", price=100)
    product.effective_from = datetime(2025, 1, 1)
    product.effective_to = datetime(2029, 12, 31)
    db.commit()
    db.refresh(product)

    response = client.patch(
        f"/api/data/products/{product.id}",
        headers=headers,
        json={
            "expected_revision": product.revision,
            "purchase_price_effective_from": "2026-01-01",
            "purchase_price_effective_to": "2026-06-30",
            "selling_price_effective_from": "2026-02-01",
            "selling_price_effective_to": "2026-12-31",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["purchase_price_effective_from"].startswith("2026-01-01")
    assert body["purchase_price_effective_to"].startswith("2026-06-30")
    assert body["selling_price_effective_from"].startswith("2026-02-01")
    assert body["selling_price_effective_to"].startswith("2026-12-31")

    db.refresh(product)
    assert product.effective_from == datetime(2025, 1, 1)
    assert product.effective_to == datetime(2029, 12, 31)


def test_http_rejects_reversed_purchase_or_selling_period(client, db):
    seed_user(db, email="invalid-period@example.com", role="admin")
    headers = login(client, "invalid-period@example.com")
    product = seed_product(db, code="PERIOD-2", name="Invalid Period", price=100)

    for payload, expected in (
        (
            {
                "purchase_price_effective_from": "2026-12-31",
                "purchase_price_effective_to": "2026-01-01",
            },
            "采购价有效开始日期不能晚于结束日期",
        ),
        (
            {
                "selling_price_effective_from": "2026-12-31",
                "selling_price_effective_to": "2026-01-01",
            },
            "卖价有效开始日期不能晚于结束日期",
        ),
    ):
        db.refresh(product)
        response = client.patch(
            f"/api/data/products/{product.id}",
            headers=headers,
            json={"expected_revision": product.revision, **payload},
        )
        assert response.status_code == 400, response.text
        assert expected in response.json()["detail"]


def test_bulk_update_previews_commits_and_logs_all_price_periods(db):
    product = seed_product(db, code="PERIOD-3", name="Bulk Period", price=100)
    batch = upload.parse_excel(
        db,
        file_bytes=make_excel([
            {
                "product_name": "Bulk Period",
                "product_code": "PERIOD-3",
                "purchase_price_effective_from": "2026-01-01",
                "purchase_price_effective_to": "2026-06-30",
                "selling_price_effective_from": "2026-02-01",
                "selling_price_effective_to": "2026-12-31",
            }
        ]),
        filename="periods.xlsx",
        user_id=1,
    )
    upload.resolve_and_score(db, batch_id=batch.id, user_id=1)
    preview = upload.preview_changes(db, batch_id=batch.id, user_id=1)
    assert PRICE_PERIOD_FIELDS.issubset(preview["update"][0]["will_write_fields"])
    assert upload.commit_batch(db, batch_id=batch.id, user_id=1)["updated"] == 1

    db.refresh(product)
    assert product.purchase_price_effective_from == datetime(2026, 1, 1)
    assert product.purchase_price_effective_to == datetime(2026, 6, 30)
    assert product.selling_price_effective_from == datetime(2026, 2, 1)
    assert product.selling_price_effective_to == datetime(2026, 12, 31)
    event = price_history.list_history(db, product.id)["items"][0]
    assert PRICE_PERIOD_FIELDS.issubset(event["changed_fields"])


def test_bulk_validation_includes_existing_other_endpoint(db):
    product = seed_product(db, code="PERIOD-4", name="Existing Endpoint", price=100)
    product.purchase_price_effective_to = datetime(2026, 6, 30)
    db.commit()
    batch = upload.parse_excel(
        db,
        file_bytes=make_excel([
            {
                "product_name": "Existing Endpoint",
                "product_code": "PERIOD-4",
                "purchase_price_effective_from": "2026-07-01",
            }
        ]),
        filename="invalid-period.xlsx",
        user_id=1,
    )
    resolved = upload.resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 1
    preview = upload.preview_changes(db, batch_id=batch.id, user_id=1)
    assert "采购价有效开始日期不能晚于结束日期" in preview["error"][0]["errors"]
