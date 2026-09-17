"""Direct product-image workbench contract tests (migration 0030)."""

from __future__ import annotations

import io
from datetime import datetime, timedelta

import pytest
from PIL import Image

from domains.masterdata.images import bulk_service, direct_service
from domains.masterdata.images.bulk_models import BulkImageBatch, BulkImageStaging
from domains.masterdata.models import Country, Port, ProductImage
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
            ],
            "expected_revision": checked.json()["plans"][0]["revision"],
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


def test_retry_restores_all_failed_rows_for_same_product_atomically(
    client, db, monkeypatch
):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    staged = []
    for index in range(2):
        staged.append(
            client.post(
                f"/api/data/bulk-images/{batch_id}/files",
                data={"product_id": str(product.id)},
                files={
                    "file": (
                        f"retry-{index}.png",
                        _png((index + 1, 20, 30)),
                        "image/png",
                    )
                },
                headers=headers,
            ).json()
        )
    client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers)
    original = direct_service.image_service.add_product_image
    monkeypatch.setattr(
        direct_service.image_service,
        "add_product_image",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary error")),
    )
    failed = client.post(f"/api/data/bulk-images/{batch_id}/commit", headers=headers)
    assert failed.json()["failed_count"] == 2

    monkeypatch.setattr(direct_service.image_service, "add_product_image", original)
    retried = client.post(
        f"/api/data/bulk-images/{batch_id}/rows/{staged[0]['id']}/retry",
        headers=headers,
    )
    assert retried.status_code == 200, retried.text
    current = client.get(f"/api/data/bulk-images/{batch_id}", headers=headers).json()
    assert current["failed_count"] == 0
    assert current["ingested_count"] == 2
    assert all(row["status"] == "committed" for row in current["rows"])
    assert db.query(ProductImage).filter_by(product_id=product.id).count() == 2


def test_failed_retry_rebases_when_gallery_changed(client, db, monkeypatch):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    staged = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("rebase.png", _png(), "image/png")},
        headers=headers,
    ).json()
    client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers)
    original = direct_service.image_service.add_product_image
    monkeypatch.setattr(
        direct_service.image_service,
        "add_product_image",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("temporary error")),
    )
    failed = client.post(f"/api/data/bulk-images/{batch_id}/commit", headers=headers)
    assert failed.json()["failed_count"] == 1
    monkeypatch.setattr(direct_service.image_service, "add_product_image", original)
    concurrent = client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("concurrent.png", _png((99, 88, 77)), "image/png")},
        headers=headers,
    ).json()

    rebased = client.post(
        f"/api/data/bulk-images/{batch_id}/rows/{staged['id']}/retry",
        headers=headers,
    )
    assert rebased.status_code == 200, rebased.text
    assert rebased.json()["status"] == "ready"
    current = client.get(f"/api/data/bulk-images/{batch_id}", headers=headers).json()
    assert current["status"] == "preview_ready"
    assert current["error_message"] is None
    assert current["plans"][0]["expected_existing_image_ids"] == [concurrent["id"]]

    completed = client.post(f"/api/data/bulk-images/{batch_id}/commit", headers=headers)
    assert completed.status_code == 202
    assert completed.json()["status"] == "completed"
    assert db.query(ProductImage).filter_by(product_id=product.id).count() == 2


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


def test_retry_recovers_durable_source_marker_without_duplicate(client, db):
    user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    staged = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("once.png", _png(), "image/png")},
        headers=headers,
    ).json()
    client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers)
    formal = ProductImage(
        product_id=product.id,
        storage_key="formal/full",
        thumbnail_key="formal/thumb",
        medium_key="formal/medium",
        filename="once.png",
        file_type="image/jpeg",
        file_size_bytes=20,
        display_order=0,
        uploaded_by_user_id=user.id,
        source_bulk_staging_id=staged["id"],
    )
    db.add(formal)
    row = db.get(BulkImageStaging, staged["id"])
    row.status = "committed_failed"
    db.commit()

    response = client.post(
        f"/api/data/bulk-images/{batch_id}/rows/{staged['id']}/retry", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["committed_image_id"] == formal.id
    assert db.query(ProductImage).filter_by(product_id=product.id).count() == 1


def test_concurrent_gallery_change_returns_batch_to_review(client, db):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("planned.png", _png(), "image/png")},
        headers=headers,
    )
    client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers)
    client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("concurrent.png", _png((90, 80, 70)), "image/png")},
        headers=headers,
    )

    response = client.post(f"/api/data/bulk-images/{batch_id}/commit", headers=headers)
    assert response.status_code == 202
    assert response.json()["status"] == "preview_ready"
    assert "其他用户" in response.json()["error_message"]
    assert db.query(ProductImage).filter_by(product_id=product.id).count() == 1

    checked = client.post(
        f"/api/data/bulk-images/{batch_id}/validate", headers=headers
    )
    assert checked.status_code == 200
    assert checked.json()["error_message"] is None
    assert checked.json()["plans"][0]["expected_existing_image_ids"]
    committed = client.post(f"/api/data/bulk-images/{batch_id}/commit", headers=headers)
    assert committed.status_code == 202
    assert committed.json()["status"] == "completed"
    assert db.query(ProductImage).filter_by(product_id=product.id).count() == 2


