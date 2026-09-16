"""Section 6 — Masterdata: bulk image upload via ZIP (2026-06-22).

为什么这套测试存在：
    Felix 提出"一个一个上传图片不现实"。新增的 ZIP 批量上传流允许
    `<country>/<port>/<code>/` 目录结构 → 后端按三元组精确匹配 →
    预览（不入库）→ 用户确认 → 后台任务调 add_product_image 入库。

    这条 pipeline 跨多个边界（pure ZIP 函数 / DB 匹配 / HTTP 状态机 /
    异步入库 / GCS 暂存）。每一环都需要回归测试，否则未来重构会让
    100 张图悄无声息地传到错的产品上。

锁定的契约：
    A. ZIP slip 攻击被拒
    B. 模板生成只包含过滤后的产品
    C. 三层目录解析正确
    D. 三元组匹配命中 → status=matched，未命中 → status=unmatched
    E. 不支持的扩展 → status=error（不丢弃，让用户看到）
    F. preview **不**入库（v3_product_images 不变）
    G. commit 后逐行入库 + 失败不阻塞
    H. 状态机：preview_ready 才能 commit，processing/completed 不能再 commit
    I. cancel 后 ZIP storage 被清理
    J. 已有图的产品 → 模板里带 ✓ 标记
"""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal

import pytest

from domains.masterdata.images import bulk_service, bulk_upload
from domains.masterdata.images.bulk_models import (
    BulkImageBatch,
    BulkImageStaging,
)
from domains.masterdata.models import ProductImage
from test_v2.fixtures.helpers import seed_product, seed_user


# ─── Helpers ──────────────────────────────────────────────────


def _make_zip(structure: dict[str, bytes]) -> bytes:
    """Build an in-memory ZIP. Keys are zip-relative paths; values are bytes.

    Used by tests to construct uploads without touching the filesystem.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in structure.items():
            if path.endswith("/"):
                zf.writestr(path, b"")
            else:
                zf.writestr(path, content)
    return buf.getvalue()


def _tiny_png() -> bytes:
    """1x1 transparent PNG bytes — smallest valid PNG. Used as test image."""
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae"
        "426082"
    )


# ─── A. ZIP slip defense ──────────────────────────────────────


def test_zip_slip_attack_is_rejected():
    """A malicious ZIP with `..` paths must NOT produce a ParsedEntry —
    even though we never call extract(), defense in depth: if a future
    code path ever extracts to disk, the guard already exists.
    """
    evil = _make_zip(
        {
            "../../../etc/passwd/USA/Tokyo/X1/a.jpg": _tiny_png(),
            "/absolute/USA/Tokyo/X2/b.jpg": _tiny_png(),
        }
    )
    entries, warnings = bulk_upload.parse_uploaded_zip(evil)
    # Both must be filtered out via the slip guard.
    paths = [e.zip_path for e in entries]
    assert all(".." not in p for p in paths)
    assert all(not p.startswith("/") for p in paths)
    # And warnings tell us why.
    assert any("zip slip" in w for w in warnings)


# ─── B. Template generation respects filters ────────────────


def test_template_includes_only_filtered_products(db):
    """Filter by country_id → only those products' directories appear."""
    from domains.masterdata.models import Country, Port

    seed_user(db, email="t1@example.com", role="admin")
    # Seed: 2 countries, 1 port each, 2 products per country.
    c1 = Country(name="USA")
    c2 = Country(name="Japan")
    db.add_all([c1, c2])
    db.commit()
    p1 = Port(name="LA", country_id=c1.id)
    p2 = Port(name="Tokyo", country_id=c2.id)
    db.add_all([p1, p2])
    db.commit()

    seed_product(db, code="USA-001", name="A", country_id=c1.id, port_id=p1.id)
    seed_product(db, code="USA-002", name="B", country_id=c1.id, port_id=p1.id)
    seed_product(db, code="JP-001", name="C", country_id=c2.id, port_id=p2.id)
    seed_product(db, code="JP-002", name="D", country_id=c2.id, port_id=p2.id)

    # Filter to USA only.
    zip_bytes = bulk_service.build_template(db, country_ids=[c1.id])
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    assert any("USA-001" in n for n in names)
    assert any("USA-002" in n for n in names)
    assert not any("JP-001" in n for n in names)
    assert not any("JP-002" in n for n in names)


