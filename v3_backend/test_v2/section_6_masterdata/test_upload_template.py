"""Section 6 — Masterdata upload, template download + agent tool.

测试目标：
    新的「产品上传模板」流：从静态 .xlsx 到 HTTP endpoint 到 agent 工具，
    每一层都按契约工作；最关键的是**自洽 + round-trip**：
      - **自洽**：当前 `_HEADER_ALIASES` 字典里识别的每个列名，必须出
        现在静态模板的第一行 header — 否则模板和 parser 就漂移了。
      - **Round-trip**：模板下载 → 不改一行 → 喂回 `parse_excel` →
        `commit_batch` → DB 里真的有 3 条 Product。

为什么重要：
    模板这一层很容易"看着对但实际错"。例如有人改了 `_HEADER_ALIASES`
    加了一列但忘了重新生成 .xlsx；或者 endpoint 路径错；或者 agent
    工具返回的 markdown 不含 link。一条 CI 测试比一周后的用户报错
    便宜 1000 倍。

设计方法：
    - 静态 file 验证：直接 open + assert headers + sample rows + 文本
      格式没丢前导零。
    - HTTP 验证：TestClient GET endpoint，断 mime type + filename + magic bytes。
    - Round-trip：把 endpoint 返回的 bytes 喂给 parse_excel 跑完整链路。
    - Agent 工具：dispatch + assert markdown 含 link + 必填列字眼。
"""

from __future__ import annotations

import io
from pathlib import Path

from openpyxl import load_workbook

from domains.masterdata.models import Product, ProductPricePeriod
from domains.masterdata.upload import commit_batch, parse_excel, resolve_and_score
from domains.masterdata.upload.models import StagingProduct
from test_v2.fixtures.helpers import login, seed_user

# Mirror the import in `apps.http.data_upload._TEMPLATE_PATH`.
_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "static"
    / "templates"
    / "product_upload_template.xlsx"
)


# ─── Static file sanity ──────────────────────────────────────


def test_template_file_exists_in_repo():
    """The xlsx must be checked into git — the endpoint serves it from disk."""
    assert _TEMPLATE_PATH.exists(), (
        f"Template missing at {_TEMPLATE_PATH}. "
        f"Run `python scripts/generate_product_upload_template.py`."
    )


def test_template_has_data_and_instructions_sheets():
    """Two-sheet layout: filling sheet + how-to. Pattern borrowed from
    SAP / Microsoft import templates so users can scroll data without
    a header block eating space."""
    wb = load_workbook(_TEMPLATE_PATH)
    assert "产品数据" in wb.sheetnames
    assert "使用说明" in wb.sheetnames


def test_template_headers_match_parser_canonical_field_names():
    """Self-consistency: every header on row 1 of the data sheet must be
    a canonical field name the `parse_excel` alias map recognises.
    Without this guard, someone could rename a header in the .xlsx and
    the parser would silently drop the column."""
    from domains.masterdata.upload.service import _HEADER_ALIASES

    wb = load_workbook(_TEMPLATE_PATH)
    ws = wb["产品数据"]
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    from scripts.generate_product_upload_template import ALL_COLUMNS

    assert headers == [col[0] for col in ALL_COLUMNS]
    assert "price" in headers and "contract_price" in headers
    assert {
        "purchase_price_effective_from", "purchase_price_effective_to",
        "selling_price_effective_from", "selling_price_effective_to",
    }.issubset(headers)
    canonical = set(_HEADER_ALIASES.keys())
    for h in headers:
        assert h in canonical, (
            f"Template header {h!r} is not in _HEADER_ALIASES "
            f"({sorted(canonical)}); parser will silently ignore it"
        )


def test_template_product_code_cell_is_text_formatted():
    """The leading-zero trap: Excel will quietly auto-coerce "00100"
    into 100.0 unless the column has number_format `@` (text). We test
    a row that USES a leading-zero code to catch this at CI.

    Column order (post 2026-05-27 strict-contract refactor):
      A=product_name, B=country, C=port, D=product_code, E=...
    Row 3 of the EXAMPLE_ROWS is the Yogurt row with code "00100".
    """
    wb = load_workbook(_TEMPLATE_PATH)
    ws = wb["产品数据"]
    code_cell = ws["D3"]
    assert code_cell.value == "00100", (
        f"Example leading-zero code lost — got {code_cell.value!r}; "
        f"the template no longer guards against Excel's number coercion."
    )
    assert code_cell.number_format == "@"


# ─── HTTP endpoint ───────────────────────────────────────────


