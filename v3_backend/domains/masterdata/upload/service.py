"""Upload pipeline service layer.

Five public stages (called in order, but each idempotent):

1. `parse_excel(file_bytes, filename, user_id)`
   → opens the workbook, normalizes column headers, writes a
     `UploadBatch` (status="parsing"→"ready") + N `StagingProduct` rows.

2. `resolve_and_score(batch_id, user_id)`
   → for each staging row, compares to live `products`:
     - exact `code` hit (full key or unique single-port) → "exact", 1.0
     - exact name hit (case-insensitive, country+port scoped or unique) → "name_exact", 0.95
     - else                     → "new",    None
   Updates batch counters.

   Note (2026-05-25): fuzzy similarity matching was removed — it caused
   real false positives in prod (e.g. "lettuce mizuna" matched "lettuce
   mesclun mix" at 0.667). Industry-standard master-data systems (SAP,
   Coupa, NetSuite) use code/name explicit keys only; ambiguous rows
   become `new` and the user decides.

3. `preview_changes(batch_id, user_id)`
   → returns a grouped diff: `{create: [...], update: [...], skip: [...]}`
     truncated to top 50 per group with `truncated` flag.

4. `commit_batch(batch_id, user_id)`
   → walks staging rows, applies create/update via savepoints, writes
     a `ProductChangeLog` per field change. One bad row doesn't roll
     back the whole batch — only that row.

5. `cancel_batch(batch_id, user_id)`
   → user declined the resolved batch before committing. Marks batch
     `cancelled` so it doesn't sit forever at `resolved`. Distinct from
     `rollback_batch` which is for *committed* batches.

6. `rollback_batch(batch_id, user_id)`
   → reverses an already-committed batch using the changelog: deletes
     created rows, restores updated fields. Idempotent.

Cross-user safety: every function takes a `user_id` and checks the
batch's owner before doing anything. Wrong owner → `BatchOwnedByOther`.

ADR-0006 RULE-2: this module is whitelisted (it's the only legitimate
bulk-write path into `products`, paralleling `agent/storage/`).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domains.masterdata import price_history
from domains.masterdata._money import parse_product_price
from domains.masterdata.errors import Conflict
from domains.masterdata.models import Product
from domains.masterdata.price_periods import sync_compatibility_periods
from domains.masterdata.upload.errors import (
    BatchInWrongState,
    BatchNotFound,
    BatchOwnedByOther,
    BatchValidationFailed,
    ParseError,
    UploadError,
)
from domains.masterdata.upload.models import (
    ProductChangeLog,
    StagingProduct,
    UploadBatch,
)

logger = logging.getLogger(__name__)

_FUZZY_MIN_SIMILARITY = 0.6
_PREVIEW_PER_GROUP = 50


# ─── Header normalization ────────────────────────────────────


_HEADER_ALIASES = {
    # Core identity
    "product_code": ("code", "product_code", "sku", "item_code", "item code", "コード", "品番", "商品代码"),
    "product_name": (
        "name", "product_name", "product_name_en", "english name",
        "英文品名", "品名", "商品名",
        "item", "item name", "description",  # description kept as legacy alias
    ),
    "product_name_jp": (
        "product_name_jp", "name_jp", "jp_name", "japanese_name", "日文品名", "日本語", "日本語の商品名"
    ),
    # Brand + simple text fields
    "brand": ("brand", "品牌", "ブランド"),
    "country_of_origin": (
        "country_of_origin", "origin", "原产地", "原産地", "country of origin"
    ),
    # FK lookups by NAME (resolver hits master tables)
    "category": ("category", "category_name", "类别", "カテゴリ"),
    "supplier": (
        "supplier", "supplier_name", "vendor", "vendor_name", "供应商", "サプライヤー"
    ),
    "country": ("country", "country_name", "国家", "国"),
    "port": ("port", "port_name", "港口", "港"),
    # Price + currency
    "price": ("price", "unit_price", "amount", "単価", "价格"),
    # Contract-bound selling price — Felix 2026-06-06 (R2 / migration 0014).
    # UI label was renamed to 「卖价」 on 2026-06-22 because "合同价"
    # confused users. DB column + canonical key stay `contract_price`;
    # all old aliases kept for backward-compat with older spreadsheets.
    "contract_price": (
        "contract_price", "contracted_price", "contract price",
        "卖价", "selling_price", "selling price",
        "合同卖价", "合同价", "合約価格", "契約価格",
    ),
    "purchase_price_effective_from": (
        "purchase_price_effective_from", "purchase_price_from",
        "采购价有效开始日期", "采购价开始日期",
    ),
    "purchase_price_effective_to": (
        "purchase_price_effective_to", "purchase_price_to",
        "采购价有效结束日期", "采购价结束日期",
    ),
    "selling_price_effective_from": (
        "selling_price_effective_from", "selling_price_from",
        "卖价有效开始日期", "卖价开始日期",
    ),
    "selling_price_effective_to": (
        "selling_price_effective_to", "selling_price_to",
        "卖价有效结束日期", "卖价结束日期",
    ),
    "currency": ("currency", "ccy", "币种", "貨幣", "货币", "通貨"),
    # Unit / packaging
    "unit": ("unit", "uom", "単位", "单位"),
    "unit_size": ("unit_size", "单位规格", "単位サイズ"),
    "pack_size": ("pack_size", "pack", "spec", "規格", "包装规格", "size"),
    # Effective period (dates)
    "effective_from": (
        "effective_from", "valid_from", "start_date", "生效起", "生效起始日"
    ),
    "effective_to": ("effective_to", "valid_to", "end_date", "生效止", "生效结束日"),
    # Legacy backward-compat: supplier_code still gets parsed (a few old
    # spreadsheets shipped this column) but resolves to nothing — the new
    # FK lookup uses the `supplier` (name) column instead. We retain the
    # alias only so old uploads don't reject the column.
    "supplier_code": ("supplier_code", "vendor_code"),
}

_DATE_FIELDS = (
    "effective_from",
    "effective_to",
    "purchase_price_effective_from",
    "purchase_price_effective_to",
    "selling_price_effective_from",
    "selling_price_effective_to",
)
_PRICE_PERIODS = (
    ("purchase_price_effective_from", "purchase_price_effective_to", "采购价"),
    ("selling_price_effective_from", "selling_price_effective_to", "卖价"),
)


def _normalize_header(raw: str) -> str | None:
    """Map a raw column header to one of our normalized field names."""
    if raw is None:
        return None
    cleaned = str(raw).strip().lower()
    for canonical, aliases in _HEADER_ALIASES.items():
        if cleaned == canonical or cleaned in aliases:
            return canonical
    return None


def _parse_price(value: Any) -> float | None:
    parsed = _decimal_or_none(value)
    return float(parsed) if parsed is not None else None


def _parse_date(value: Any) -> datetime | None:
    """Accept a date cell value as Excel datetime OR ISO `YYYY-MM-DD` string.

    Returns `None` for blank cells. Returns the sentinel `_DATE_PARSE_ERROR`
    (a separate marker we test for) when the value is present but cannot
    be parsed — callers convert that into a row-level validation error so
    the user sees "1995/13/x is not a valid date" rather than the row
    silently dropping its effective dates.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    # Most users will type the ISO form 2026-05-30; we also accept Excel-
    # exported variants 2026/05/30 and 2026.05.30 because Japanese-locale
    # Excel saves dates this way by default.
    # Order matters — try the most specific (with time) before bare date,
    # so we don't truncate timezone info.
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",      # ISO 8601 with T separator (what openpyxl emits)
        "%Y-%m-%dT%H:%M:%S.%f",   # ISO with fractional seconds
        "%Y-%m-%d %H:%M:%S",      # ISO with space
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return _DATE_PARSE_ERROR