def test_commit_claim_is_compare_and_swap(client, db):
    user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("claim.png", _png(), "image/png")},
        headers=headers,
    )
    client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers)
    claimed = bulk_service.trigger_commit(
        db, batch_id=batch_id, user_id=user.id, is_admin=False
    )
    assert claimed.status == "processing"
    with pytest.raises(bulk_service.StatusConflict):
        bulk_service.trigger_commit(
            db, batch_id=batch_id, user_id=user.id, is_admin=False
        )


def test_processing_direct_batch_cannot_be_force_cancelled(client, db):
    _user, _product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    batch = db.get(BulkImageBatch, batch_id)
    batch.status = "processing"
    db.commit()
    response = client.delete(
        f"/api/data/bulk-images/{batch_id}?force=true", headers=headers
    )
    assert response.status_code == 409


def test_stuck_direct_batch_keeps_retryable_source_files(client, db):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("stuck.png", _png(), "image/png")},
        headers=headers,
    )
    batch = db.get(BulkImageBatch, batch_id)
    batch.status = "processing"
    batch.updated_at = datetime.utcnow() - timedelta(minutes=20)
    db.commit()

    bulk_service.sweep_stale_batches(db, stuck_minutes=15)
    db.expire_all()
    row = db.query(BulkImageStaging).filter_by(batch_id=batch_id).one()
    assert db.get(BulkImageBatch, batch_id).status == "error"
    assert row.storage_key is not None
    assert row.preview_storage_key is not None


def test_declared_mime_extension_and_actual_format_must_agree(client, db):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    response = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("looks-like-jpeg.jpg", _png(), "image/jpeg")},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["issue_code"] == "format_mismatch"


def test_invalid_row_can_be_replaced_and_then_pass_validation(client, db):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    row = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("broken.png", b"not-an-image", "image/png")},
        headers=headers,
    ).json()

    replaced = client.put(
        f"/api/data/bulk-images/{batch_id}/rows/{row['id']}/file",
        files={"file": ("fixed.png", _png(), "image/png")},
        headers=headers,
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["can_continue"] is True
    assert replaced.json()["rows"][0]["status"] == "ready"
    assert replaced.json()["rows"][0]["image_filename"] == "fixed.png"


def test_replacing_valid_row_with_invalid_file_stays_blocked(client, db):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    row = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("valid.png", _png(), "image/png")},
        headers=headers,
    ).json()
    replaced = client.put(
        f"/api/data/bulk-images/{batch_id}/rows/{row['id']}/file",
        files={"file": ("invalid.png", b"broken", "image/png")},
        headers=headers,
    )
    assert replaced.status_code == 200
    assert replaced.json()["can_continue"] is False
    assert replaced.json()["rows"][0]["issue_code"] == "invalid_image"


def test_partial_staging_upload_is_cleaned_and_reported(
    client, db, monkeypatch, _local_storage
):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    real_upload = _local_storage.upload
    calls = 0

    def fail_second_upload(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("preview storage unavailable")
        return real_upload(*args, **kwargs)

    monkeypatch.setattr(_local_storage, "upload", fail_second_upload)
    response = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("partial.png", _png(), "image/png")},
        headers=headers,
    )
    assert response.status_code == 400
    assert "暂存失败" in response.json()["detail"]
    assert not [path for path in _local_storage.root.rglob("*") if path.is_file()]