def test_template_endpoint_is_public_no_auth_required(client):
    """No bearer → 200. The chat UI renders the agent's reply as
    markdown; when the user clicks `[📥 下载模板](url)` the browser
    issues a plain GET with no Authorization header. Requiring auth
    here would 401 every click — exactly the prod regression of
    2026-05-14 ("Not authenticated" on click). Body has no user data,
    so public is the right contract (Shopify / Stripe do the same)."""
    r = client.get("/api/data-upload/template")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"  # xlsx magic — actually returned, not 401


def test_template_endpoint_returns_xlsx_with_correct_mime(client, db):
    seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    r = client.get("/api/data-upload/template", headers=headers)
    assert r.status_code == 200, r.text
    ctype = r.headers["content-type"]
    assert "spreadsheetml" in ctype, f"got content-type={ctype!r}"
    # Filename surfaced via Content-Disposition so browsers default the
    # save dialog correctly.
    cd = r.headers.get("content-disposition", "")
    assert "product_upload_template.xlsx" in cd, (
        f"missing filename in Content-Disposition: {cd!r}"
    )
    # xlsx is a zip — magic bytes start with PK.
    assert r.content[:2] == b"PK"


def test_template_endpoint_returns_same_bytes_each_call(client, db):
    """Idempotent download — two GETs return byte-identical files. Without
    this guarantee an in-flight download race could hand the user different
    contents from what the docs / agent message describe."""
    seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    r1 = client.get("/api/data-upload/template", headers=headers)
    r2 = client.get("/api/data-upload/template", headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.content == r2.content


# ─── Round-trip: template bytes → DB ──────────────────────────


def test_template_round_trip_through_parse_excel(client, db):
    """Critical end-to-end: download template → parse_excel → resolve →
    commit → DB has the example products. Any layer that drops headers
    or silently shifts columns lights up here.

    Seeds the FK master tables first so the template's example rows
    (which reference real-looking country/category/supplier/port names)
    resolve cleanly — mirrors how the system looks in any real
    deployment."""
    _seed_masterdata_for_examples(db)
    seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    r = client.get("/api/data-upload/template", headers=headers)
    assert r.status_code == 200
    blob = r.content

    batch = parse_excel(db, file_bytes=blob, filename="tpl.xlsx", user_id=1)
    assert batch.status == "ready"
    assert batch.total_rows == 3  # three example rows

    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    # No products in DB yet → all three should land as "new".
    assert resolved.new_rows == 3
    assert resolved.error_rows == 0, (
        "FK lookups failed: rows ended in error. "
        "Are USA/Japan/FRUIT/DAIRY/Sunkist/Morinaga seeded?"
    )

    result = commit_batch(db, batch_id=batch.id, user_id=1)
    assert result["created"] == 3
    products = (
        db.query(Product)
        .filter(Product.product_name_en.in_([
            "Apple - Red Delicious 125ct",
            "Yogurt - Strawberry 75G",
            "Beef Tenderloin Grade A",
        ]))
        .all()
    )
    assert len(products) == 3

    # Verify all 21 fields landed for the rich row.
    apple = next(p for p in products if p.product_name_en.startswith("Apple"))
    assert apple.product_name_jp == "赤りんご デリシャス"
    assert apple.code == "FRT-APL-RED"
    assert apple.brand == "Sunkist"
    assert apple.category_id is not None       # FK by name resolved
    assert apple.supplier_id is not None
    assert apple.country_id is not None
    assert apple.port_id is not None
    assert float(apple.price) == 850.00
    assert float(apple.contract_price) == 1050.00
    assert apple.purchase_price_effective_from is not None
    assert apple.purchase_price_effective_to is not None
    assert apple.selling_price_effective_from is not None
    assert apple.selling_price_effective_to is not None
    periods = (
        db.query(ProductPricePeriod)
        .filter(ProductPricePeriod.product_id == apple.id)
        .order_by(ProductPricePeriod.price_type)
        .all()
    )
    assert [period.price_type for period in periods] == ["purchase", "selling"]
    assert [float(period.amount) for period in periods] == [850.0, 1050.0]
    assert apple.currency == "USD"
    assert apple.unit == "CT"
    assert apple.unit_size == "40LB/CT"
    assert apple.pack_size == "125CT/CTN"
    assert apple.country_of_origin == "Washington, USA"
    assert apple.effective_from is not None
    assert apple.effective_to is not None


def _seed_masterdata_for_examples(db):
    """Insert the FK targets the template's example rows reference."""
    from domains.masterdata.models import Category, Country, Port, Supplier

    for name in ("USA", "Japan"):
        db.add(Country(name=name))
    for name in ("FRUIT", "DAIRY"):
        db.add(Category(name=name))
    for name in ("Sunkist Growers Inc.", "Morinaga Milk Industry Co., Ltd."):
        db.add(Supplier(name=name))
    db.add(Port(name="Yokohama"))
    db.commit()


# ─── New 16-column pipeline coverage ─────────────────────────


def test_resolve_fk_lookup_miss_flags_row_as_error(db):
    """FK names that don't exist in masterdata → row marked error with
    a helpful message naming the offending field + value. This is the
    user-visible signal "type the supplier name correctly"."""
    from test_v2.fixtures.helpers import make_excel

    blob = make_excel(
        [
            {
                "product_name": "Mystery Item",
                "supplier": "Nonexistent Co",  # not in DB
                "country": "Atlantis",  # not in DB
            }
        ]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 1

    from domains.masterdata.upload.models import StagingProduct

    sp = db.query(StagingProduct).filter(StagingProduct.batch_id == batch.id).one()
    assert sp.match_status == "error"
    assert sp.validation_errors is not None
    msgs = " ".join(sp.validation_errors)
    assert "supplier 'Nonexistent Co' not found" in msgs
    assert "country 'Atlantis' not found" in msgs


def test_resolve_fk_lookup_hit_does_not_flag_error(db):
    """Sanity inverse: when FK names are real, no error. Pin the
    case-insensitive + trimmed match too."""
    from domains.masterdata.models import Supplier
    from test_v2.fixtures.helpers import make_excel

    db.add(Supplier(name="Real Supplier"))
    db.commit()

    blob = make_excel(
        [
            # Mixed case + trailing whitespace — must still match.
            {"product_name": "Item", "supplier": "  REAL supplier "}
        ]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 0
    assert resolved.new_rows == 1


def test_commit_writes_fk_id_from_name(db):
    """End-to-end FK: Excel has supplier name → commit puts supplier_id
    on the Product. Pre-fix this column was silently dropped (supplier_code
    was in the alias map but never reached Product). Now name → id should
    actually land."""
    from domains.masterdata.models import Supplier
    from test_v2.fixtures.helpers import make_excel

    sup = Supplier(name="Acme Corp")
    db.add(sup)
    db.commit()
    db.refresh(sup)

    blob = make_excel([{"product_name": "Acme Widget", "supplier": "Acme Corp"}])
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolve_and_score(db, batch_id=batch.id, user_id=1)
    commit_batch(db, batch_id=batch.id, user_id=1)

    p = db.query(Product).filter(Product.product_name_en == "Acme Widget").one()
    assert p.supplier_id == sup.id, "FK lookup didn't make it to the Product row"


def test_commit_writes_all_extended_fields(db):
    """One row exercises every column the new template ships. Pin this
    end-to-end — a regression where one column gets dropped between
    parse → commit will fail here."""
    from datetime import datetime

    from domains.masterdata.models import Category, Country, Port, Supplier
    from test_v2.fixtures.helpers import make_excel

    db.add(Country(name="JP"))
    db.add(Category(name="FOOD"))
    db.add(Supplier(name="Sup1"))
    db.add(Port(name="HND"))
    db.commit()

    blob = make_excel(
        [
            {
                "product_name": "Rich Row",
                "product_code": "RICH-1",
                "product_name_jp": "豊富な商品",
                "brand": "BrandX",
                "category": "FOOD",
                "supplier": "Sup1",
                "country": "JP",
                "port": "HND",
                "price": 99.99,
                "currency": "JPY",
                "unit": "EA",
                "unit_size": "100G",
                "pack_size": "12x100G",
                "country_of_origin": "Tokyo, Japan",
                "effective_from": "2026-03-01",
                "effective_to": "2026-09-30",
            }
        ]
    )
    batch = parse_excel(db, file_bytes=blob, filename="rich.xlsx", user_id=1)
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 0
    commit_batch(db, batch_id=batch.id, user_id=1)

    p = db.query(Product).filter(Product.product_name_en == "Rich Row").one()
    assert p.code == "RICH-1"
    assert p.product_name_jp == "豊富な商品"
    assert p.brand == "BrandX"
    assert p.category_id is not None
    assert p.supplier_id is not None
    assert p.country_id is not None
    assert p.port_id is not None
    assert float(p.price) == 99.99
    assert p.currency == "JPY"
    assert p.unit == "EA"
    assert p.unit_size == "100G"
    assert p.pack_size == "12x100G"
    assert p.country_of_origin == "Tokyo, Japan"
    assert p.effective_from == datetime(2026, 3, 1)
    assert p.effective_to == datetime(2026, 9, 30)


def test_resolve_invalid_date_flags_row_as_error(db):
    """An effective_from like "1995/13/45" is not a real date — must
    surface as a validation error rather than silently dropping the
    date field."""
    from test_v2.fixtures.helpers import make_excel

    blob = make_excel(
        [{"product_name": "Bad Date Item", "effective_from": "1995/13/45"}]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 1

    from domains.masterdata.upload.models import StagingProduct

    sp = db.query(StagingProduct).filter(StagingProduct.batch_id == batch.id).one()
    assert "effective_from '1995/13/45' is not a valid date" in " ".join(
        sp.validation_errors or []
    )


def test_resolve_accepts_iso_with_T_separator(db):
    """openpyxl returns date-typed cells as `datetime` objects, which
    parse_excel JSON-serialises into ISO `2026-01-01T00:00:00` form.
    The resolver must still accept those — the date format support is
    what closes the parser/Excel-cell-type loop."""
    from test_v2.fixtures.helpers import make_excel

    blob = make_excel(
        [{"product_name": "ISO Date Item", "effective_from": "2026-01-01T00:00:00"}]
    )
    batch = parse_excel(db, file_bytes=blob, filename="t.xlsx", user_id=1)
    resolved = resolve_and_score(db, batch_id=batch.id, user_id=1)
    assert resolved.error_rows == 0


def test_template_data_sheet_has_21_columns(db):
    """Pin the column count so a future "drop a column" change is loud."""
    wb = load_workbook(_TEMPLATE_PATH)
    ws = wb["产品数据"]
    headers = [
        ws.cell(row=1, column=c).value
        for c in range(1, 24)
        if ws.cell(row=1, column=c).value
    ]
    assert len(headers) == 21, (
        f"expected 21 columns, found {len(headers)}: {headers}"
    )


def test_template_round_trip_preserves_leading_zero_code(client, db):
    """Specific regression: the "00100" example must survive the entire
    pipeline as a 5-char string, not get squashed into 100. This is the
    one user-visible bug the text format on the code column exists to
    prevent."""
    _seed_masterdata_for_examples(db)
    seed_user(db, email="emp@example.com")
    headers = login(client, "emp@example.com")
    r = client.get("/api/data-upload/template", headers=headers)
    blob = r.content

    batch = parse_excel(db, file_bytes=blob, filename="tpl.xlsx", user_id=1)
    staged = (
        db.query(StagingProduct)
        .filter(StagingProduct.batch_id == batch.id)
        .all()
    )
    codes = {s.product_code for s in staged}
    assert "00100" in codes, f"leading-zero code lost — staged codes: {codes}"


# ─── Agent tool ──────────────────────────────────────────────


def test_get_upload_template_tool_returns_markdown_with_link(db):
    """The agent calls this tool at Step 0 and pastes the body. Body
    must contain a markdown link to the endpoint and mention which
    column is required — these two carry the entire user value."""
    from pathlib import Path

    from agent.runtime import tools as _v3_tools  # noqa: F401 — register tools
    from agent.runtime.deps import V3Deps, inject_deps
    from general_agent import REGISTRY, ToolContext

    ctx = ToolContext(workspace=Path("/tmp"), extras={})
    inject_deps(ctx, V3Deps(db=db, user_id=1))

    out = REGISTRY.view(["get_upload_template"]).dispatch(
        "get_upload_template", {}, ctx=ctx
    )
    assert not out.startswith("Error:"), out
    # Markdown link to the endpoint — agents/UIs render this as clickable.
    assert "/api/data-upload/template" in out
    assert "📥" in out or "下载" in out
    # Required column highlighted.
    assert "product_name" in out
    assert "必填" in out
    # Table reference present (markdown table for the 5 columns).
    assert "| `product_code` |" in out
    assert "| `price` |" in out
    assert "| `contract_price` |" in out
    assert "21 列" in out
    assert "只有 `product_name`" not in out


def test_downloaded_template_matches_generated_values_and_formats(client):
    from scripts.generate_product_upload_template import build_template

    response = client.get("/api/data-upload/template")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    actual = load_workbook(io.BytesIO(response.content))
    generated = io.BytesIO()
    build_template().save(generated)
    expected = load_workbook(io.BytesIO(generated.getvalue()))
    for sheet in expected:
        downloaded = actual[sheet.title]
        assert list(downloaded.values) == list(sheet.values)
    data = actual["产品数据"]
    assert data["L2"].value == 1050
    assert data["L2"].number_format == "#,##0.00"
    for cell in ("J2", "K2", "M2", "N2", "T2", "U2"):
        assert data[cell].number_format == "yyyy-mm-dd"
    assert data["D3"].value == "00100"
    text = " ".join(str(c.value) for row in actual["使用说明"] for c in row if c.value)
    assert "模糊匹配" not in text
    assert "product_name、country、port" in text