def _json_safe(value: Any) -> Any:
    """Coerce an Excel cell value into something the JSON column will accept.

    openpyxl returns `datetime` objects for date-typed cells, which the
    SQLAlchemy JSON encoder can't handle. We serialise those to ISO
    strings up front so `raw_data` stays loadable.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


class _DateParseError:
    """Sentinel: parsing was attempted and failed (distinct from "blank")."""

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return "<_DateParseError>"


_DATE_PARSE_ERROR = _DateParseError()


def _canonical_fields(sp: Any) -> dict[str, Any]:
    """Re-derive `{canonical_key: cell_value}` from a StagingProduct's raw_data.

    We don't persist the canonical map separately — `raw_data` already
    keeps the original Excel headers verbatim, and the canonical key per
    column changes only when `_HEADER_ALIASES` does. So we recompute the
    mapping at commit time, which costs ~6 dict lookups per row and lets
    us add new aliases (or fix mis-named columns) without a DB migration.
    """
    out: dict[str, Any] = {}
    for header, value in (sp.raw_data or {}).items():
        canonical = _normalize_header(header)
        if canonical is None:
            continue
        out[canonical] = value
    return out


class _FKResolver:
    """Cache name→id maps for the four FK lookups used by the uploader.

    Built once per `resolve_and_score` call (and again per `commit_batch`
    call) so we don't fire 4 SELECTs per staging row. Name comparison is
    case-insensitive + trimmed — users routinely paste "Sunkist Growers "
    with a trailing space.
    """

    def __init__(self, db: Session) -> None:
        from domains.masterdata.models import Category, Country, Port, Supplier

        def _by_name(model_cls: Any) -> dict[str, int]:
            return {
                (row.name or "").strip().lower(): row.id
                for row in db.execute(select(model_cls)).scalars()
                if row.name
            }

        self._maps: dict[str, dict[str, int]] = {
            "category": _by_name(Category),
            "supplier": _by_name(Supplier),
            "country": _by_name(Country),
            "port": _by_name(Port),
        }

    def resolve(self, kind: str, name: Any) -> int | None:
        """`name` may be a string (looked up) or already an int (returned as-is)."""
        if name is None or name == "":
            return None
        if isinstance(name, int):
            return name
        key = str(name).strip().lower()
        if not key:
            return None
        return self._maps.get(kind, {}).get(key)


# ─── Stage 1: parse ──────────────────────────────────────────


def parse_excel(
    db: Session,
    *,
    file_bytes: bytes,
    filename: str,
    user_id: int,
    file_url: str | None = None,
    file_sha256: str | None = None,
    entity_type: str = "products",
    strict_headers: bool = True,
    workflow_version: int = 1,
) -> UploadBatch:
    """Parse the workbook into a batch + staging rows.

    Reads the first worksheet only; first non-empty row is treated as
    the header. Empty rows are skipped (not staged, not counted).
    """
    if entity_type != "products":
        raise ParseError(f"entity_type={entity_type} not supported yet")

    try:
        wb = load_workbook(BytesIO(file_bytes), data_only=False, read_only=True)
    except Exception as exc:
        raise ParseError("无法读取 Excel 工作簿；请上传 .xlsx 文件，CSV/PDF/旧版 .xls 不适用于产品导入") from exc

    ws = wb.worksheets[0] if wb.worksheets else None
    if ws is None:
        raise ParseError("workbook has no worksheets")

    rows_iter = enumerate(ws.iter_rows(values_only=True), start=1)
    header_row: tuple[Any, ...] | None = None
    header_row_number: int | None = None
    for sheet_row_number, r in rows_iter:
        if r and any(c is not None and str(c).strip() for c in r):
            header_row = r
            header_row_number = sheet_row_number
            break
    if header_row is None:
        raise ParseError("workbook has no header row")

    column_map: dict[int, str] = {}
    canonical_columns: dict[str, list[int]] = {}
    header_columns: list[dict[str, Any]] = []
    for idx, raw in enumerate(header_row):
        canonical = _normalize_header(raw)
        if canonical is not None:
            column_map[idx] = canonical
            canonical_columns.setdefault(canonical, []).append(idx + 1)
        header_columns.append(
            {
                "column": idx + 1,
                "raw": "" if raw is None else str(raw).strip(),
                "canonical": canonical,
                "status": "recognized" if canonical is not None else "unrecognized",
            }
        )

    unrecognized = [c for c in header_columns if c["raw"] and c["canonical"] is None]
    duplicates = [
        {"canonical": canonical, "columns": columns}
        for canonical, columns in canonical_columns.items()
        if len(columns) > 1
    ]
    required_fields = ("product_name", "country", "port")
    missing_required = [field for field in required_fields if field not in canonical_columns]
    blocking_issues: list[dict[str, Any]] = []
    blocking_issues.extend(
        {
            "code": "unknown_header",
            "field": None,
            "columns": [item["column"]],
            "message": f"第 {item['column']} 列表头“{item['raw']}”无法识别",
        }
        for item in unrecognized
    )
    blocking_issues.extend(
        {
            "code": "duplicate_header",
            "field": item["canonical"],
            "columns": item["columns"],
            "message": f"多列表头同时映射到 {item['canonical']}，无法确定使用哪一列",
        }
        for item in duplicates
    )
    blocking_issues.extend(
        {
            "code": "missing_required_header",
            "field": field,
            "columns": [],
            "message": f"缺少必需表头 {field}",
        }
        for field in missing_required
    )
    header_diagnostics = {
        "columns": header_columns,
        "unrecognized": unrecognized,
        "duplicate_canonical": duplicates,
        "missing_required": missing_required,
        "blocking_issues": blocking_issues,
    }

    if strict_headers and "product_name" not in column_map.values():
        raise ParseError(
            "no `product_name` column found (aliases: name, description, item, 品名)"
        )

    batch = UploadBatch(
        user_id=user_id,
        filename=filename,
        file_url=file_url,
        file_sha256=file_sha256,
        entity_type=entity_type,
        sheet_name=ws.title,
        header_row_number=header_row_number,
        header_diagnostics=header_diagnostics,
        workflow_version=workflow_version,
        status="parsing",
    )
    db.add(batch)
    db.flush()  # assign id

    parsed = 0
    for row_idx, (source_row_number, row) in enumerate(rows_iter, start=1):
        if not row or all(c is None or str(c).strip() == "" for c in row):
            continue
        # openpyxl returns datetimes as `datetime` objects for date-formatted
        # cells; JSON can't serialise those into `raw_data` (a JSON column).
        # Coerce to ISO strings here so downstream (`_canonical_fields` +
        # `_parse_date`) gets a stable text form.
        raw_data = {
            str(header_row[i] or f"col_{i}"): _json_safe(row[i])
            for i in range(len(row))
        }
        normalized = {column_map[i]: row[i] for i in range(len(row)) if i in column_map}
        sp = StagingProduct(
            batch_id=batch.id,
            row_index=row_idx,
            source_row_number=source_row_number,
            raw_data=raw_data,
            product_code=_str_or_none(normalized.get("product_code")),
            product_name=_str_or_none(normalized.get("product_name")),
            supplier_code=_str_or_none(normalized.get("supplier_code")),
            price=_parse_price(normalized.get("price")),
            unit=_str_or_none(normalized.get("unit")),
            pack_size=_str_or_none(normalized.get("pack_size")),
            description=_str_or_none(normalized.get("description")),
        )
        db.add(sp)
        parsed += 1

    batch.total_rows = parsed
    batch.parsed_rows = parsed
    if parsed == 0:
        diagnostics = dict(batch.header_diagnostics or {})
        issues = list(diagnostics.get("blocking_issues") or [])
        issues.append(
            {
                "code": "empty_data",
                "field": None,
                "columns": [],
                "message": "工作表没有可导入的数据行",
            }
        )
        diagnostics["blocking_issues"] = issues
        batch.header_diagnostics = diagnostics
    batch.status = "ready"
    batch.parsed_at = datetime.utcnow()
    db.commit()
    db.refresh(batch)
    return batch


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _decimal_or_none(v: Any) -> Decimal | None:
    """Tolerant staging conversion; _price_errors reports invalid nonblank input."""
    try:
        return parse_product_price(v)
    except ValueError:
        return None


def _price_errors(sp: StagingProduct) -> list[str]:
    errors = []
    raw = _canonical_fields(sp)
    for field in ("price", "contract_price"):
        try:
            parse_product_price(raw.get(field))
        except ValueError as exc:
            errors.append(f"{field}: {exc}")
    return errors


# ─── Stage 2: resolve ────────────────────────────────────────


def resolve_and_score(db: Session, *, batch_id: int, user_id: int) -> UploadBatch:
    """Score each staging row against live products."""
    batch = _load_owned_batch(db, batch_id, user_id)
    db.refresh(batch, with_for_update=True)
    if batch.status not in ("ready", "resolved"):
        raise BatchInWrongState(
            f"resolve requires status=ready/resolved, got {batch.status}"
        )

    rows = list(
        db.execute(
            select(StagingProduct).where(StagingProduct.batch_id == batch_id)
        ).scalars()
    )
    products = list(db.execute(select(Product)).scalars())

    # Strict identity contract (2026-05-27): a product's identity is
    # exactly `(country_id, product_name_en, port_id)` — the unique
    # constraint `uix_country_product_name_port`. The matcher honors
    # that and nothing else.
    #
    # Two rules, no fallback:
    #   Rule 1: `(code, country_id, port_id)` triple → "exact"
    #   Rule 2: `(country_id, name, port_id)` natural key → "name_exact"
    #   Else: "new"
    #
    # Excel rows MUST supply both country + port (validated below). The
    # old code-only / name-only fallbacks silently misrouted in multi-
    # port settings — deleted, replaced by row-level errors that tell
    # the user exactly which column is missing.
    by_full_key: dict[tuple[str, int, int], Any] = {}
    by_natural_key: dict[tuple[int, str, int], Any] = {}
    for p in products:
        if p.country_id and p.port_id:
            if p.code:
                by_full_key[(p.code.strip().lower(), p.country_id, p.port_id)] = p
            if p.product_name_en:
                by_natural_key[
                    (p.country_id, p.product_name_en.strip().lower(), p.port_id)
                ] = p

    # Build FK + date validators once per batch so we don't N+1 the DB.
    fk_resolver = _FKResolver(db)

    counters = {"exact": 0, "fuzzy": 0, "new": 0, "error": 0}
    for sp in rows:
        sp.expected_revision = None
        sp.validation_errors = None
        errors: list[str] = _price_errors(sp)
        if not sp.product_name:
            errors.append("missing product_name")

        # Validate FK lookups + dates from the canonical view of raw_data.
        # We want the user to see ALL the row's problems at once, not just
        # the first one — re-resolve every row even if we already flagged
        # an error above. Saves them a second upload to fix issue #2.
        canonical = _canonical_fields(sp)
        for fk_kind in ("category", "supplier", "country", "port"):
            name = canonical.get(fk_kind)
            if name in (None, "") or isinstance(name, int):
                continue
            if fk_resolver.resolve(fk_kind, name) is None:
                errors.append(
                    f"{fk_kind} '{name}' not found in masterdata "
                    f"(check spelling, or add it via the Data tab first)"
                )
        for date_field in _DATE_FIELDS:
            raw_val = canonical.get(date_field)
            if raw_val in (None, ""):
                continue
            if _parse_date(raw_val) is _DATE_PARSE_ERROR:
                errors.append(
                    f"{date_field} '{raw_val}' is not a valid date "
                    f"(use YYYY-MM-DD, e.g. 2026-05-30)"
                )
        errors.extend(_price_period_errors(canonical))

        if errors:
            sp.match_status = "error"
            sp.match_target_id = None
            sp.confidence = None
            sp.validation_errors = errors
            counters["error"] += 1
            continue

        excel_country_id = fk_resolver.resolve("country", canonical.get("country"))
        excel_port_id = fk_resolver.resolve("port", canonical.get("port"))

        # Required: country + port. Without them a product code or name
        # cannot uniquely identify a row in a multi-port DB. Surface a
        # row-level error pointing the user at the missing column — loud
        # failure beats the silent misroute the old code-only / name-only
        # fallbacks used to produce.
        location_errors: list[str] = []
        if excel_country_id is None:
            location_errors.append(
                "country is required. Add a `country` column to the Excel "
                "row with the country's name (e.g. 'Japan'). Same product "
                "in different countries = different records."
            )
        if excel_port_id is None:
            location_errors.append(
                "port is required. Add a `port` column to the Excel row "
                "with the port's name (e.g. 'Tokyo'). Same product at "
                "different ports = different records."
            )
        if location_errors:
            sp.match_status = "error"
            sp.match_target_id = None
            sp.confidence = None
            sp.validation_errors = location_errors
            counters["error"] += 1
            continue

        code_key = (sp.product_code or "").strip().lower()
        name_key = (sp.product_name or "").strip().lower()

        # Rule 1: full composite match — (code, country, port).
        if code_key and (code_key, excel_country_id, excel_port_id) in by_full_key:
            target = by_full_key[(code_key, excel_country_id, excel_port_id)]
            period_errors = _price_period_errors(canonical, target)
            if period_errors:
                sp.match_status = "error"
                sp.match_target_id = None
                sp.confidence = None
                sp.validation_errors = period_errors
                counters["error"] += 1
                continue
            sp.match_status = "exact"
            sp.match_target_id = target.id
            sp.expected_revision = target.revision
            sp.confidence = 1.0
            counters["exact"] += 1
            continue

        # Rule 2: natural key match — (country, name, port).
        if name_key and (excel_country_id, name_key, excel_port_id) in by_natural_key:
            target = by_natural_key[(excel_country_id, name_key, excel_port_id)]
            period_errors = _price_period_errors(canonical, target)
            if period_errors:
                sp.match_status = "error"
                sp.match_target_id = None
                sp.confidence = None
                sp.validation_errors = period_errors
                counters["error"] += 1
                continue
            sp.match_status = "name_exact"
            sp.match_target_id = target.id
            sp.expected_revision = target.revision
            sp.confidence = 0.95
            counters["exact"] += 1
            continue

        # No match → INSERT a new product at this (country, name, port).
        sp.match_status = "new"
        sp.match_target_id = None
        sp.confidence = None
        counters["new"] += 1
        sp.validation_errors = None

    identities: dict[tuple, list] = {}
    for sp in rows:
        if sp.match_status == "error":
            continue
        fields = _canonical_fields(sp)
        key = ("existing", sp.match_target_id) if sp.match_target_id else (
            "new", fk_resolver.resolve("country", fields.get("country")),
            (sp.product_name or "").strip().casefold(), fk_resolver.resolve("port", fields.get("port")))
        identities.setdefault(key, []).append(sp)
    for group in identities.values():
        if len(group) > 1:
            # Existing products may legitimately occupy several rows when each
            # row adds a different complete price interval. Product identity
            # stays one row; only v3_product_price_periods gains records.
            signatures = [_price_period_signature(_canonical_fields(sp)) for sp in group]
            if all(signature for signature in signatures) and len(set(signatures)) == len(group):
                continue
            for sp in group:
                counters["new" if sp.match_status == "new" else "exact"] -= 1
                counters["error"] += 1
                sp.match_status = "error"
                sp.validation_errors = ["同一批次重复出现同一个产品，请合并为一行后重新上传"]
    batch.matched_exact = counters["exact"]
    batch.matched_fuzzy = counters["fuzzy"]
    batch.new_rows = counters["new"]
    batch.error_rows = counters["error"]
    batch.status = "resolved"
    db.commit()
    db.refresh(batch)
    return batch


def _price_period_signature(fields: dict[str, Any]) -> tuple[Any, ...] | None:
    parts: list[Any] = []
    for start_field, end_field, label in _PRICE_PERIODS:
        start = _parse_date_safe(fields.get(start_field))
        end = _parse_date_safe(fields.get(end_field))
        amount_field = "price" if label == "采购价" else "contract_price"
        amount = fields.get(amount_field)
        if amount not in (None, "") and start is not None and end is not None:
            parts.extend((label, start.date(), end.date(), str(amount)))
    return tuple(parts) or None


# ─── Stage 3: preview ────────────────────────────────────────


def preview_changes(
    db: Session, *, batch_id: int, user_id: int, limit: int = _PREVIEW_PER_GROUP
) -> dict[str, Any]:
    """Return a grouped diff. Each group is capped at `limit` rows.

    Output:
      {
        "summary": {"create": N, "update": M, "skip": K, "error": E},
        "create": [{row_index, product_name, code, ...}, ...],
        "update": [{row_index, product_id, name, before, after, fields, ...}, ...],
        "skip":   [{row_index, reason}, ...],
        "error":  [{row_index, errors}, ...],
        "truncated": bool,
      }
    """
    batch = _load_owned_batch(db, batch_id, user_id)
    if batch.status not in ("resolved", "completed", "rolled_back"):
        raise BatchInWrongState(
            f"preview requires status=resolved/completed/rolled_back, got {batch.status}"
        )

    rows = list(
        db.execute(
            select(StagingProduct).where(StagingProduct.batch_id == batch_id)
        ).scalars()
    )
    target_ids = {
        row.match_target_id for row in rows if row.match_target_id is not None
    }
    targets = (
        {
            product.id: product
            for product in db.execute(
                select(Product).where(Product.id.in_(target_ids))
            ).scalars()
        }
        if target_ids
        else {}
    )
    fk_resolver = _FKResolver(db)

    create: list[dict[str, Any]] = []
    update: list[dict[str, Any]] = []
    skip: list[dict[str, Any]] = []
    error: list[dict[str, Any]] = []

    truncated = False
    counts = {"create": 0, "update": 0, "skip": 0, "error": 0}
    for sp in rows:
        validation_errors = _price_errors(sp)
        if sp.match_status == "error" or validation_errors:
            counts["error"] += 1
            if len(error) < limit:
                error.append(
                    {
                        "row_index": sp.row_index,
                        "errors": list(dict.fromkeys([*(sp.validation_errors or []), *validation_errors])),
                    }
                )
            else:
                truncated = True
            continue
        if sp.match_status == "new":
            counts["create"] += 1
            if len(create) < limit:
                create.append(
                    {
                        "row_index": sp.row_index,
                        "product_name": sp.product_name,
                        "code": sp.product_code,
                        "price": sp.price,
                        "contract_price": _json_safe(_decimal_or_none(_canonical_fields(sp).get("contract_price"))),
                        **{
                            field: _json_safe(
                                _parse_date_safe(_canonical_fields(sp).get(field))
                            )
                            for field in _DATE_FIELDS
                        },
                    }
                )
            else:
                truncated = True
            continue
        if sp.match_status in ("exact", "name_exact", "fuzzy"):
            target = targets.get(sp.match_target_id)
            if target is None:
                counts["skip"] += 1
                # match disappeared between resolve and preview — surface as skip
                if len(skip) < limit:
                    skip.append(
                        {"row_index": sp.row_index, "reason": "match target disappeared"}
                    )
                else:
                    truncated = True
                continue
            if batch.status == "resolved" and sp.expected_revision != target.revision:
                counts["error"] += 1
                if len(error) < limit:
                    error.append({"row_index": sp.row_index, "product_id": target.id,
                                  "errors": ["产品在解析后已修改或旧批次缺少版本，请重新解析并确认预览"]})
                else:
                    truncated = True
                continue
            diff = _field_diff(db, target, sp, fk_resolver=fk_resolver)
            # New 4-state schema: a row is "actually changing" iff any
            # field has will_write=True (action ∈ {change, set_new}).
            # The other actions (unchanged, keep_db) are non-mutations.
            mutating_fields = [f for f, info in diff.items() if info["will_write"]]
            if not mutating_fields:
                counts["skip"] += 1
                if len(skip) < limit:
                    skip.append(
                        {
                            "row_index": sp.row_index,
                            "product_id": target.id,
                            "reason": "no field changes",
                            # Surface the 4-state info even when skipping
                            # so the agent can answer "why did this row
                            # not change?" — the answer is in `fields`.
                            "fields": diff,
                        }
                    )
                else:
                    truncated = True
            else:
                counts["update"] += 1
                if len(update) < limit:
                    update.append(
                        {
                            "row_index": sp.row_index,
                            "product_id": target.id,
                            "match_status": sp.match_status,
                            "confidence": sp.confidence,
                            "fields": diff,
                            # Convenience for the agent: list of field
                            # names that will be written. Saves the LLM
                            # from re-filtering on `will_write`.
                            "will_write_fields": mutating_fields,
                        }
                    )
                else:
                    truncated = True

    return {
        "summary": {
            **counts,
            "total": batch.total_rows,
        },
        "create": create,
        "update": update,
        "skip": skip,
        "error": error,
        "truncated": truncated,
    }


# ──────────────────────────────────────────────────────────────
# Preview field-diff — 4-state per-field classifier
# ──────────────────────────────────────────────────────────────
#
# Each of the 19 mutable fields gets one of 4 states:
#
#   change     — Excel provided value, differs from DB value → will write
#   unchanged  — Excel provided value, equals DB value       → no-op
#   keep_db    — Excel cell empty/missing                    → DB unchanged
#   set_new    — DB is NULL/empty, Excel provided value      → will write
#
# Why 4 states instead of "changes only":
#
# Old `_field_diff` returned ONLY changed fields, plus only covered 3 of
# the fields `_apply_update` can mutate. Two failure modes from prod
# 2026-05-18/19:
#   (a) Agent told user "Excel 没填 pack_size" when Excel actually had
#       the same value as DB (action=unchanged, not "missing") — agent
#       had to GUESS because preview hid this state.
#   (b) Preview never surfaced changes to product_name_jp / brand /
#       country_id / supplier_id / effective_from etc. — 11 fields were
#       invisible. User confirmed commits without knowing what would
#       actually change.
#
# Industry-converged pattern (Terraform plan, kubectl diff, Aider/Claude
# Code, Shopify CSV import): always show EVERY field with explicit
# +/~/=/↩ markers. We expose the same structure via `action`.
#
# `will_write` is a derived boolean — redundant with action but lets
# LLM consumers (agent prompts) one-shot "is this row dirty?" without
# re-deriving from string action.


_MUTABLE_FIELDS_SIMPLE_STR = (
    # (column on Product, canonical key on staging raw_data, sp attribute or None)
    ("unit", "unit", "unit"),
    ("pack_size", "pack_size", "pack_size"),
    ("product_name_jp", "product_name_jp", None),
    ("brand", "brand", None),
    ("unit_size", "unit_size", None),
    ("country_of_origin", "country_of_origin", None),
    ("currency", "currency", None),
)

_MUTABLE_FK_FIELDS = (
    # (Product.column, FKResolver kind, canonical key on raw_data)
    ("category_id", "category", "category"),
    ("supplier_id", "supplier", "supplier"),
    ("country_id", "country", "country"),
    ("port_id", "port", "port"),
)


def _classify(db_val: Any, excel_val: Any) -> dict[str, Any]:
    """Return a 4-state record for one field.

    Equality is the same notion `_apply_update` uses internally — for
    strings, both sides normalised to truthy/non-empty; for ints/dates
    direct ==.
    """
    excel_empty = excel_val is None or excel_val == ""
    db_empty = db_val is None or db_val == ""

    if excel_empty:
        return {
            "action": "keep_db",
            "db": _json_safe(db_val) if not db_empty else None,
            "excel": None,
            "will_write": False,
        }
    if db_empty:
        return {
            "action": "set_new",
            "db": None,
            "excel": _json_safe(excel_val),
            "will_write": True,
        }
    if db_val == excel_val:
        return {
            "action": "unchanged",
            "db": _json_safe(db_val),
            "excel": _json_safe(excel_val),
            "will_write": False,
        }
    return {
        "action": "change",
        "db": _json_safe(db_val),
        "excel": _json_safe(excel_val),
        "will_write": True,
    }


def _field_diff(
    db: Session,
    target: Product,
    sp: StagingProduct,
    *,
    fk_resolver: _FKResolver | None = None,
) -> dict[str, dict[str, Any]]:
    """Return a per-field 4-state classification for ALL 15 fields
    `_apply_update` is allowed to mutate.

    Unlike the prior (3-field, changed-only) version, this returns the
    same 15 fields every time, so the consumer can answer "what's the
    state of field X for row Y" without re-reading the staging row.
    `will_write=True` marks the fields that the upcoming commit will
    actually mutate; the caller can filter to those for a concise view.
    """
    out: dict[str, dict[str, Any]] = {}
    extras = _canonical_fields(sp)
    fk_resolver = fk_resolver or _FKResolver(db)

    # Price — Decimal vs float comparison normalised via float()
    excel_price = float(sp.price) if sp.price is not None else None
    db_price = float(target.price) if target.price is not None else None
    out["price"] = _classify(db_price, excel_price)
    out["contract_price"] = _classify(
        target.contract_price, _decimal_or_none(extras.get("contract_price"))
    )

    # Simple string fields
    for col, canonical_key, sp_attr in _MUTABLE_FIELDS_SIMPLE_STR:
        if sp_attr is not None:
            excel = getattr(sp, sp_attr, None)
        else:
            excel = _str_or_none(extras.get(canonical_key))
        db_val = getattr(target, col, None)
        out[col] = _classify(db_val, excel)

    # FK fields — Excel cell is a NAME; resolve to id for comparison
    for col, kind, canonical_key in _MUTABLE_FK_FIELDS:
        excel_name = extras.get(canonical_key)
        excel_id = (
            None
            if excel_name in (None, "")
            else fk_resolver.resolve(kind, excel_name)
        )
        db_id = getattr(target, col, None)
        out[col] = _classify(db_id, excel_id)

    # Date fields
    for field in _DATE_FIELDS:
        excel_dt = _parse_date_safe(extras.get(field))
        db_dt = getattr(target, field, None)
        out[field] = _classify(db_dt, excel_dt)

    return out


# ─── Stage 4: commit ─────────────────────────────────────────


def commit_batch(db: Session, *, batch_id: int, user_id: int) -> dict[str, int]:
    """Apply changes; per-row savepoints isolate failures."""
    batch = _load_owned_batch(db, batch_id, user_id)
    db.refresh(batch, with_for_update=True)
    if batch.status != "resolved":
        raise BatchInWrongState(
            f"commit requires status=resolved, got {batch.status}"
        )

    rows = list(
        db.execute(
            select(StagingProduct).where(StagingProduct.batch_id == batch_id)
        ).scalars()
    )

    # Lock every existing product once, in a stable order, and validate the
    # batch's optimistic revision before applying any of its rows. This makes
    # repeated rows for different periods safe while still detecting edits
    # made after preview.
    target_ids = sorted(
        {row.match_target_id for row in rows if row.match_target_id is not None}
    )
    locked_targets = {
        product.id: product
        for product in db.scalars(
            select(Product)
            .where(Product.id.in_(target_ids))
            .order_by(Product.id)
            .with_for_update()
        ).all()
    }
    stale_target_ids = {
        row.match_target_id
        for row in rows
        if row.match_target_id is not None
        and (
            row.match_target_id not in locked_targets
            or row.expected_revision != locked_targets[row.match_target_id].revision
        )
    }
    created_in_batch: dict[tuple[Any, ...], Product] = {}

    created = 0
    updated = 0
    skipped = 0
    errors = 0
    error_details: list[dict[str, Any]] = []

    for sp in rows:
        price_errors = _price_errors(sp)
        if price_errors:
            sp.match_status = "error"
            sp.validation_errors = price_errors
        if sp.match_status == "error":
            errors += 1
            error_details.append(
                {
                    "row_index": sp.row_index,
                    "product_code": sp.product_code,
                    "product_name": sp.product_name,
                    "errors": list(sp.validation_errors or []),
                }
            )
            continue

        # Decide the operation OUTCOME inside the savepoint, but only
        # increment the outer counter AFTER the savepoint exits cleanly.
        # Prior version incremented `updated += 1` before the savepoint's
        # implicit flush; if flush then raised IntegrityError, the counter
        # had already moved → "updated=69, errors=57" math nonsense in
        # batch #5. Now we track the operation in a local var and apply
        # the count only on the success branch.
        outcome: str | None = None
        try:
            with db.begin_nested():
                if sp.match_status == "new":
                    identity_key = _new_product_batch_key(sp)
                    existing_new = created_in_batch.get(identity_key)
                    if existing_new is None:
                        new_p = _apply_create(db, sp, batch.id, user_id)
                        if new_p is not None:
                            created_in_batch[identity_key] = new_p
                        outcome = "created" if new_p is not None else "skipped"
                    else:
                        n = _apply_update(db, existing_new, sp, batch.id, user_id)
                        outcome = "updated" if n > 0 else "skipped"
                elif sp.match_status in ("exact", "name_exact", "fuzzy"):
                    if sp.expected_revision is None:
                        raise Conflict("旧批次缺少产品版本，请重新解析并确认预览")
                    if sp.match_target_id in stale_target_ids:
                        raise Conflict("产品在预览后已修改，请重新解析并确认")
                    target = locked_targets.get(sp.match_target_id)
                    if target is None:
                        outcome = "skipped"
                    else:
                        n = _apply_update(db, target, sp, batch.id, user_id)
                        outcome = "updated" if n > 0 else "skipped"
        except Exception as exc:
            logger.exception("commit row %s failed", sp.id)
            errors += 1
            err_text = f"{type(exc).__name__}: {exc}"
            sp.validation_errors = [err_text]
            # CRITICAL: flip the row's match_status to "error" so that
            # `preview_changes` (which filters by `match_status == 'error'`)
            # surfaces the row to the agent / UI on subsequent reads.
            # Without this the failure is persisted in `validation_errors`
            # but structurally invisible (prior bug — staging row keeps
            # status="exact" → preview_upload returns empty error list →
            # agent says "我无法获取错误详情").
            sp.match_status = "error"
            error_details.append(
                {
                    "row_index": sp.row_index,
                    "product_code": sp.product_code,
                    "product_name": sp.product_name,
                    "errors": [err_text],
                }
            )
            continue  # Skip the outcome accounting below — already counted

        # Savepoint exited successfully; apply the outcome to outer counters.
        if outcome == "created":
            created += 1
        elif outcome == "updated":
            updated += 1
        elif outcome == "skipped":
            skipped += 1

    batch.created_count = created
    batch.updated_count = updated
    batch.skipped_count = skipped
    batch.error_rows = max(batch.error_rows, errors)
    batch.status = "completed"
    batch.committed_at = datetime.utcnow()
    db.commit()

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "error_details": error_details,
    }


def _new_product_batch_key(sp: StagingProduct) -> tuple[Any, ...]:
    fields = _canonical_fields(sp)
    return (
        str(fields.get("country") or "").strip().casefold(),
        str(sp.product_name or "").strip().casefold(),
        str(fields.get("port") or "").strip().casefold(),
    )


def _apply_create(
    db: Session, sp: StagingProduct, batch_id: int, user_id: int
) -> Product | None:
    if not sp.product_name:
        return None

    # Pull every supported field from the canonical view of raw_data so
    # adding a new alias later doesn't need a backfill of staging rows.
    extras = _canonical_fields(sp)
    fk_resolver = _FKResolver(db)

    p = Product(
        product_name_en=sp.product_name,
        product_name_jp=_str_or_none(extras.get("product_name_jp")),
        code=sp.product_code,
        brand=_str_or_none(extras.get("brand")),
        country_id=fk_resolver.resolve("country", extras.get("country")),
        category_id=fk_resolver.resolve("category", extras.get("category")),
        supplier_id=fk_resolver.resolve("supplier", extras.get("supplier")),
        port_id=fk_resolver.resolve("port", extras.get("port")),
        unit=sp.unit,
        price=Decimal(str(sp.price)) if sp.price is not None else None,
        contract_price=_decimal_or_none(extras.get("contract_price")),
        purchase_price_effective_from=_parse_date_safe(
            extras.get("purchase_price_effective_from")
        ),
        purchase_price_effective_to=_parse_date_safe(
            extras.get("purchase_price_effective_to")
        ),
        selling_price_effective_from=_parse_date_safe(
            extras.get("selling_price_effective_from")
        ),
        selling_price_effective_to=_parse_date_safe(
            extras.get("selling_price_effective_to")
        ),
        unit_size=_str_or_none(extras.get("unit_size")),
        pack_size=sp.pack_size,
        country_of_origin=_str_or_none(extras.get("country_of_origin")),
        currency=_str_or_none(extras.get("currency")),
        effective_from=_parse_date_safe(extras.get("effective_from")),
        effective_to=_parse_date_safe(extras.get("effective_to")),
        status=True,
    )
    db.add(p)
    price_history.initial(db, p, actor_id=user_id, source="upload", batch_id=batch_id)
    sync_compatibility_periods(
        db,
        p,
        actor_id=user_id,
        source="upload",
        source_batch_id=batch_id,
    )
    db.add(
        ProductChangeLog(
            batch_id=batch_id,
            product_id=p.id,
            user_id=user_id,
            action="create",
            field_name=None,
            old_value=None,
            new_value=str(sp.product_name),
            product_revision=p.revision,
        )
    )
    return p


def _parse_date_safe(value: Any) -> datetime | None:
    """`_parse_date` but treats the failure sentinel as `None` (caller
    knows resolve_and_score already flagged it as a validation_error)."""
    result = _parse_date(value)
    if isinstance(result, _DateParseError):
        return None
    return result


def _price_period_errors(
    values: dict[str, Any], current: Product | None = None
) -> list[str]:
    """Validate uploaded price periods, including retained existing endpoints."""
    errors: list[str] = []
    for start_field, end_field, label in _PRICE_PERIODS:
        parsed: dict[str, datetime | None] = {}
        invalid = False
        for field in (start_field, end_field):
            raw = values.get(field)
            if raw in (None, ""):
                parsed[field] = getattr(current, field) if current is not None else None
                continue
            value = _parse_date(raw)
            if value is _DATE_PARSE_ERROR:
                invalid = True
                break
            parsed[field] = value
        if invalid:
            continue
        start = parsed[start_field]
        end = parsed[end_field]
        if start is not None and end is not None and start > end:
            errors.append(f"{label}有效开始日期不能晚于结束日期")
    return errors


def _apply_update(
    db: Session, target: Product, sp: StagingProduct, batch_id: int, user_id: int
) -> int:
    """Apply each changed field to a matched Product. Return number of
    fields actually changed (drives the `updated` vs `skipped` counter).

    `product_name_en` and `code` are intentionally **immutable** here —
    they're the row's identity. If you renamed an existing SKU you'd
    want a separate "merge / split" workflow, not silently overwrite.
    Everything else (price, unit, brand, FKs, currency, dates …) is
    mutable; an empty cell means "leave it alone" (otherwise users
    would have to re-type every column every time).
    """
    before_snapshot = price_history.snapshot(target)
    written_logs = []
    extras = _canonical_fields(sp)
    fk_resolver = _FKResolver(db)
    n = 0

    period_errors = _price_period_errors(extras, target)
    if period_errors:
        raise UploadError("；".join(period_errors))

    def _log(field: str, old: Any, new: Any) -> None:
        log = ProductChangeLog(batch_id=batch_id, product_id=target.id, user_id=user_id,
                               action="update", field_name=field,
                               old_value=None if old is None else str(old),
                               new_value=None if new is None else str(new))
        db.add(log)
        written_logs.append(log)

    # Price: distinct test because Decimal/float comparison + zero is valid.
    if sp.price is not None and (
        target.price is None or float(target.price) != float(sp.price)
    ):
        old = float(target.price) if target.price is not None else None
        target.price = Decimal(str(sp.price))
        _log("price", old, sp.price)
        n += 1

    # contract_price: same pattern, but the source lives in extras (no
    # dedicated StagingProduct column). Blank cell = "leave it alone",
    # mirroring the simple_string_fields convention below.
    new_contract = _decimal_or_none(extras.get("contract_price"))
    if new_contract is not None and (
        target.contract_price is None
        or float(target.contract_price) != float(new_contract)
    ):
        old_cp = (
            float(target.contract_price) if target.contract_price is not None else None
        )
        target.contract_price = new_contract
        _log("contract_price", old_cp, float(new_contract))
        n += 1

    # Simple string fields — `new` truthy AND different from current.
    # `extras.get(canonical)` returns the raw Excel cell value; `_str_or_none`
    # normalises empty strings to None so we don't overwrite a real value
    # with "".
    simple_string_fields = {
        "unit": sp.unit,
        "pack_size": sp.pack_size,
        "product_name_jp": _str_or_none(extras.get("product_name_jp")),
        "brand": _str_or_none(extras.get("brand")),
        "unit_size": _str_or_none(extras.get("unit_size")),
        "country_of_origin": _str_or_none(extras.get("country_of_origin")),
        "currency": _str_or_none(extras.get("currency")),
    }
    for field, new_val in simple_string_fields.items():
        if not new_val:
            continue
        current = getattr(target, field, None)
        if (current or "") == new_val:
            continue
        setattr(target, field, new_val)
        _log(field, current, new_val)
        n += 1

    # FK fields — value in Excel is a NAME; we look up the id.
    fk_fields = {
        "category_id": ("category", extras.get("category")),
        "supplier_id": ("supplier", extras.get("supplier")),
        "country_id": ("country", extras.get("country")),
        "port_id": ("port", extras.get("port")),
    }
    for col, (kind, name) in fk_fields.items():
        if name in (None, ""):
            continue
        new_id = fk_resolver.resolve(kind, name)
        if new_id is None:
            # resolve_and_score already flagged this — savepoint may have
            # caught it. Belt-and-braces here: skip silently rather than
            # write NULL over an existing valid FK.
            continue
        current = getattr(target, col, None)
        if current == new_id:
            continue
        setattr(target, col, new_id)
        _log(col, current, new_id)
        n += 1

    # Date fields.
    for field in _DATE_FIELDS:
        new_val = _parse_date_safe(extras.get(field))
        if new_val is None:
            continue
        current = getattr(target, field, None)
        if current == new_val:
            continue
        setattr(target, field, new_val)
        _log(field, current, new_val.isoformat() if new_val else None)
        n += 1

    sync_compatibility_periods(
        db,
        target,
        actor_id=user_id,
        source="upload",
        source_batch_id=batch_id,
    )
    price_history.record_change(db, target, before_snapshot, actor_id=user_id,
                                source="upload", batch_id=batch_id)
    db.flush()
    for log in written_logs:
        log.product_revision = target.revision
    return n


# ─── Stage 5: rollback ───────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# inspect_row — single-row full 4-state view (per-field + immutable
# context + unrecognised columns). This is the "drill-down" agent tool
# users hit when they ask "what about row N specifically".
# ──────────────────────────────────────────────────────────────


def inspect_row(
    db: Session, *, batch_id: int, row_index: int, user_id: int
) -> dict[str, Any]:
    """Return full per-row inspection: 4-state for each mutable
    fields, plus identity fields (product_name_en, code) marked
    immutable for context, plus any unrecognised column headers the
    Excel had (so the agent can flag "I saw this column but couldn't
    map it" instead of silently dropping them).

    Why a dedicated tool: previous flows had the agent guessing about
    row-specific values from cached preview output. The HARD RULE in
    SKILL.md Step 5c says the agent MUST call this rather than guess —
    output is the ONLY truthful source for per-row questions.
    """
    batch = _load_owned_batch(db, batch_id, user_id)
    sp = db.execute(
        select(StagingProduct).where(
            StagingProduct.batch_id == batch_id,
            StagingProduct.row_index == row_index,
        )
    ).scalar_one_or_none()
    if sp is None:
        raise UploadError(
            f"row_index={row_index} not found in batch {batch_id}"
        )

    out: dict[str, Any] = {
        "batch_id": batch_id,
        "batch_status": batch.status,
        "row_index": row_index,
        "match_status": sp.match_status,
        "product_code": sp.product_code,
        "product_name": sp.product_name,
        "matched_product_id": sp.match_target_id,
        "validation_errors": list(sp.validation_errors or []),
    }

    # Identity fields shown for context but flagged immutable.
    target = db.get(Product, sp.match_target_id) if sp.match_target_id else None

    if target is not None:
        # Complete 4-state body
        out["fields"] = _field_diff(db, target, sp)
        # Identity context — never mutated by upload, surfaced for sanity check
        out["identity"] = {
            "product_name_en": {
                "action": "immutable",
                "db": target.product_name_en,
                "excel": sp.product_name,
                "will_write": False,
            },
            "code": {
                "action": "immutable",
                "db": target.code,
                "excel": sp.product_code,
                "will_write": False,
            },
        }
    else:
        # No matched target → this row will INSERT a new product.
        # Show what Excel will write (no DB side to compare against).
        extras = _canonical_fields(sp)
        out["fields"] = {
            "price": {
                "action": "set_new" if sp.price is not None else "keep_db",
                "db": None,
                "excel": float(sp.price) if sp.price is not None else None,
                "will_write": sp.price is not None,
            },
            "contract_price": {
                "action": (
                    "set_new"
                    if extras.get("contract_price") not in (None, "")
                    else "keep_db"
                ),
                "db": None,
                "excel": _json_safe(_decimal_or_none(extras.get("contract_price"))),
                "will_write": extras.get("contract_price") not in (None, ""),
            },
            "unit": {
                "action": "set_new" if sp.unit else "keep_db",
                "db": None,
                "excel": sp.unit,
                "will_write": bool(sp.unit),
            },
            "pack_size": {
                "action": "set_new" if sp.pack_size else "keep_db",
                "db": None,
                "excel": sp.pack_size,
                "will_write": bool(sp.pack_size),
            },
        }
        for col, _canon, _attr in _MUTABLE_FIELDS_SIMPLE_STR[2:]:  # skip unit / pack_size
            v = _str_or_none(extras.get(col))
            out["fields"][col] = {
                "action": "set_new" if v else "keep_db",
                "db": None,
                "excel": v,
                "will_write": bool(v),
            }
        for field in _DATE_FIELDS:
            value = _parse_date_safe(extras.get(field))
            out["fields"][field] = {
                "action": "set_new" if value is not None else "keep_db",
                "db": None,
                "excel": _json_safe(value),
                "will_write": value is not None,
            }
        out["identity"] = {
            "product_name_en": {
                "action": "set_new",
                "db": None,
                "excel": sp.product_name,
                "will_write": bool(sp.product_name),
            },
            "code": {
                "action": "set_new",
                "db": None,
                "excel": sp.product_code,
                "will_write": bool(sp.product_code),
            },
        }

    # Unrecognised columns — Excel headers that `_normalize_header`
    # couldn't map to a canonical field. Silent-drop was the old
    # behaviour; surface them here so the agent can warn the user.
    out["unrecognized_columns"] = sorted(
        h for h in (sp.raw_data or {})
        if _normalize_header(h) is None and str(h).strip()
    )

    return out


# ──────────────────────────────────────────────────────────────
# Rollback restorers — text-back-to-typed mapping for every field
# `_apply_update` is allowed to mutate.
# ──────────────────────────────────────────────────────────────
#
# Each entry maps `field_name` → fn(text_value) → typed_value. The
# `text_value` is what `_log()` wrote into `v3_product_changelog.old_value`
# at the time of the original update; we have to invert it.
#
# Why this is a dict and not hardcoded if/elif: previously the rollback
# only knew 3 mutable fields (price/unit/pack_size); the others stayed
# dirty after a "rollback" — silently incomplete. Verified
# prod 2026-05-18 batch #6 (69 updated products, 8 distinct fields
# touched: only 3 would have been restorable under the old code).
#
# Format quirks established by sampling real changelog data:
#   - `_log()` calls `str(old)`. For Decimal price → `"67.0"`. For int FK
#     → `"15"`. For naive datetime → `"2026-03-01 00:00:00"` BUT for
#     tz-aware datetime (which Postgres returns for `effective_from` even
#     though the column is TIMESTAMP WITHOUT TZ) → `"2026-03-01 00:00:00+00:00"`.
#     Python 3.11's `datetime.fromisoformat` handles BOTH; we strip
#     `tzinfo` so the assigned value matches the column (naive).
#   - `_log()` calls `str(None)` and stores literal `"None"` — NO. Look
#     again: `old_value=None if old is None else str(old)` — so None is
#     stored as SQL NULL, surfaced here as `None`. Restorers must accept
#     None and a literal empty string as "this field was previously NULL".
#
# Keep this dict in lock-step with `_apply_update`. The round-trip test
# in test_upload_rollback_roundtrip.py enumerates every field — if
# someone adds a new mutable field to `_apply_update` without adding it
# here, the test fails.


def _restore_optional_str(v: Any) -> str | None:
    return v if v else None


def _restore_optional_decimal(v: Any) -> Decimal | None:
    return Decimal(v) if v else None


def _restore_optional_int(v: Any) -> int | None:
    return int(v) if v else None


def _restore_optional_datetime(v: Any) -> datetime | None:
    if not v:
        return None
    parsed = datetime.fromisoformat(v)
    # Product.effective_{from,to} are `DateTime` (no tz); strip tz so
    # SQLAlchemy / Postgres don't complain about aware-vs-naive mismatch.
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


_FIELD_RESTORERS: dict[str, Any] = {
    # Plain strings
    "unit": _restore_optional_str,
    "pack_size": _restore_optional_str,
    "product_name_jp": _restore_optional_str,
    "brand": _restore_optional_str,
    "unit_size": _restore_optional_str,
    "country_of_origin": _restore_optional_str,
    "currency": _restore_optional_str,
    # Numeric
    "price": _restore_optional_decimal,
    "contract_price": _restore_optional_decimal,
    # FK ints
    "category_id": _restore_optional_int,
    "supplier_id": _restore_optional_int,
    "country_id": _restore_optional_int,
    "port_id": _restore_optional_int,
    # Dates
    "effective_from": _restore_optional_datetime,
    "effective_to": _restore_optional_datetime,
    "purchase_price_effective_from": _restore_optional_datetime,
    "purchase_price_effective_to": _restore_optional_datetime,
    "selling_price_effective_from": _restore_optional_datetime,
    "selling_price_effective_to": _restore_optional_datetime,
}


def cancel_batch(db: Session, *, batch_id: int, user_id: int) -> dict[str, Any]:
    """Mark a resolved-but-uncommitted batch as cancelled.

    Distinct from `rollback_batch`:
      • `cancel_batch` — user declined the batch BEFORE it ever wrote to
        `products`. No changelog exists, nothing to undo; we just mark the
        batch `cancelled` so it stops occupying the HITL queue. Staging
        rows are kept for audit (cheap to retain, useful for "what did I
        almost upload?").
      • `rollback_batch` — batch already wrote rows; walks the changelog
        in reverse to restore prior state.

    Idempotent on a batch that's already `cancelled`.
    """
    batch = _load_owned_batch(db, batch_id, user_id)
    if batch.status == "cancelled":
        return {"ok": True, "batch_id": batch_id, "status": "cancelled", "already": True}
    if batch.status != "resolved":
        raise BatchInWrongState(
            f"cancel 只能用于 status=resolved 的批次（当前 status={batch.status}）。"
            f"已提交的批次请用 rollback。"
        )
    batch.status = "cancelled"
    db.commit()
    return {"ok": True, "batch_id": batch_id, "status": "cancelled", "already": False}


def rollback_batch(db: Session, *, batch_id: int, user_id: int) -> dict[str, Any]:
    """Restore each product atomically, protecting all later product edits."""
    batch = _load_owned_batch(db, batch_id, user_id)
    db.refresh(batch, with_for_update=True)
    if batch.status not in ("completed", "rolled_back"):
        raise BatchInWrongState(f"rollback 仅用于已提交批次；当前 status={batch.status}，未提交批次请使用 cancel")
    if batch.status == "rolled_back":
        return {"deleted": 0, "restored": 0, "skipped": 0}
    logs = db.scalars(select(ProductChangeLog).where(
        ProductChangeLog.batch_id == batch_id, ProductChangeLog.restored_at.is_(None)
    ).order_by(ProductChangeLog.product_id, ProductChangeLog.id.desc())).all()
    grouped = {}
    for log in logs:
        grouped.setdefault(log.product_id, []).append(log)
    deleted = restored = skipped = 0
    conflicts = []
    for product_id, group in grouped.items():
        try:
            with db.begin_nested():
                versions = {log.product_revision for log in group}
                if None in versions or len(versions) != 1:
                    raise Conflict("旧批次缺少可靠版本，不能自动回滚，请核对后重新调价")
                target = price_history.lock_product(db, product_id, next(iter(versions)))
                before = price_history.snapshot(target)
                creating = any(log.action == "create" for log in group)
                if not creating:
                    for log in group:
                        if log.action != "update" or log.field_name not in _FIELD_RESTORERS:
                            raise Conflict("日志字段无法完整恢复，请人工核对")
                        setattr(target, log.field_name, _FIELD_RESTORERS[log.field_name](log.old_value))
                price_history.record_change(db, target, before, actor_id=user_id,
                                            source="rollback", batch_id=batch_id,
                                            event_type="delete" if creating else "restore")
                db.flush()
                if creating:
                    db.delete(target)
                for log in group:
                    log.restored_at = datetime.utcnow()
            if creating:
                deleted += 1
            else:
                restored += len(group)
        except Exception as exc:
            skipped += len(group)
            conflicts.append({"product_id": product_id, "reason": str(exc)})
    batch.status = "rolled_back" if skipped == 0 else "completed"
    db.commit()
    result = {"deleted": deleted, "restored": restored, "skipped": skipped}
    if conflicts:
        result["conflicts"] = conflicts
    return result


# ─── Deterministic workbench contract ───────────────────────


_WORKFLOW_FIELD_LABELS = {
    "price": "采购价",
    "contract_price": "卖价",
    "purchase_price_effective_from": "采购价开始日期",
    "purchase_price_effective_to": "采购价结束日期",
    "selling_price_effective_from": "卖价开始日期",
    "selling_price_effective_to": "卖价结束日期",
    "unit": "单位",
    "pack_size": "包装规格",
    "product_name_jp": "日文品名",
    "brand": "品牌",
    "unit_size": "单位规格",
    "country_of_origin": "原产地",
    "currency": "币种",
    "category_id": "分类",
    "supplier_id": "供应商",
    "country_id": "国家",
    "port_id": "港口",
    "effective_from": "商品有效开始日期",
    "effective_to": "商品有效结束日期",
}

_WORKFLOW_VALIDATION_FIELD_LABELS = {
    "product_name": "产品名称",
    "product_code": "产品代码",
    "price": "采购价",
    "contract_price": "卖价",
    "purchase_price_effective_from": "采购价开始日期",
    "purchase_price_effective_to": "采购价结束日期",
    "selling_price_effective_from": "卖价开始日期",
    "selling_price_effective_to": "卖价结束日期",
    "effective_from": "商品有效开始日期",
    "effective_to": "商品有效结束日期",
    "category": "分类",
    "supplier": "供应商",
    "country": "国家",
    "port": "港口",
    "currency": "币种",
    "unit": "单位",
    "unit_size": "单位规格",
    "pack_size": "包装规格",
}

_FK_DISPLAY_MODELS: dict[str, tuple[str, str]] = {
    "category_id": ("Category", "category"),
    "supplier_id": ("Supplier", "supplier"),
    "country_id": ("Country", "country"),
    "port_id": ("Port", "port"),
}


def _workflow_summary(db: Session, batch: UploadBatch, user_id: int) -> dict[str, int]:
    if batch.status in ("completed", "rolled_back"):
        return {
            "create": batch.created_count,
            "update": batch.updated_count,
            "skip": batch.skipped_count,
            "error": batch.error_rows,
            "total": batch.total_rows,
        }
    if batch.status not in ("resolved", "completed", "rolled_back"):
        return {"create": 0, "update": 0, "skip": 0, "error": 0, "total": batch.total_rows}
    return preview_changes(
        db,
        batch_id=batch.id,
        user_id=user_id,
        limit=max(batch.total_rows, 1),
    )["summary"]


def get_workflow_batch(db: Session, *, batch_id: int, user_id: int) -> dict[str, Any]:
    """Return the single batch snapshot consumed by Steps 2–4."""
    batch = _load_owned_batch(db, batch_id, user_id)
    summary = _workflow_summary(db, batch, user_id)
    diagnostics = batch.header_diagnostics or {
        "columns": [],
        "unrecognized": [],
        "duplicate_canonical": [],
        "missing_required": [],
        "blocking_issues": [],
    }
    blocking_count = len(diagnostics.get("blocking_issues") or [])
    can_continue = (
        batch.status == "resolved"
        and blocking_count == 0
        and summary.get("error", 0) == 0
    )
    if batch.status == "ready":
        current_step = 2
    elif batch.status == "resolved":
        current_step = 3 if can_continue else 2
    else:
        current_step = 4
    return {
        "id": batch.id,
        "filename": batch.filename,
        "file_sha256": batch.file_sha256,
        "original_available": bool(batch.file_url),
        "sheet_name": batch.sheet_name,
        "header_row_number": batch.header_row_number,
        "workflow_version": batch.workflow_version,
        "status": batch.status,
        "current_step": current_step,
        "total_rows": batch.total_rows,
        "header_diagnostics": diagnostics,
        "summary": summary,
        "can_continue": can_continue,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "committed_at": batch.committed_at.isoformat() if batch.committed_at else None,
    }


def validate_workflow_batch(db: Session, *, batch_id: int, user_id: int) -> dict[str, Any]:
    """Run deterministic matching/validation and return the Step 2 result."""
    batch = _load_owned_batch(db, batch_id, user_id)
    if batch.status not in ("ready", "resolved"):
        raise BatchInWrongState(
            f"validate requires status=ready/resolved, got {batch.status}"
        )
    resolve_and_score(db, batch_id=batch_id, user_id=user_id)
    return get_workflow_batch(db, batch_id=batch_id, user_id=user_id)


def _localize_workflow_issue(message: str, field: str | None) -> str:
    """Convert internal validation text into the Chinese workbench contract.

    The legacy upload pipeline keeps its existing diagnostics for compatibility;
    only the deterministic workbench DTO is localized here. Unknown English
    exceptions are deliberately not exposed to users.
    """
    price_match = re.match(r"^(price|contract_price):\s*(.+)$", message)
    if price_match:
        label = _WORKFLOW_VALIDATION_FIELD_LABELS[price_match.group(1)]
        detail = price_match.group(2)
        return f"{label}{detail[2:]}" if detail.startswith("价格") else f"{label}：{detail}"

    if message == "missing product_name":
        return "产品名称为必填项"
    if message.startswith("country is required"):
        return "国家为必填项，请填写国家名称（例如：Japan）"
    if message.startswith("port is required"):
        return "港口为必填项，请填写港口名称（例如：Tokyo）"

    fk_match = re.match(
        r"^(category|supplier|country|port) '(.+?)' not found in masterdata",
        message,
    )
    if fk_match:
        label = _WORKFLOW_VALIDATION_FIELD_LABELS[fk_match.group(1)]
        return f"{label}“{fk_match.group(2)}”不存在，请检查名称或先在数据管理中新增"

    date_match = re.match(
        r"^([a-z_]+) '(.+?)' is not a valid date",
        message,
    )
    if date_match:
        label = _WORKFLOW_VALIDATION_FIELD_LABELS.get(date_match.group(1), "日期")
        return f"{label}“{date_match.group(2)}”不是有效日期，请使用 YYYY-MM-DD 格式（例如：2026-05-30）"

    if re.search(r"[\u3400-\u9fff]", message):
        return message
    if field:
        return f"{_WORKFLOW_VALIDATION_FIELD_LABELS.get(field, '该字段')}填写内容不符合要求"
    return "数据内容不符合要求，请检查该行填写内容"


def _workflow_issue(message: str) -> dict[str, Any]:
    field = next(
        (key for key in _HEADER_ALIASES if message.startswith(f"{key}:") or key in message),
        None,
    )
    if message.startswith("采购价有效"):
        field = "purchase_price_effective_from"
    elif message.startswith("卖价有效"):
        field = "selling_price_effective_from"
    if "required" in message or message.startswith("missing "):
        code = "required"
    elif "not found in masterdata" in message:
        code = "masterdata_not_found"
    elif "valid date" in message:
        code = "invalid_date"
    elif "重复" in message:
        code = "duplicate_product"
    else:
        code = "invalid_value"
    return {
        "code": code,
        "field": field,
        "message": _localize_workflow_issue(message, field),
    }


def _fk_name(db: Session, field: str, value: Any) -> Any:
    if value in (None, "") or field not in _FK_DISPLAY_MODELS:
        return value
    from domains.masterdata import models as masterdata_models

    model_name, _ = _FK_DISPLAY_MODELS[field]
    model = getattr(masterdata_models, model_name)
    row = db.get(model, value) if isinstance(value, int) else None
    return row.name if row is not None else value


def _workflow_fields(
    db: Session,
    sp: StagingProduct,
    fields: dict[str, dict[str, Any]],
    *,
    changed_only: bool,
) -> list[dict[str, Any]]:
    canonical = _canonical_fields(sp)
    currency_info = fields.get("currency", {})
    display_currency = (
        currency_info.get("excel")
        if currency_info.get("excel") not in (None, "")
        else currency_info.get("db")
    )
    out: list[dict[str, Any]] = []
    for key, info in fields.items():
        changed = bool(info.get("will_write"))
        if changed_only and not changed:
            continue
        before = _fk_name(db, key, info.get("db"))
        after = _fk_name(db, key, info.get("excel"))
        if key in _FK_DISPLAY_MODELS and info.get("excel") not in (None, ""):
            after = canonical.get(_FK_DISPLAY_MODELS[key][1], after)
        out.append(
            {
                "key": key,
                "label": _WORKFLOW_FIELD_LABELS.get(key, key),
                "before": before,
                "after": after,
                "action": info.get("action"),
                "changed": changed,
                "currency": display_currency if key in ("price", "contract_price") else None,
            }
        )
    return out


def get_workflow_rows(
    db: Session,
    *,
    batch_id: int,
    user_id: int,
    view: str = "all",
    page: int = 1,
    page_size: int = 50,
    changed_only: bool = True,
) -> dict[str, Any]:
    """Return one unified, server-paginated row shape for Steps 2 and 3."""
    if view not in ("all", "issues", "changes"):
        raise ValueError("view must be all, issues, or changes")
    batch = _load_owned_batch(db, batch_id, user_id)
    preview = preview_changes(
        db, batch_id=batch_id, user_id=user_id, limit=max(batch.total_rows, 1)
    )
    staging = {
        row.row_index: row
        for row in db.execute(
            select(StagingProduct).where(StagingProduct.batch_id == batch_id)
        ).scalars()
    }
    items: list[dict[str, Any]] = []
    for kind in ("error", "create", "update", "skip"):
        for legacy in preview[kind]:
            sp = staging[legacy["row_index"]]
            target = db.get(Product, sp.match_target_id) if sp.match_target_id else None
            identity = {
                "product_id": sp.match_target_id,
                "product_code": sp.product_code,
                "product_name": sp.product_name or (target.product_name_en if target else None),
            }
            fields: list[dict[str, Any]] = []
            issues: list[dict[str, Any]] = []
            if kind == "error":
                issues = [_workflow_issue(message) for message in legacy.get("errors", [])]
            elif kind in ("update", "skip"):
                fields = _workflow_fields(
                    db, sp, legacy.get("fields", {}), changed_only=changed_only
                )
            elif kind == "create":
                canonical = _canonical_fields(sp)
                for key, value in canonical.items():
                    if key in ("product_name", "product_code", "supplier_code") or value in (None, ""):
                        continue
                    display_key = {
                        "category": "category_id",
                        "supplier": "supplier_id",
                        "country": "country_id",
                        "port": "port_id",
                    }.get(key, key)
                    fields.append(
                        {
                            "key": display_key,
                            "label": _WORKFLOW_FIELD_LABELS.get(display_key, display_key),
                            "before": None,
                            "after": _json_safe(value),
                            "action": "set_new",
                            "changed": True,
                            "currency": canonical.get("currency")
                            if display_key in ("price", "contract_price")
                            else None,
                        }
                    )
            items.append(
                {
                    "staging_id": sp.id,
                    "source_row_number": sp.source_row_number or sp.row_index + 1,
                    "kind": kind,
                    "identity": identity,
                    "fields": fields,
                    "issues": issues,
                    "reason": legacy.get("reason"),
                }
            )

    if view == "issues":
        items = [item for item in items if item["kind"] == "error"]
    elif view == "changes":
        items = [item for item in items if item["kind"] in ("create", "update")]
    items.sort(key=lambda item: (item["source_row_number"], item["staging_id"]))
    safe_page = max(1, int(page))
    safe_page_size = max(1, min(int(page_size), 200))
    total_items = len(items)
    total_pages = max(1, (total_items + safe_page_size - 1) // safe_page_size)
    start = (safe_page - 1) * safe_page_size
    return {
        "items": items[start : start + safe_page_size],
        "page": safe_page,
        "page_size": safe_page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "summary": preview["summary"],
    }


def commit_validated_batch(db: Session, *, batch_id: int, user_id: int) -> dict[str, Any]:
    """Commit only after the same server-side checks shown in Step 2/3 pass."""
    snapshot = get_workflow_batch(db, batch_id=batch_id, user_id=user_id)
    if not snapshot["can_continue"]:
        raise BatchValidationFailed("批次仍有表头、数据或并发校验问题，不能提交")
    return commit_batch(db, batch_id=batch_id, user_id=user_id)


def list_workflow_batches(
    db: Session,
    *,
    user_id: int,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """List only workbench-v2 uploads owned by the current user."""
    safe_page = max(1, int(page))
    safe_page_size = max(1, min(int(page_size), 100))
    base = (
        select(UploadBatch)
        .where(UploadBatch.user_id == user_id)
        .where(UploadBatch.workflow_version >= 2)
    )
    total_items = int(
        db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
    )
    batches = list(
        db.execute(
            base.order_by(UploadBatch.created_at.desc(), UploadBatch.id.desc())
            .offset((safe_page - 1) * safe_page_size)
            .limit(safe_page_size)
        ).scalars()
    )
    return {
        "items": [get_workflow_batch(db, batch_id=batch.id, user_id=user_id) for batch in batches],
        "page": safe_page,
        "page_size": safe_page_size,
        "total_items": total_items,
        "total_pages": max(1, (total_items + safe_page_size - 1) // safe_page_size),
    }


def get_workflow_file_key(db: Session, *, batch_id: int, user_id: int) -> str:
    batch = _load_owned_batch(db, batch_id, user_id)
    if not batch.file_url:
        raise BatchNotFound(f"batch {batch_id} has no stored original")
    return batch.file_url


# ─── Listing ─────────────────────────────────────────────────


def list_batches(
    db: Session, *, user_id: int, limit: int = 20, status: str | None = None
) -> list[dict[str, Any]]:
    stmt = (
        select(UploadBatch)
        .where(UploadBatch.user_id == user_id)
        .order_by(UploadBatch.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(UploadBatch.status == status)
    rows = list(db.execute(stmt).scalars())
    return [_batch_summary(b) for b in rows]


def search_batches(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
    status: str | None = None,
    has_errors: bool = False,
) -> dict[str, Any]:
    """Same as list_batches but also returns total matching COUNT.

    Used by agent tools that need to correctly answer "how many uploads
    do I have" without conflating len(items) with the DB count.

    `has_errors=True` filters to batches where any row had a validation
    error (`error_rows > 0`), regardless of batch-level `status`. This is
    orthogonal to `status="failed"` — the latter means the batch itself
    crashed (rare); the former is the common "this upload had bad rows"
    case.

    When filters return 0 rows, the response also includes a `hint`
    counter so the caller can tell whether the *filter* was wrong vs.
    no data exists at all.
    """
    from sqlalchemy import func

    base = select(UploadBatch).where(UploadBatch.user_id == user_id)
    if status:
        base = base.where(UploadBatch.status == status)
    if has_errors:
        base = base.where(UploadBatch.error_rows > 0)
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar() or 0
    rows = list(
        db.execute(
            base.order_by(UploadBatch.created_at.desc()).limit(limit)
        ).scalars()
    )
    out: dict[str, Any] = {
        "total": int(total),
        "items": [_batch_summary(b) for b in rows],
    }
    # Self-correcting hint: if a restrictive filter returned 0 but the
    # user has other batches (especially with row errors), surface that
    # so the calling agent can retry with the right filter instead of
    # concluding "no data".
    if total == 0 and (status or has_errors):
        unfiltered_total = db.execute(
            select(func.count())
            .select_from(
                select(UploadBatch).where(UploadBatch.user_id == user_id).subquery()
            )
        ).scalar() or 0
        with_errors_total = db.execute(
            select(func.count())
            .select_from(
                select(UploadBatch)
                .where(UploadBatch.user_id == user_id)
                .where(UploadBatch.error_rows > 0)
                .subquery()
            )
        ).scalar() or 0
        out["hint"] = {
            "all_batches": int(unfiltered_total),
            "batches_with_row_errors": int(with_errors_total),
            "note": (
                "Filter returned 0. If you meant 'batches that had "
                "errors / failed rows', call again with has_errors=true "
                "(NOT status='failed' — that means the whole batch "
                "crashed, which is rare)."
            ),
        }
    return out


def get_batch(db: Session, *, batch_id: int, user_id: int) -> dict[str, Any]:
    batch = _load_owned_batch(db, batch_id, user_id)
    return _batch_summary(batch)


def _batch_summary(b: UploadBatch) -> dict[str, Any]:
    return {
        "id": b.id,
        "filename": b.filename,
        "entity_type": b.entity_type,
        "status": b.status,
        "total_rows": b.total_rows,
        "matched_exact": b.matched_exact,
        "matched_fuzzy": b.matched_fuzzy,
        "new_rows": b.new_rows,
        "error_rows": b.error_rows,
        "created_count": b.created_count,
        "updated_count": b.updated_count,
        "skipped_count": b.skipped_count,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "committed_at": b.committed_at.isoformat() if b.committed_at else None,
    }


# ─── Internal ────────────────────────────────────────────────


def _load_owned_batch(db: Session, batch_id: int, user_id: int) -> UploadBatch:
    batch = db.get(UploadBatch, batch_id)
    if batch is None:
        raise BatchNotFound(f"batch {batch_id} not found")
    if batch.user_id != user_id:
        raise BatchOwnedByOther(f"batch {batch_id} not owned by user {user_id}")
    return batch