def test_plan_revision_rejects_stale_order_write(client, db):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("order.png", _png(), "image/png")},
        headers=headers,
    )
    checked = client.post(
        f"/api/data/bulk-images/{batch_id}/validate", headers=headers
    ).json()
    plan = checked["plans"][0]
    first = client.put(
        f"/api/data/bulk-images/{batch_id}/plans/{product.id}",
        json={"items": plan["ordered_items"], "expected_revision": plan["revision"]},
        headers=headers,
    )
    assert first.status_code == 200
    stale = client.put(
        f"/api/data/bulk-images/{batch_id}/plans/{product.id}",
        json={"items": plan["ordered_items"], "expected_revision": plan["revision"]},
        headers=headers,
    )
    assert stale.status_code == 409
    assert "其他页面" in stale.json()["detail"]


def test_stuck_batch_can_resume_from_unfinished_product(client, db):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("resume.png", _png(), "image/png")},
        headers=headers,
    )
    client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers)
    batch = db.get(BulkImageBatch, batch_id)
    batch.status = "error"
    batch.error_message = "simulated worker interruption"
    db.commit()

    resumed = client.post(f"/api/data/bulk-images/{batch_id}/resume", headers=headers)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["status"] == "completed"
    assert resumed.json()["ingested_count"] == 1
    assert db.query(ProductImage).filter_by(product_id=product.id).count() == 1


def test_error_batch_cannot_resume_while_new_batch_is_active(client, db):
    _user, _product, headers = _setup(db, client)
    old_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    old = db.get(BulkImageBatch, old_id)
    old.status = "error"
    db.commit()
    current_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]

    resumed = client.post(f"/api/data/bulk-images/{old_id}/resume", headers=headers)
    assert resumed.status_code == 409
    assert str(current_id) in resumed.json()["detail"]


def test_non_owner_cannot_read_or_mutate_direct_batch(client, db):
    _user, _product, owner_headers = _setup(db, client)
    batch_id = client.post(
        "/api/data/bulk-images/direct", headers=owner_headers
    ).json()["id"]
    seed_user(db, email="other-images@x.test", role="employee")
    other_headers = login(client, "other-images@x.test")

    assert client.get(
        f"/api/data/bulk-images/{batch_id}", headers=other_headers
    ).status_code == 404
    assert client.delete(
        f"/api/data/bulk-images/{batch_id}", headers=other_headers
    ).status_code == 404


def test_filename_auto_match_is_limited_to_selected_location(client, db):
    _user, first, headers = _setup(db, client)
    country = Country(name="Scoped country")
    db.add(country)
    db.flush()
    port = Port(name="Scoped port", country_id=country.id)
    db.add(port)
    db.commit()
    second = seed_product(
        db,
        code=first.code,
        name="Scoped duplicate",
        country_id=country.id,
        port_id=port.id,
    )
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    uploaded = client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"country_id": str(country.id), "port_id": str(port.id)},
        files={"file": (f"{first.code}.png", _png(), "image/png")},
        headers=headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["product_id"] == second.id


def test_completed_cleanup_failure_keeps_keys_for_gc_retry(
    client, db, monkeypatch, _local_storage
):
    _user, product, headers = _setup(db, client)
    batch_id = client.post("/api/data/bulk-images/direct", headers=headers).json()["id"]
    client.post(
        f"/api/data/bulk-images/{batch_id}/files",
        data={"product_id": str(product.id)},
        files={"file": ("cleanup.png", _png(), "image/png")},
        headers=headers,
    )
    client.post(f"/api/data/bulk-images/{batch_id}/validate", headers=headers)
    real_delete = _local_storage.delete
    monkeypatch.setattr(
        _local_storage,
        "delete",
        lambda _key: (_ for _ in ()).throw(RuntimeError("temporary delete failure")),
    )

    committed = client.post(f"/api/data/bulk-images/{batch_id}/commit", headers=headers)
    assert committed.status_code == 202
    assert committed.json()["status"] == "completed"
    db.expire_all()
    row = db.query(BulkImageStaging).filter_by(batch_id=batch_id).one()
    assert row.storage_key is not None
    assert row.preview_storage_key is not None

    monkeypatch.setattr(_local_storage, "delete", real_delete)
    batch = db.get(BulkImageBatch, batch_id)
    batch.created_at = datetime.utcnow() - timedelta(hours=25)
    db.commit()
    swept = bulk_service.sweep_stale_batches(db, ttl_hours=24)
    db.expire_all()
    row = db.query(BulkImageStaging).filter_by(batch_id=batch_id).one()
    assert swept["stale_batches"] == 1
    assert db.get(BulkImageBatch, batch_id).status == "completed"
    assert row.storage_key is None
    assert row.preview_storage_key is None