def test_template_marks_products_with_existing_images(db):
    """Products that already have images get a ✓ in the folder name."""
    from datetime import datetime

    from domains.masterdata.models import Country, Port

    seed_user(db, email="t2@example.com", role="admin")
    c = Country(name="USA")
    db.add(c)
    db.commit()
    p = Port(name="Tokyo", country_id=c.id)
    db.add(p)
    db.commit()

    has_img_prod = seed_product(
        db, code="HAS-IMG", name="X", country_id=c.id, port_id=p.id
    )
    seed_product(db, code="NO-IMG", name="Y", country_id=c.id, port_id=p.id)

    # Give one product an image row.
    db.add(
        ProductImage(
            product_id=has_img_prod.id,
            storage_key="k/full.jpg",
            thumbnail_key="k/thumb.jpg",
            medium_key="k/med.jpg",
            filename="a.jpg",
            file_type="image/jpeg",
            file_size_bytes=100,
            display_order=0,
            uploaded_by_user_id=1,
            uploaded_at=datetime.utcnow(),
        )
    )
    db.commit()

    zip_bytes = bulk_service.build_template(db)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    # Folder format is `<code> — <name> ✓?` — the ✓ sits at the end
    # of the full label regardless of whether the name suffix is
    # present. Match on "HAS-IMG" + trailing ✓ via the per-name
    # endswith check (the closing slash for the dir entry).
    assert any(n.rstrip("/").endswith("✓") and "HAS-IMG" in n for n in names), (
        f"missing trailing ✓ on HAS-IMG; names = {names}"
    )
    assert not any(
        n.rstrip("/").endswith("✓") and "NO-IMG" in n for n in names
    ), (
        f"NO-IMG should be unmarked; names = {names}"
    )


def test_template_only_missing_images_filter(db):
    """`only_missing_images=True` skips products that already have a ProductImage row."""
    from datetime import datetime

    from domains.masterdata.models import Country, Port

    seed_user(db, email="t3@example.com", role="admin")
    c = Country(name="USA")
    db.add(c)
    db.commit()
    p = Port(name="LA", country_id=c.id)
    db.add(p)
    db.commit()

    has = seed_product(db, code="HAS", name="X", country_id=c.id, port_id=p.id)
    seed_product(db, code="MISSING", name="Y", country_id=c.id, port_id=p.id)
    db.add(
        ProductImage(
            product_id=has.id,
            storage_key="k1",
            thumbnail_key="k1t",
            medium_key="k1m",
            filename="a.jpg",
            file_type="image/jpeg",
            file_size_bytes=100,
            display_order=0,
            uploaded_by_user_id=1,
            uploaded_at=datetime.utcnow(),
        )
    )
    db.commit()

    zip_bytes = bulk_service.build_template(db, only_missing_images=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    assert any("MISSING" in n for n in names)
    assert not any("HAS" in n for n in names), (
        "only_missing=True should skip products with existing images"
    )


# ─── C+D. Parser layering + matching ────────────────────────


def test_parse_extracts_correct_triples_and_matches(db):
    """A 4-deep path `products/<country>/<port>/<code>/<file>` resolves
    to the right Product row via three-tuple lookup."""
    from domains.masterdata.models import Country, Port

    user = seed_user(db, email="p1@example.com", role="admin")
    c = Country(name="USA")
    db.add(c)
    db.commit()
    p = Port(name="Yokohama", country_id=c.id)
    db.add(p)
    db.commit()

    target = seed_product(
        db, code="BEEF-001", name="Beef", country_id=c.id, port_id=p.id
    )

    zip_bytes = _make_zip(
        {
            "products/USA/Yokohama/BEEF-001/photo1.jpg": _tiny_png(),
            "products/USA/Yokohama/BEEF-001/photo2.png": _tiny_png(),
        }
    )

    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="test.zip", zip_bytes=zip_bytes
    )
    assert batch.status == "preview_ready", batch.error_message
    assert batch.total_files == 2
    assert batch.matched_count == 2
    assert batch.unmatched_count == 0

    rows = (
        db.query(BulkImageStaging)
        .filter_by(batch_id=batch.id)
        .order_by(BulkImageStaging.id)
        .all()
    )
    assert all(r.product_id == target.id for r in rows)
    assert all(r.status == "matched" for r in rows)


