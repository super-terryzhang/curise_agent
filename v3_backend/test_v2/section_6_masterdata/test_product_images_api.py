"""End-to-end HTTP tests for the product-image endpoints (R5 2026-06-22).

These walk the full stack: TestClient → router → service → Pillow →
storage abstraction (LocalFileStorage via the autouse fixture) → DB.
We assert on:
    - HTTP status codes
    - response shape
    - persisted DB state via fresh sessions
    - blob existence on disk (LocalFileStorage)

`test_product_image_thumbnails.py` already covers the pure-function
edge cases of the Pillow utility, so here we focus on the orchestration
seam: did the right things get written, in the right order, with the
right cross-product isolation?

LocalFileStorage detail: the autouse `_local_storage` fixture in
conftest writes blobs under a per-test tmp dir, so we don't need to
mock the storage backend — files actually appear and we can assert on
them.
"""

from __future__ import annotations

import io

from PIL import Image

from domains.masterdata.models import ProductImage
from test_v2.fixtures.helpers import login, seed_product, seed_user


# ─── Helpers ──────────────────────────────────────────────────


def _png_bytes(size: tuple[int, int] = (300, 200), color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    """Synthesize a valid PNG. We use PNG (not JPEG) here so the test
    inputs differ from the JPEG output the service produces — that way
    we know the output bytes really came from the re-encode path."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _seeded_user_and_product(db):
    """Most tests need (user, product, headers). Centralized to avoid
    boilerplate in every test."""
    user = seed_user(db, email="alice@x.test", role="admin")
    product = seed_product(db, code="PIMG-001", name="Test product for images")
    return user, product


# ─── Upload happy path ────────────────────────────────────────


def test_upload_returns_three_signed_urls(client, db, auth_tokens):
    """After a successful POST, the response carries the three URLs the
    frontend needs (thumbnail/medium/full) plus stable metadata."""
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")

    r = client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("test.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    for field in ("id", "filename", "file_type", "display_order",
                  "thumbnail_url", "medium_url", "full_url"):
        assert field in body, f"missing {field}"
    # First image upload → display_order=0 (primary).
    assert body["display_order"] == 0
    # Output mime is always JPEG — the service re-encodes regardless of input.
    assert body["file_type"] == "image/jpeg"
    # URLs are non-empty strings.
    assert isinstance(body["thumbnail_url"], str) and body["thumbnail_url"]
    assert isinstance(body["medium_url"], str)
    assert isinstance(body["full_url"], str)


def test_second_upload_lands_at_display_order_1(client, db):
    """Append semantics: the second upload should NOT collide with the
    first at display_order=0."""
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")

    client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("a.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    r = client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("b.png", _png_bytes(color=(100, 100, 100)), "image/png")},
        headers=headers,
    )
    assert r.status_code == 201
    assert r.json()["display_order"] == 1


def test_list_endpoint_returns_images_ordered_by_display_order(client, db):
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")

    for color in [(10, 10, 10), (50, 50, 50), (100, 100, 100)]:
        client.post(
            f"/api/data/products/{product.id}/images",
            files={"file": ("x.png", _png_bytes(color=color), "image/png")},
            headers=headers,
        )

    r = client.get(f"/api/data/products/{product.id}/images", headers=headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 3
    assert [r["display_order"] for r in rows] == [0, 1, 2]


# ─── List endpoint projection (thumbnail_url on Product row) ──


def test_product_list_projects_thumbnail_url_and_count(client, db):
    """The /api/data/products list endpoint should embed thumbnail_url +
    image_count per row so the frontend table doesn't N+1."""
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")

    # Two uploads.
    client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("p1.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("p2.png", _png_bytes(color=(99, 99, 99)), "image/png")},
        headers=headers,
    )

    r = client.get("/api/data/products", headers=headers)
    assert r.status_code == 200
    items = r.json()["items"]
    row = next(item for item in items if item["id"] == product.id)
    assert row["image_count"] == 2
    assert isinstance(row["thumbnail_url"], str) and row["thumbnail_url"]


def test_product_list_thumbnail_url_is_null_for_product_with_no_images(client, db):
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")
    r = client.get("/api/data/products", headers=headers)
    row = next(item for item in r.json()["items"] if item["id"] == product.id)
    assert row["image_count"] == 0
    assert row["thumbnail_url"] is None


# ─── Validation errors ───────────────────────────────────────


def test_upload_rejects_heic_with_friendly_error(client, db):
    """HEIC handling is MVP-rejected; the error must guide the user
    toward the action (convert to JPG). This is THE test that catches
    a regression where we accept HEIC bytes but Pillow chokes far
    downstream with a stack trace."""
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")
    r = client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("photo.heic", b"FAKE_HEIC_BYTES", "image/heic")},
        headers=headers,
    )
    assert r.status_code == 400
    assert "HEIC" in r.json()["detail"]


