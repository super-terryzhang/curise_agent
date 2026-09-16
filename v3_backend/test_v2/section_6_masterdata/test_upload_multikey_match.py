"""Section 6 — Replay test for the prod batch #5 incident (2026-05-18).

测试目标：
    复现 prod 那次 69 行上传 → 57 IntegrityError 的真实场景，并断言
    新的多键匹配策略把它们全部正确路由：
      - DB 里已经有 (code, country=日本, port=横浜) 的产品 → UPDATE 那行
      - DB 里只有别港但同 code 的产品 → INSERT 新行（新港口）
      - DB 完全没有的 code → INSERT 新产品

    使用真实的 `test-orders/product_upload_template.xlsx` 文件作为上传
    输入，避免合成测试漏掉真实 Excel 的格式问题。

修复前行为（已通过 prod batch #5 验证）：
    - 用 code 单字段索引 → dict 覆盖只剩 1 条 → 任意挑（不一定是横浜行）
    - UPDATE 该行的 port_id=19 → 撞唯一约束 (country, name, port)
    - 57/69 报错；错误细节被 match_status="exact" 屏蔽，preview 看不到

修复后预期：
    - Rule 1: (code=X, country=日本, port=横浜) → 命中横浜行 → 安全 UPDATE
    - 既存横浜行的产品全部更新成功
    - 横浜没记录的产品 → INSERT 新行
    - errors=0，没有 spurious IntegrityError
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domains.masterdata.upload import service as upload_service
from domains.masterdata.upload.models import StagingProduct, UploadBatch
from domains.masterdata.models import Category, Country, Port, Product, Supplier


# Resolve relative to repo: v3_backend/test_v2/section_6_masterdata/ →
# v3_backend/ → curise_agent/ → curise_agent/test-orders/.
# Same convention as `_SAMPLE_PDF_DIR` in test_v2/conftest.py.
REAL_TEMPLATE = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "test-orders"
    / "product_upload_template.xlsx"
)


def _seed_masterdata_japan(db) -> dict[str, int]:
    """Seed Japan + 横浜/大阪/北海道 ports + minimal categories/suppliers
    so the FK resolver in the upload pipeline succeeds for the real Excel."""
    japan = Country(name="Japan")
    bangkok_country = Country(name="Thailand")  # for the lone BANGKOK row (country=12 in prod)
    yokohama = Port(name="横浜 大さん橋")
    osaka = Port(name="大阪")
    hokkaido = Port(name="北海道")
    bangkok_port = Port(name="BANGKOK (LAEMCHABANG)")
    produce = Category(name="PRODUCE")
    dairy = Category(name="DAIRY")
    grocery = Category(name="GROCERY")
    # The real Excel references 4 distinct suppliers (verified via openpyxl):
    #   57× 株式会社　松武, 9× 株式会社三祐, 2× タカナシ販売株式会社, 1× ㈲田浦中央食品
    matsutake_supplier = Supplier(name="株式会社　松武")
    sanyu_supplier = Supplier(name="株式会社三祐")
    takanashi_supplier = Supplier(name="タカナシ販売株式会社")
    taura_supplier = Supplier(name="㈲田浦中央食品")
    db.add_all([
        japan, bangkok_country,
        yokohama, osaka, hokkaido, bangkok_port,
        produce, dairy, grocery,
        matsutake_supplier, sanyu_supplier, takanashi_supplier, taura_supplier,
    ])
    db.commit()
    return {
        "japan_id": japan.id,
        "yokohama_id": yokohama.id,
        "osaka_id": osaka.id,
        "hokkaido_id": hokkaido.id,
        "supplier_id": matsutake_supplier.id,
        "produce_id": produce.id,
    }


def _seed_replica_of_prod_collision(db, ids: dict) -> dict[str, int]:
    """Seed a handful of products that REPLICATE the prod bug shape:
    same `code` appearing at MULTIPLE ports — including 横浜 — so that
    when the Excel (which wants 横浜) is uploaded, the matcher has to
    pick the right port-specific row.

    Replicates 5 of the prod collision cases from batch #5:
      MELON WATERMELON RED 65LB at 横浜 + 大阪
      PINEAPPLE GOLDEN 5-6CT/25LB at 横浜 + 北海道
      MELON CANTALOUPE JUMBO 9CT/40LB at 横浜 + 大阪
      BANANA STAGE 3 PREMIUM at 横浜 + 北海道
      BERRY BLUEBERRY 12X6 OZ at 横浜 only (control — was the lucky success in prod)
    """
    rows = [
        # MELON WATERMELON — has both 横浜 (target) and 大阪 (wrong-picked-in-prod)
        ("99PRD011390", "MELON WATERMELON RED 65LB", ids["yokohama_id"], 999.0),
        ("99PRD011390", "MELON WATERMELON RED 65LB", ids["osaka_id"], 888.0),
        # PINEAPPLE — yokohama + hokkaido
        ("99PRD24469", "PINEAPPLE GOLDEN 5-6CT/25LB", ids["yokohama_id"], 999.0),
        ("99PRD24469", "PINEAPPLE GOLDEN 5-6CT/25LB", ids["hokkaido_id"], 888.0),
        # MELON CANTALOUPE — yokohama + osaka
        ("99PRD011388", "MELON CANTALOUPE JUMBO 9CT/40LB", ids["yokohama_id"], 999.0),
        ("99PRD011388", "MELON CANTALOUPE JUMBO 9CT/40LB", ids["osaka_id"], 888.0),
        # BANANA — yokohama + hokkaido
        ("99PRD51974", "BANANA STAGE 3 PREMIUM 90-100CT/40LB", ids["yokohama_id"], 999.0),
        ("99PRD51974", "BANANA STAGE 3 PREMIUM 90-100CT/40LB", ids["hokkaido_id"], 888.0),
        # BLUEBERRY — yokohama only (control: prod batch had this as a "lucky" pass)
        ("99PRD010692", "BERRY BLUEBERRY 12X6 OZ", ids["yokohama_id"], 999.0),
    ]
    out: dict[str, int] = {}
    for code, name, port_id, price in rows:
        p = Product(
            code=code,
            product_name_en=name,
            country_id=ids["japan_id"],
            port_id=port_id,
            supplier_id=ids["supplier_id"],
            category_id=ids["produce_id"],
            price=price,
            currency="JPY",
            unit="KG",
            status=True,
        )
        db.add(p)
    db.commit()
    # Index the横浜 row ids so the test can assert "this exact row got UPDATEd"
    for code, name, port_id, _ in rows:
        if port_id == ids["yokohama_id"]:
            row = (
                db.query(Product)
                .filter(Product.code == code, Product.port_id == ids["yokohama_id"])
                .one()
            )
            out[code] = row.id
    return out


def test_replay_prod_batch5_multikey_matching(db, seed_user):
    """Parse real product_upload_template.xlsx → resolve → commit. Assert:
      - The 横浜 versions of seeded products get UPDATEd (right rows picked)
      - Errors do NOT spike — no spurious IntegrityErrors from picking the
        wrong-port row and trying to mutate port_id
    """
    if not REAL_TEMPLATE.exists():
        pytest.skip(f"real template not found at {REAL_TEMPLATE}")

    ids = _seed_masterdata_japan(db)
    expected_yokohama_ids = _seed_replica_of_prod_collision(db, ids)

    file_bytes = REAL_TEMPLATE.read_bytes()

    # ─── Stage 1: parse ─────────────────────────────────────
    batch = upload_service.parse_excel(
        db,
        file_bytes=file_bytes,
        filename="product_upload_template.xlsx",
        user_id=seed_user.id,
    )
    assert batch.total_rows == 69
    assert batch.parsed_rows == 69

    # ─── Stage 2: resolve ───────────────────────────────────
    batch = upload_service.resolve_and_score(
        db, batch_id=batch.id, user_id=seed_user.id
    )

    # For each seeded code, the matched target MUST be the 横浜 row, not
    # the 大阪/北海道 row.
    for code, expected_pid in expected_yokohama_ids.items():
        staged = (
            db.query(StagingProduct)
            .filter(
                StagingProduct.batch_id == batch.id,
                StagingProduct.product_code == code,
            )
            .one()
        )
        assert staged.match_target_id == expected_pid, (
            f"code={code}: matcher picked id={staged.match_target_id}, "
            f"expected 横浜 row id={expected_pid}"
        )
        assert staged.match_status == "exact"

    # ─── Stage 3: commit ────────────────────────────────────
    result = upload_service.commit_batch(
        db, batch_id=batch.id, user_id=seed_user.id
    )

    # No spurious errors on the 9 seeded products. The other 60 Excel rows
    # match no DB row (because we only seeded 9) → they should INSERT as
    # "new". So errors must remain 0 for seeded products and we should see
    # successful created/updated counts.
    assert result["errors"] == 0, (
        f"unexpected errors after fix: result={result}"
    )

    # We seeded 5 unique横浜 products + 4 non-横浜 dupes = 9 DB rows. The
    # Excel UPDATEs the 5 横浜 rows. The other 64 rows in the Excel match
    # no seeded products → become "new" → INSERTed.
    assert result["updated"] >= 5, f"updated should cover 5 seeded 横浜 rows: {result}"
    assert result["created"] >= 60, f"non-seeded rows should INSERT: {result}"

    # Sanity: the 5 横浜 rows we seeded got their price updated.
    yokohama_blueberry_id = expected_yokohama_ids["99PRD010692"]
    updated_blueberry = db.get(Product, yokohama_blueberry_id)
    # Excel's blueberry price (per template) is the new price; the seed
    # used 999.0 as a sentinel. After update, price should NOT be 999.0.
    assert float(updated_blueberry.price) != 999.0, (
        "横浜 row not updated by commit; price still at seed sentinel"
    )


def test_replay_does_not_corrupt_other_port_rows(db, seed_user):
    """Stress test the "don't touch the wrong port" guarantee: after the
    upload, the 大阪 / 北海道 rows for the same SKUs MUST remain untouched
    (still at the seed sentinel price 888.0)."""
    if not REAL_TEMPLATE.exists():
        pytest.skip(f"real template not found at {REAL_TEMPLATE}")

    ids = _seed_masterdata_japan(db)
    _seed_replica_of_prod_collision(db, ids)

    file_bytes = REAL_TEMPLATE.read_bytes()

    batch = upload_service.parse_excel(
        db,
        file_bytes=file_bytes,
        filename="product_upload_template.xlsx",
        user_id=seed_user.id,
    )
    upload_service.resolve_and_score(db, batch_id=batch.id, user_id=seed_user.id)
    upload_service.commit_batch(db, batch_id=batch.id, user_id=seed_user.id)

    # All non-横浜 rows (the "wrong port" rows that the old buggy matcher
    # would have picked) must STILL be at sentinel price 888.0 — proving
    # the multi-key matcher did not touch them.
    wrong_port_rows = (
        db.query(Product)
        .filter(Product.port_id != ids["yokohama_id"])
        .filter(
            Product.code.in_(
                ["99PRD011390", "99PRD24469", "99PRD011388", "99PRD51974"]
            )
        )
        .all()
    )
    assert len(wrong_port_rows) == 4, f"setup error: expected 4 wrong-port rows"
    for row in wrong_port_rows:
        assert float(row.price) == 888.0, (
            f"row id={row.id} code={row.code} port={row.port_id} "
            f"was incorrectly mutated; price={row.price}, expected 888.0"
        )