def test_parse_unmatched_when_triple_not_in_db(db):
    """Code typo → status=unmatched + helpful error message."""
    user = seed_user(db, email="p2@example.com", role="admin")
    # No DB seed → every entry will fail to match.

    zip_bytes = _make_zip(
        {"products/Mars/Olympus/UNKNOWN/photo.jpg": _tiny_png()}
    )
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )
    assert batch.matched_count == 0
    assert batch.unmatched_count == 1
    row = db.query(BulkImageStaging).filter_by(batch_id=batch.id).one()
    assert row.status == "unmatched"
    assert "UNKNOWN" in row.error_message


# ─── E. Bad extension → error entry, not silent drop ─────────


def test_unsupported_extension_becomes_error_row(db):
    """`.heic` etc. surface as error rows so user sees them in the
    report — silent drops would hide their existence."""
    user = seed_user(db, email="p3@example.com", role="admin")

    zip_bytes = _make_zip(
        {"products/USA/LA/X1/picture.heic": _tiny_png()}
    )
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )
    assert batch.error_count == 1
    row = db.query(BulkImageStaging).filter_by(batch_id=batch.id).one()
    assert row.status == "error"
    assert "heic" in row.error_message.lower()


# ─── F. Preview does NOT mutate v3_product_images ────────────


def test_preview_does_not_ingest_into_product_images(db, session_factory):
    """Critical contract: preview is read-only. If a regression starts
    writing to v3_product_images at preview time, the row count rises
    and this test red-lights."""
    from domains.masterdata.models import Country, Port

    user = seed_user(db, email="p4@example.com", role="admin")
    c = Country(name="USA")
    db.add(c)
    db.commit()
    p = Port(name="LA", country_id=c.id)
    db.add(p)
    db.commit()
    seed_product(db, code="P1", name="X", country_id=c.id, port_id=p.id)

    before = db.query(ProductImage).count()
    zip_bytes = _make_zip({"products/USA/LA/P1/a.jpg": _tiny_png()})
    bulk_service.create_preview(
        db, user_id=user.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )
    after = db.query(ProductImage).count()
    assert before == after, (
        "preview must NOT write to v3_product_images; "
        f"row count changed {before} → {after}"
    )


# ─── G+H. Commit flips status + ingests ─────────────────────


def test_commit_ingests_and_status_machine_locks_double_commit(
    db, session_factory
):
    """End-to-end: preview → commit (sync via SynchronousRunner) →
    rows land in v3_product_images. Second commit attempt 409s."""
    from domains.masterdata.models import Country, Port

    user = seed_user(db, email="p5@example.com", role="admin")
    c = Country(name="USA")
    db.add(c)
    db.commit()
    p = Port(name="LA", country_id=c.id)
    db.add(p)
    db.commit()
    target = seed_product(
        db, code="P5", name="X", country_id=c.id, port_id=p.id
    )

    zip_bytes = _make_zip({"products/USA/LA/P5/a.jpg": _tiny_png()})
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )

    # Trigger + synchronous run (test conftest SynchronousRunner does
    # `await fn()` inline). We invoke the sync helper directly to keep
    # this test independent of the runner abstraction.
    bulk_service.trigger_commit(
        db, batch_id=batch.id, user_id=user.id, is_admin=True
    )
    bulk_service._run_commit_sync(batch.id)

    # Verify ingestion
    fresh = session_factory()
    try:
        imgs = (
            fresh.query(ProductImage)
            .filter_by(product_id=target.id)
            .all()
        )
        assert len(imgs) == 1
        batch_reloaded = fresh.get(BulkImageBatch, batch.id)
        assert batch_reloaded.status == "completed"
        assert batch_reloaded.ingested_count == 1
    finally:
        fresh.close()

    # State machine: completed → can't re-commit
    fresh2 = session_factory()
    try:
        with pytest.raises(bulk_service.StatusConflict):
            bulk_service.trigger_commit(
                fresh2, batch_id=batch.id, user_id=user.id, is_admin=True
            )
    finally:
        fresh2.close()


# ─── I. Cancel cleans up ─────────────────────────────────────


