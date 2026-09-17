"""Direct product-image workbench contract tests (migration 0030)."""

from __future__ import annotations

import io
from datetime import datetime, timedelta

from PIL import Image

from domains.masterdata.images import bulk_service, direct_service
from domains.masterdata.images.bulk_models import BulkImageBatch, BulkImageStaging
from domains.masterdata.models import ProductImage
from test_v2.fixtures.helpers import login, seed_product, seed_user


def _png(color: tuple[int, int, int] = (20, 40, 60)) -> bytes:
    image = Image.new("RGB", (120, 80), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _setup(db, client):
    user = seed_user(db, email="images@x.test", role="employee")
    product = seed_product(db, code="IMG-001", name="Image workbench product")
    return user, product, login(client, "images@x.test")


def test_direct_upload_validates_real_image_and_can_be_recovered(client, db):
    _user, product, headers = _setup(db, client)
    created = client.post("/api/data/bulk-images/direct", headers=headers)
    assert created.status_code == 201, created.text
    batch_id = created.json()["id"]

    uploaded = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("front.png", _png(), "image/png")},
        headers=headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    row = uploaded.json()
    assert row["status"] == "ready"
    assert row["issue_code"] is None
    assert row["preview_url"]

    active = client.get("/api/data/bulk-images/active", headers=headers)
    assert active.status_code == 200
    assert active.json()["id"] == batch_id
    assert active.json()["source_type"] == "direct"
    assert active.json()["rows"][0]["product_id"] == product.id


def test_direct_upload_reports_corrupt_image_in_chinese(client, db):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]

    response = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("broken.png", b"not-an-image", "image/png")},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["status"] == "needs_attention"
    assert response.json()["issue_code"] == "invalid_image"
    assert "图片" in response.json()["error_message"]


def test_validate_plan_commit_makes_first_item_primary_and_is_idempotent(client, db):
    _user, product, headers = _setup(db, client)
    existing = client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("existing.png", _png((1, 2, 3)), "image/png")},
        headers=headers,
    ).json()

    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    first = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("first.png", _png((4, 5, 6)), "image/png")},
        headers=headers,
    ).json()
    second = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("second.png", _png((7, 8, 9)), "image/png")},
        headers=headers,
    ).json()

    checked = client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers)
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_continue"] is True

    planned = client.put(
        f"/api/data/bulk-images/{batch_id}/plans/{product.id}",
        json={
            "items": [
                f"staged:{second['id']}",
                f"existing:{existing['id']}",
                f"staged:{first['id']}",
            ]
        },
        headers=headers,
    )
    assert planned.status_code == 200, planned.text

    committed = client.post(f"/api/data/bulk-images/{batch_id}/commit", headers=headers)
    assert committed.status_code == 202, committed.text

    db.expire_all()
    rows = (
        db.query(ProductImage)
        .filter(ProductImage.product_id == product.id)
        .order_by(ProductImage.display_order)
        .all()
    )
    assert [row.filename for row in rows] == ["second.png", "existing.png", "first.png"]
    assert db.query(ProductImage).filter(ProductImage.product_id == product.id).count() == 3

    batch = db.get(BulkImageBatch, batch_id)
    assert batch.status == "completed"
    staged = db.query(BulkImageStaging).filter_by(batch_id=batch_id).all()
    assert all(row.committed_image_id for row in staged)

    repeated = client.post(f"/api/data/bulk-images/{batch_id}/commit", headers=headers)
    assert repeated.status_code == 409
    assert db.query(ProductImage).filter(ProductImage.product_id == product.id).count() == 3


def test_direct_image_workbench_rejects_finance_role(client, db):
    seed_user(db, email="finance-images@x.test", role="finance")
    headers = login(client, "finance-images@x.test")
    response = client.post("/api/data/bulk-images/direct", headers=headers)
    assert response.status_code == 403


def test_excluding_bad_row_allows_valid_rows_to_continue(client, db):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("good.png", _png(), "image/png")},
        headers=headers,
    )
    bad = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("bad.png", b"broken", "image/png")},
        headers=headers,
    ).json()
    checked = client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers)
    assert checked.json()["can_continue"] is False

    resolved = client.patch(
        f"/api/data/bulk-images/{batch_id}/rows/{bad['id']}",
        json={"decision": "exclude"},
        headers=headers,
    )
    assert resolved.status_code == 200
    assert resolved.json()["can_continue"] is True
    assert resolved.json()["excluded_count"] == 1


def test_capacity_is_rechecked_before_review(client, db):
    user, product, headers = _setup(db, client)
    for index in range(30):
        db.add(
            ProductImage(
                product_id=product.id,
                storage_key=f"full/{index}",
                thumbnail_key=f"thumb/{index}",
                medium_key=f"medium/{index}",
                filename=f"{index}.jpg",
                file_type="image/jpeg",
                file_size_bytes=10,
                display_order=index,
                uploaded_by_user_id=user.id,
            )
        )
    db.commit()
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("overflow.png", _png(), "image/png")},
        headers=headers,
    )
    checked = client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers).json()
    assert checked["can_continue"] is False
    assert checked["rows"][0]["issue_code"] == "capacity_exceeded"


def test_failed_commit_row_can_retry_without_duplicate(client, db, monkeypatch):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    row = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("retry.png", _png(), "image/png")},
        headers=headers,
    ).json()
    client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers)

    original = direct_service.image_service.add_product_image
    monkeypatch.setattr(
        direct_service.image_service,
        "add_product_image",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary storage error")),
    )
    response = client.post(f"/api/data/bulk-images/{batch_id}/commit", headers=headers)
    assert response.status_code == 202
    assert response.json()["failed_count"] == 1
    assert response.json()["rows"][0]["status"] == "committed_failed"

    monkeypatch.setattr(direct_service.image_service, "add_product_image", original)
    retried = client.post(
        f"/api/data/bulk-images/{batch_id}/rows/{row['id']}/retry", headers=headers
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "committed"
    assert db.query(ProductImage).filter_by(product_id=product.id).count() == 1


def test_gc_cleans_abandoned_direct_staging_files(client, db):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("old.png", _png(), "image/png")},
        headers=headers,
    )
    batch = db.get(BulkImageBatch, batch_id)
    batch.created_at = datetime.utcnow() - timedelta(hours=25)
    db.commit()

    result = bulk_service.sweep_stale_batches(db, ttl_hours=24)
    db.expire_all()
    row = db.query(BulkImageStaging).filter_by(batch_id=batch_id).one()
    assert result["stale_batches"] == 1
    assert db.get(BulkImageBatch, batch_id).status == "cancelled"
    assert row.storage_key is None
    assert row.preview_storage_key is None
