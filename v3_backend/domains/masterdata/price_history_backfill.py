"""Idempotent legacy evidence import; never invent complete price versions."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import inspect, select, text

from domains.masterdata._money import parse_product_price
from domains.masterdata.models import ProductPriceHistory


def import_legacy(db, *, apply: bool = False) -> dict:
    inspector = inspect(db.connection())
    existing = set(
        db.scalars(
            select(ProductPriceHistory.source_key).where(ProductPriceHistory.event_type == "legacy")
        ).all()
    )
    result = {"importable": 0, "already_imported": 0, "invalid": 0, "inserted": 0}

    def record(generation, row, field, old, new, index=0):
        key = f"legacy:{generation}:{row['id']}:{index}:{field}"
        if key in existing:
            result["already_imported"] += 1
            return
        if row["product_id"] is None:
            result["invalid"] += 1
            return
        try:
            before_value, after_value = parse_product_price(old), parse_product_price(new)
        except ValueError:
            result["invalid"] += 1
            return
        if old in (None, "") and new in (None, ""):
            result["invalid"] += 1
            return
        context = {
            "id": row["product_id"],
            "known_fields": [field],
            "legacy_recorded_at": str(row["occurred_at"]) if row["occurred_at"] else None,
            "batch_status_at_import": row["batch_status"],
            "price": None,
            "contract_price": None,
        }
        before = {
            **context,
            field: format(before_value, ".2f") if before_value is not None else None,
        }
        after = {**context, field: format(after_value, ".2f") if after_value is not None else None}
        event = ProductPriceHistory(
            id=uuid4().hex,
            product_id=row["product_id"],
            version=None,
            event_type="legacy",
            before_price=before.get("price"),
            after_price=after.get("price"),
            before_contract_price=before.get("contract_price"),
            after_contract_price=after.get("contract_price"),
            before_context=before,
            after_context=after,
            changed_fields=[field],
            actor_id=row["actor_id"],
            source="legacy_" + generation,
            source_batch_id=row["batch_id"],
            source_key=key,
            recorded_at=datetime.now(UTC),
        )
        result["importable"] += 1
        if apply:
            db.add(event)
            existing.add(key)
            result["inserted"] += 1

    if inspector.has_table("v3_product_changelog"):
        rows = db.execute(
            text("""SELECT l.id,l.product_id,l.batch_id,l.user_id AS actor_id,
            l.created_at AS occurred_at,l.field_name,l.old_value,l.new_value,b.status AS batch_status
            FROM v3_product_changelog l LEFT JOIN v3_upload_batches b ON b.id=l.batch_id
            WHERE l.action='update' AND l.field_name IN ('price','contract_price')
            AND l.product_revision IS NULL ORDER BY l.id""")
        ).mappings()
        for row in rows:
            record("v3", row, row["field_name"], row["old_value"], row["new_value"])
    if inspector.has_table("v2_product_changelog"):
        rows = db.execute(
            text("""SELECT l.id,l.product_id,l.batch_id,l.changed_by AS actor_id,
            l.changed_at AS occurred_at,l.field_changes,b.status AS batch_status
            FROM v2_product_changelog l LEFT JOIN v2_upload_batches b ON b.id=l.batch_id
            WHERE l.change_type='updated' ORDER BY l.id""")
        ).mappings()
        for row in rows:
            fields = row["field_changes"]
            if isinstance(fields, str):
                try:
                    fields = json.loads(fields)
                except ValueError:
                    result["invalid"] += 1
                    continue
            if not isinstance(fields, list):
                result["invalid"] += 1
                continue
            for i, item in enumerate(fields):
                if isinstance(item, dict) and item.get("field") in ("price", "contract_price"):
                    if "old_value" not in item or "new_value" not in item:
                        result["invalid"] += 1
                        continue
                    record("v2", row, item["field"], item["old_value"], item["new_value"], i)
    if apply:
        db.flush()
    return result