def test_cancel_clears_storage_key_and_flips_status(db):
    """After cancel, the ZIP storage_key is cleared and status=cancelled.
    Subsequent commit attempts must conflict."""
    user = seed_user(db, email="p6@example.com", role="admin")
    zip_bytes = _make_zip({"products/USA/LA/X/a.jpg": _tiny_png()})
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )
    assert batch.zip_storage_key is not None

    bulk_service.cancel_batch(
        db, batch_id=batch.id, user_id=user.id, is_admin=True
    )
    db.refresh(batch)
    assert batch.status == "cancelled"
    assert batch.zip_storage_key is None

    with pytest.raises(bulk_service.StatusConflict):
        bulk_service.trigger_commit(
            db, batch_id=batch.id, user_id=user.id, is_admin=True
        )


# ─── J. Cross-user isolation ─────────────────────────────────


def test_user_cannot_see_other_users_batch(db):
    """Non-admin user can only see their own batches. Admin sees all."""
    user_a = seed_user(db, email="a@example.com", role="employee")
    seed_user(db, email="b@example.com", role="employee")

    zip_bytes = _make_zip({"products/USA/LA/X/a.jpg": _tiny_png()})
    batch = bulk_service.create_preview(
        db, user_id=user_a.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )

    # User B can't see user A's batch.
    with pytest.raises(bulk_service.NotFound):
        bulk_service.list_batch(
            db, batch_id=batch.id, user_id=user_a.id + 999, is_admin=False
        )

    # But admin can.
    visible = bulk_service.list_batch(
        db, batch_id=batch.id, user_id=999, is_admin=True
    )
    assert visible["id"] == batch.id


# ─── L. Folder-name `<code> — <name>` format ────────────────


def test_template_folder_includes_product_name(db):
    """2026-06-22 UX fix: folder names embed the English product name
    so users can tell what each code refers to without leaving Finder.
    Format: `<code> — <name>` (em dash). Code alone is not enough."""
    from domains.masterdata.models import Country, Port

    seed_user(db, email="fn1@example.com", role="admin")
    c = Country(name="USA")
    db.add(c)
    db.commit()
    p = Port(name="LA", country_id=c.id)
    db.add(p)
    db.commit()
    seed_product(
        db,
        code="FN-1",
        name="Apple Red Delicious 125ct",
        country_id=c.id,
        port_id=p.id,
    )

    zip_bytes = bulk_service.build_template(db)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    # Expect the em-dash separator + the readable name.
    assert any("FN-1 — Apple Red Delicious 125ct" in n for n in names), (
        f"folder should include `<code> — <name>`; names = {names}"
    )


def test_parser_extracts_code_from_new_format(db):
    """Path with `<code> — <name>` folder still matches the product
    by code only — the name suffix is purely cosmetic."""
    from domains.masterdata.models import Country, Port

    user = seed_user(db, email="fn2@example.com", role="admin")
    c = Country(name="USA")
    db.add(c)
    db.commit()
    p = Port(name="LA", country_id=c.id)
    db.add(p)
    db.commit()
    target = seed_product(
        db, code="FN-2", name="Banana", country_id=c.id, port_id=p.id
    )

    # User downloaded the v2 template and uploads with the full label.
    zip_bytes = _make_zip(
        {"products/USA/LA/FN-2 — Banana Cavendish/photo.jpg": _tiny_png()}
    )
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )
    assert batch.matched_count == 1
    row = db.query(BulkImageStaging).filter_by(batch_id=batch.id).one()
    assert row.product_id == target.id
    # The parsed code field stores ONLY the code, not the cosmetic name.
    assert row.product_code == "FN-2", (
        f"product_code field should drop the cosmetic name; got {row.product_code!r}"
    )


def test_parser_handles_legacy_code_only_format(db):
    """Users who downloaded the v1 template (before this fix) must
    keep uploading successfully — folder is code-only with no separator."""
    from domains.masterdata.models import Country, Port

    user = seed_user(db, email="fn3@example.com", role="admin")
    c = Country(name="USA")
    db.add(c)
    db.commit()
    p = Port(name="LA", country_id=c.id)
    db.add(p)
    db.commit()
    seed_product(db, code="LEGACY-1", name="X", country_id=c.id, port_id=p.id)

    zip_bytes = _make_zip(
        {"products/USA/LA/LEGACY-1/photo.jpg": _tiny_png()}
    )
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )
    assert batch.matched_count == 1, "legacy code-only folders must keep matching"