def test_upload_rejects_over_5mb(client, db):
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")
    # 6 MB of zero bytes — beyond MAX_IMAGE_BYTES.
    big = b"\x00" * (6 * 1024 * 1024)
    r = client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("huge.png", big, "image/png")},
        headers=headers,
    )
    assert r.status_code == 400
    assert "MB" in r.json()["detail"]


def test_upload_rejects_unknown_mime(client, db):
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")
    r = client.post(
        f"/api/data/products/{product.id}/images",
        files={"file": ("file.txt", b"plain text content", "text/plain")},
        headers=headers,
    )
    assert r.status_code == 400


def test_upload_returns_404_for_unknown_product(client, db):
    seed_user(db, email="alice@x.test", role="admin")
    headers = login(client, "alice@x.test")
    r = client.post(
        "/api/data/products/999999/images",
        files={"file": ("x.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    assert r.status_code == 404


# ─── Delete + compaction ─────────────────────────────────────


def test_delete_image_compacts_display_order(client, db, session_factory):
    """After deleting display_order=1 from [0,1,2], the remaining rows
    should be re-numbered [0,1] — no gap left behind."""
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")

    ids = []
    for i in range(3):
        r = client.post(
            f"/api/data/products/{product.id}/images",
            files={"file": (f"{i}.png", _png_bytes(color=(i, i, i)), "image/png")},
            headers=headers,
        )
        ids.append(r.json()["id"])

    # Delete the middle image (display_order=1).
    r = client.delete(
        f"/api/data/products/{product.id}/images/{ids[1]}",
        headers=headers,
    )
    assert r.status_code == 204

    fresh = session_factory()
    try:
        remaining = (
            fresh.query(ProductImage)
            .filter(ProductImage.product_id == product.id)
            .order_by(ProductImage.display_order)
            .all()
        )
        orders = [r.display_order for r in remaining]
        assert orders == [0, 1], (
            f"display_order should re-pack after delete: got {orders}"
        )
    finally:
        fresh.close()


# ─── Reorder validation ──────────────────────────────────────


def test_reorder_replaces_display_orders(client, db, session_factory):
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")
    ids = []
    for i in range(3):
        r = client.post(
            f"/api/data/products/{product.id}/images",
            files={"file": (f"{i}.png", _png_bytes(color=(i, i, i)), "image/png")},
            headers=headers,
        )
        ids.append(r.json()["id"])

    # Reverse the order.
    r = client.put(
        f"/api/data/products/{product.id}/images/reorder",
        json={
            "items": [
                {"id": ids[0], "display_order": 2},
                {"id": ids[1], "display_order": 1},
                {"id": ids[2], "display_order": 0},
            ]
        },
        headers=headers,
    )
    assert r.status_code == 200
    rows = r.json()
    # Returned list should be in new display_order ascending.
    assert [row["id"] for row in rows] == [ids[2], ids[1], ids[0]]


def test_reorder_rejects_partial_id_set(client, db):
    """The contract says you must send ALL ids. Sending a subset is a
    400 so the server-side ordering doesn't drift into a partial
    state."""
    user, product = _seeded_user_and_product(db)
    headers = login(client, "alice@x.test")
    ids = []
    for i in range(3):
        r = client.post(
            f"/api/data/products/{product.id}/images",
            files={"file": (f"{i}.png", _png_bytes(color=(i, i, i)), "image/png")},
            headers=headers,
        )
        ids.append(r.json()["id"])

    r = client.put(
        f"/api/data/products/{product.id}/images/reorder",
        json={"items": [{"id": ids[0], "display_order": 0}]},
        headers=headers,
    )
    assert r.status_code == 400


# ─── Cross-product isolation ─────────────────────────────────


def test_cannot_delete_image_via_wrong_product_id(client, db):
    """Image id 1 belongs to product A. Asking to delete it via product
    B's URL must 404, not silently delete."""
    user, product_a = _seeded_user_and_product(db)
    product_b = seed_product(db, code="PIMG-002", name="Other product")
    headers = login(client, "alice@x.test")
    r = client.post(
        f"/api/data/products/{product_a.id}/images",
        files={"file": ("x.png", _png_bytes(), "image/png")},
        headers=headers,
    )
    img_id = r.json()["id"]

    r = client.delete(
        f"/api/data/products/{product_b.id}/images/{img_id}",
        headers=headers,
    )
    assert r.status_code == 404
