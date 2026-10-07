"""Copy the requested production lookup data into the isolated clean database.

Production extraction is a read-only repeatable snapshot. Target writes are
atomic; nonempty targets are accepted only when already identical. No product,
price or order data is copied. Credentials are supplied via environment only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.engine import make_url

TABLES = ("countries", "categories", "ports", "suppliers", "supplier_categories")


def fingerprint(rows: list[dict]) -> str:
    normalized = sorted(json.dumps(canonical_row(row), sort_keys=True, default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256("\n".join(normalized).encode()).hexdigest()


def canonical_row(row: dict) -> dict:
    # Production uses timestamptz; the current ORM uses UTC-naive timestamps.
    return {
        key: value.astimezone(UTC).replace(tzinfo=None)
        if isinstance(value, datetime) and value.tzinfo is not None else value
        for key, value in row.items()
    }


def run(*, apply: bool) -> dict:
    source_url = make_url(os.environ["MASTER_SOURCE_URL"]).set(host="127.0.0.1", port=55451).difference_update_query(["host"])
    target_url = make_url(os.environ["MASTER_TARGET_URL"]).set(host="127.0.0.1", port=55450).difference_update_query(["host"])
    if source_url.database != "cruise_v3" or target_url.database != "cruise_v3_clean":
        raise RuntimeError("Unexpected source or destination database")
    source = sa.create_engine(source_url, isolation_level="REPEATABLE READ")
    target = sa.create_engine(target_url)
    try:
        with source.connect() as connection, connection.begin():
            connection.execute(sa.text("SET TRANSACTION READ ONLY"))
            if connection.scalar(sa.text("SELECT current_database()")) != "cruise_v3":
                raise RuntimeError("Wrong source connection")
            source_tables = {
                name: sa.Table(name, sa.MetaData(), autoload_with=connection)
                for name in TABLES
            }
            payload = {
                name: [canonical_row(dict(row)) for row in connection.execute(sa.select(table)).mappings()]
                for name, table in source_tables.items()
            }
        with target.begin() as connection:
            if connection.scalar(sa.text("SELECT current_database()")) != "cruise_v3_clean":
                raise RuntimeError("Wrong destination connection")
            # Serialize this copy with any lookup-table changes.
            connection.execute(sa.text("LOCK TABLE countries, categories, ports, suppliers, supplier_categories IN SHARE ROW EXCLUSIVE MODE"))
            tables = {
                name: sa.Table(name, sa.MetaData(), autoload_with=connection)
                for name in TABLES
            }
            existing = {
                name: [dict(row) for row in connection.execute(sa.select(table)).mappings()]
                for name, table in tables.items()
            }
            for name, table in tables.items():
                if set(table.columns.keys()) != set(source_tables[name].columns.keys()):
                    raise RuntimeError(f"{name}: source and destination columns differ")
            identical = all(fingerprint(existing[name]) == fingerprint(payload[name]) for name in TABLES)
            if not identical and any(existing.values()):
                raise RuntimeError("Destination already has differing lookup data; manual reconciliation required")
            report = {
                "source": "cruise_v3",
                "target": "cruise_v3_clean",
                "mode": "apply" if apply else "dry_run",
                "already_identical": identical,
                "tables": {
                    name: {
                        "source_rows": len(payload[name]),
                        "destination_before": len(existing[name]),
                        "active_rows": sum(row.get("status") is True for row in payload[name]) if name != "supplier_categories" else None,
                        "sha256": fingerprint(payload[name]),
                    }
                    for name in TABLES
                },
                "duplicate_active_names": {
                    name: sorted(label for label, count in Counter(str(row["name"]).strip().casefold() for row in payload[name] if row.get("status") is True).items() if count > 1)
                    for name in TABLES if name != "supplier_categories"
                },
            }
            if apply and not identical:
                for name, table in tables.items():
                    if payload[name]:
                        connection.execute(table.insert(), payload[name])
                for name in TABLES:
                    copied = [dict(row) for row in connection.execute(sa.select(tables[name])).mappings()]
                    if fingerprint(copied) != fingerprint(payload[name]):
                        raise RuntimeError(f"{name}: verification mismatch")
                for name in TABLES[:-1]:
                    sequence = connection.scalar(sa.text("SELECT pg_get_serial_sequence(:name, 'id')"), {"name": name})
                    if sequence:
                        maximum = max((row["id"] for row in payload[name]), default=0)
                        connection.execute(sa.text("SELECT setval(CAST(:sequence AS regclass), :value, :called)"), {"sequence": sequence, "value": max(1, maximum), "called": maximum > 0})
            report["copied"] = apply and not identical
            return report
    finally:
        source.dispose()
        target.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(run(apply=parser.parse_args().apply), ensure_ascii=False, indent=2))