def test_parser_handles_checkmark_with_new_format(db):
    """`<code> — <name> ✓` (the marked variant of the new format) must
    parse the same as the unmarked variant. The ✓ is stripped before
    name extraction."""
    from domains.masterdata.models import Country, Port

    user = seed_user(db, email="fn4@example.com", role="admin")
    c = Country(name="USA")
    db.add(c)
    db.commit()
    p = Port(name="LA", country_id=c.id)
    db.add(p)
    db.commit()
    target = seed_product(
        db, code="MK-1", name="X", country_id=c.id, port_id=p.id
    )

    zip_bytes = _make_zip(
        {"products/USA/LA/MK-1 — X ✓/new.jpg": _tiny_png()}
    )
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )
    assert batch.matched_count == 1
    row = db.query(BulkImageStaging).filter_by(batch_id=batch.id).one()
    assert row.product_id == target.id
    assert row.product_code == "MK-1"


def test_parse_folder_code_pure_helper():
    """Pure-function spot check for the parser helper. Cheap, no DB."""
    from domains.masterdata.images.bulk_upload import _parse_folder_code

    # New format
    assert _parse_folder_code("BEEF-001 — Beef Tenderloin") == "BEEF-001"
    # New format with checkmark
    assert _parse_folder_code("BEEF-001 — Beef Tenderloin ✓") == "BEEF-001"
    # Legacy format
    assert _parse_folder_code("LEGACY-1") == "LEGACY-1"
    assert _parse_folder_code("LEGACY-1 ✓") == "LEGACY-1"
    # Code that contains hyphens (the reason we use em dash)
    assert _parse_folder_code("MULTI-PART-CODE-001 — Name") == "MULTI-PART-CODE-001"


# ─── K. macOS metadata is silently skipped ──────────────────


def test_normalize_zip_filename_recovers_cp437_mojibake():
    """Pure-function test for the encoding-recovery helper.

    Python's `zipfile.ZipFile` auto-sets the UTF-8 flag on write when
    filenames carry non-ASCII bytes, so we can't easily produce a
    "broken" ZIP via Python alone. But the helper itself is pure: feed
    it a ZipInfo with `flag_bits=0` and the mojibake string Python WOULD
    have produced for an un-flagged UTF-8 filename, and assert the
    round-trip restores the original.
    """
    from domains.masterdata.images.bulk_upload import _normalize_zip_filename

    real = "AUSTRALIA/BRISBANE/99PRD010601 — ASPARAGUS GREEN LARGE/photo.jpg"
    mojibake = real.encode("utf-8").decode("cp437")
    assert mojibake != real, "fixture invariant: mojibake string differs"

    info = zipfile.ZipInfo()
    info.flag_bits = 0  # ← producer did NOT set the UTF-8 flag
    info.filename = mojibake
    assert _normalize_zip_filename(info) == real

    # And: when the flag IS set, the helper must NOT touch the name
    # (Python's decoding was already correct).
    info2 = zipfile.ZipInfo()
    info2.flag_bits = 0x800
    info2.filename = real
    assert _normalize_zip_filename(info2) == real


def _make_zip_without_utf8_flag(structure: dict[str, bytes]) -> bytes:
    """Build a ZIP whose entries do NOT have the UTF-8 flag bit set.

    Python's `zipfile.ZipFile` writes the UTF-8 flag automatically for
    names with non-ASCII chars. To reproduce the bug from the field
    we post-process the written bytes and clear bit 11 (0x800) in both
    the Local File Header (offset 6) and the Central Directory File
    Header (offset 8). The flag is stored little-endian, so we mask
    the low byte of each 2-byte field.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in structure.items():
            # Write with the real UTF-8 string — Python serializes the
            # name as UTF-8 bytes on disk (because non-ASCII chars are
            # present) and sets the UTF-8 flag. We then clear the flag
            # below so the reader falls back to cp437 — exactly the
            # field bug shape.
            zf.writestr(zipfile.ZipInfo(path), content)
    data = bytearray(buf.getvalue())

    # Clear UTF-8 flag (bit 11 → mask 0xF7 on high byte) in every
    # local file header (PK\x03\x04, flag at +6) and central directory
    # header (PK\x01\x02, flag at +8).
    i = 0
    while i < len(data) - 4:
        if data[i : i + 4] == b"PK\x03\x04":
            data[i + 7] &= 0xF7  # clear bit 11 of the 2-byte flag at +6
            i += 30
        elif data[i : i + 4] == b"PK\x01\x02":
            data[i + 9] &= 0xF7  # bit 11 of 2-byte flag at +8
            i += 46
        else:
            i += 1
    return bytes(data)


def test_parse_zip_without_utf8_flag_recovers_em_dash(db):
    """End-to-end version of the 2026-07-01 fix: produce a real ZIP
    that mimics the field bug (UTF-8 bytes on disk + flag bit not set),
    then assert parse_uploaded_zip recovers the em dash and matches."""
    from domains.masterdata.models import Country, Port

    user = seed_user(db, email="zipmoji@example.com", role="admin")
    c = Country(name="AUSTRALIA")
    db.add(c)
    db.commit()
    p = Port(name="BRISBANE", country_id=c.id)
    db.add(p)
    db.commit()
    target = seed_product(
        db,
        code="99PRD010601",
        name="ASPARAGUS GREEN LARGE",
        country_id=c.id,
        port_id=p.id,
    )

    zip_bytes = _make_zip_without_utf8_flag(
        {
            "AUSTRALIA/BRISBANE/99PRD010601 — ASPARAGUS GREEN LARGE/photo.jpg": _tiny_png(),
        }
    )
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="legacy.zip", zip_bytes=zip_bytes
    )
    assert batch.matched_count == 1, (
        f"non-UTF8-flagged ZIP must round-trip; status={batch.status}"
        f" matched={batch.matched_count} unmatched={batch.unmatched_count}"
    )
    row = db.query(BulkImageStaging).filter_by(batch_id=batch.id).one()
    assert row.product_id == target.id
    assert row.product_code == "99PRD010601"


def test_instructions_txt_skipped_regardless_of_wrapping_folder(db):
    """User's macOS workflow often produces ZIPs whose root folder is
    named after the download (`product-images-template-photo/`) rather
    than `products/`. The Instructions.txt artifact lives at the root
    of that wrapping folder. It must be skipped silently rather than
    surfacing as an unmatched-row warning."""
    user = seed_user(db, email="wrap@example.com", role="admin")

    zip_bytes = _make_zip(
        {
            "product-images-template-photo/Instructions.txt": b"docs",
            "product-images-template-photo/USA/LA/X/photo.jpg": _tiny_png(),
        }
    )
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )
    # Only the photo should appear; Instructions.txt is template
    # artifact regardless of which folder wraps it.
    assert batch.total_files == 1, (
        f"Instructions.txt under wrapping folder should be skipped silently;"
        f" total_files={batch.total_files}"
    )


# ─── M. Reliability hardening (2026-07-03) ───────────────────


def test_second_active_batch_is_rejected(db):
    """Single-active-batch-per-user contract: a preview_ready or
    processing batch blocks a second create_preview call. Prevents
    UI confusion (only 1 progress bar) + prevents 2 workers racing
    on MAX_IMAGES_PER_PRODUCT."""
    user = seed_user(db, email="active@example.com", role="admin")
    zip_bytes = _make_zip({"products/USA/LA/X/a.jpg": _tiny_png()})
    bulk_service.create_preview(
        db, user_id=user.id, zip_filename="1.zip", zip_bytes=zip_bytes
    )
    # Second call must 400.
    with pytest.raises(bulk_service.BadRequest) as exc_info:
        bulk_service.create_preview(
            db, user_id=user.id, zip_filename="2.zip", zip_bytes=zip_bytes
        )
    assert "未完成" in str(exc_info.value)


def test_sweep_closes_stale_batches(db):
    """Batches older than ttl_hours in `preview_ready` are cancelled
    and their ZIP is deleted."""
    from datetime import datetime, timedelta

    user = seed_user(db, email="stale@example.com", role="admin")
    zip_bytes = _make_zip({"products/USA/LA/X/a.jpg": _tiny_png()})
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="s.zip", zip_bytes=zip_bytes
    )
    # Time-travel: pretend this batch was created 25h ago.
    batch.created_at = datetime.utcnow() - timedelta(hours=25)
    db.commit()

    result = bulk_service.sweep_stale_batches(db, ttl_hours=24)
    assert result["stale_batches"] == 1
    assert result["storage_deleted"] == 1

    db.refresh(batch)
    assert batch.status == "cancelled"
    assert batch.zip_storage_key is None
    assert "abandoned" in (batch.error_message or "")


def test_sweep_fails_stuck_processing_batch(db):
    """`processing` with heartbeat > stuck_minutes → status=error.
    Simulates the worker dying mid-run (CPU throttling / crash)."""
    from datetime import datetime, timedelta

    user = seed_user(db, email="stuck@example.com", role="admin")
    zip_bytes = _make_zip({"products/USA/LA/X/a.jpg": _tiny_png()})
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="p.zip", zip_bytes=zip_bytes
    )
    # Fake a worker that flipped to processing then died.
    batch.status = "processing"
    batch.updated_at = datetime.utcnow() - timedelta(minutes=20)
    db.commit()

    result = bulk_service.sweep_stale_batches(db, stuck_minutes=15)
    assert result["stuck_batches"] == 1

    db.refresh(batch)
    assert batch.status == "error"
    assert "卡住" in (batch.error_message or "")


def test_sweep_dry_run_does_not_mutate(db):
    """`dry_run=True` counts what would happen but changes nothing —
    lets us validate cron config without cleaning real data."""
    from datetime import datetime, timedelta

    user = seed_user(db, email="dry@example.com", role="admin")
    zip_bytes = _make_zip({"products/USA/LA/X/a.jpg": _tiny_png()})
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="d.zip", zip_bytes=zip_bytes
    )
    batch.created_at = datetime.utcnow() - timedelta(hours=48)
    db.commit()
    original_status = batch.status
    original_key = batch.zip_storage_key

    result = bulk_service.sweep_stale_batches(db, dry_run=True)
    assert result["stale_batches"] == 1
    assert result["storage_deleted"] == 0  # nothing actually deleted

    db.refresh(batch)
    assert batch.status == original_status
    assert batch.zip_storage_key == original_key


def test_force_cancel_allows_processing_batch(db):
    """`force=True` lets the user rescue a stuck processing batch
    from the UI (the 15-min polling timeout exposes this button)."""
    user = seed_user(db, email="force@example.com", role="admin")
    zip_bytes = _make_zip({"products/USA/LA/X/a.jpg": _tiny_png()})
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="f.zip", zip_bytes=zip_bytes
    )
    batch.status = "processing"
    db.commit()

    # Non-force refuses.
    with pytest.raises(bulk_service.StatusConflict):
        bulk_service.cancel_batch(
            db, batch_id=batch.id, user_id=user.id, is_admin=True, force=False
        )
    # Force accepts.
    bulk_service.cancel_batch(
        db, batch_id=batch.id, user_id=user.id, is_admin=True, force=True
    )
    db.refresh(batch)
    assert batch.status == "cancelled"
    assert batch.zip_storage_key is None


def test_heartbeat_updated_on_row_change(db):
    """`updated_at` bumps whenever any column changes. The sweep
    depends on this being fresher than `created_at` for live workers."""
    from datetime import datetime, timedelta

    user = seed_user(db, email="hb@example.com", role="admin")
    zip_bytes = _make_zip({"products/USA/LA/X/a.jpg": _tiny_png()})
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="hb.zip", zip_bytes=zip_bytes
    )
    initial = batch.updated_at
    assert initial is not None

    # Simulate the passage of a real second so the DB-side onupdate
    # yields a different timestamp — some backends round to seconds.
    import time as _time
    _time.sleep(1.05)
    batch.status = "processing"
    db.commit()
    db.refresh(batch)
    assert batch.updated_at is not None
    assert batch.updated_at > initial, (
        f"onupdate must fire on any column change; initial={initial}"
        f" new={batch.updated_at}"
    )


def test_macos_metadata_is_skipped(db):
    """__MACOSX/ and .DS_Store entries must NOT become unmatched rows —
    that would clutter the report."""
    user = seed_user(db, email="mac@example.com", role="admin")
    zip_bytes = _make_zip(
        {
            "__MACOSX/products/USA/LA/X/._a.jpg": b"junk",
            "products/USA/LA/X/.DS_Store": b"junk",
            "products/USA/LA/X/photo.jpg": _tiny_png(),
        }
    )
    batch = bulk_service.create_preview(
        db, user_id=user.id, zip_filename="x.zip", zip_bytes=zip_bytes
    )
    # Only the photo.jpg should appear — the other two are macOS metadata.
    assert batch.total_files == 1
