"""Read-only verification for a freshly bootstrapped CruiseAgent database."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.engine import Engine, make_url

from infrastructure.db.base import Base
from infrastructure.db.model_registry import import_all_models
from scripts.bootstrap_fresh_database import (
    SYSTEM_TABLES,
    _alembic_head,
    validate_target,
)

EXTRA_TABLES = {"alembic_version", "v3_product_validity_migration_audit"}
ALLOWED_BOOTSTRAP_TABLES = {"alembic_version", "v3_data_tables", "users"}


class AuditFailed(RuntimeError):
    pass


def expected_tables() -> set[str]:
    import_all_models()
    return set(Base.metadata.tables) | EXTRA_TABLES


def evaluate_snapshot(
    *,
    actual_tables: set[str],
    row_counts: Mapping[str, int],
    alembic_heads: Sequence[str],
    system_keys: Sequence[str],
    users: Sequence[tuple[str, str, bool, bool]],
    expected_admin_email: str | None,
    expected_head: str,
) -> list[str]:
    issues: list[str] = []
    expected = expected_tables()
    missing = sorted(expected - actual_tables)
    unexpected = sorted(actual_tables - expected)
    if missing:
        issues.append("Missing tables: " + ", ".join(missing))
    if unexpected:
        issues.append("Unexpected tables: " + ", ".join(unexpected))
    if list(alembic_heads) != [expected_head]:
        issues.append(
            f"Alembic head must be {expected_head!r}, found {list(alembic_heads)!r}"
        )

    expected_keys = sorted(key for _, key, _ in SYSTEM_TABLES)
    if sorted(system_keys) != expected_keys:
        issues.append(
            f"System catalog keys must be {expected_keys!r}, found {sorted(system_keys)!r}"
        )
    if row_counts.get("v3_data_tables") != len(SYSTEM_TABLES):
        issues.append(
            "v3_data_tables must contain exactly "
            f"{len(SYSTEM_TABLES)} rows, found {row_counts.get('v3_data_tables', 0)}"
        )

    if expected_admin_email:
        expected_user = (expected_admin_email.lower(), "superadmin", True, True)
        if list(users) != [expected_user]:
            issues.append(f"Users must contain only the initial superadmin {expected_admin_email!r}")
    elif users:
        issues.append(f"Users must be empty, found {len(users)} row(s)")

    for table in sorted(actual_tables - ALLOWED_BOOTSTRAP_TABLES):
        count = row_counts.get(table, 0)
        if count:
            issues.append(f"{table} contains {count} unexpected row(s)")
    return issues


def audit_database(
    engine: Engine,
    *,
    expected_database: str,
    expected_admin_email: str | None = None,
) -> dict[str, object]:
    validate_target(engine.url, expected_database=expected_database)
    with engine.connect() as connection:
        actual_tables = set(inspect(connection).get_table_names(schema="public"))
        quote = connection.dialect.identifier_preparer.quote
        row_counts = {
            table: int(connection.scalar(sa.text(f"SELECT count(*) FROM {quote(table)}")) or 0)
            for table in actual_tables
        }
        alembic_heads = (
            list(
                connection.execute(
                    sa.text("SELECT version_num FROM alembic_version ORDER BY version_num")
                ).scalars()
            )
            if "alembic_version" in actual_tables
            else []
        )
        system_keys = (
            list(
                connection.execute(
                    sa.text(
                        "SELECT system_key FROM v3_data_tables "
                        "WHERE table_kind = 'system' ORDER BY system_key"
                    )
                ).scalars()
            )
            if "v3_data_tables" in actual_tables
            else []
        )
        users = (
            [
                (row.email, row.role, row.is_active, row.is_default_password)
                for row in connection.execute(
                    sa.text(
                        "SELECT email, role, is_active, is_default_password "
                        "FROM users ORDER BY id"
                    )
                )
            ]
            if "users" in actual_tables
            else []
        )

    expected_head = _alembic_head()
    issues = evaluate_snapshot(
        actual_tables=actual_tables,
        row_counts=row_counts,
        alembic_heads=alembic_heads,
        system_keys=system_keys,
        users=users,
        expected_admin_email=expected_admin_email,
        expected_head=expected_head,
    )
    if issues:
        raise AuditFailed("Fresh database audit failed:\n- " + "\n- ".join(issues))
    return {
        "database": expected_database,
        "alembic_head": expected_head,
        "tables": len(actual_tables),
        "system_catalog_rows": row_counts["v3_data_tables"],
        "admin_rows": row_counts["users"],
        "business_rows": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--database-url-env", default="FRESH_DATABASE_URL")
    parser.add_argument("--expected-admin-email-env", default="FRESH_ADMIN_EMAIL")
    args = parser.parse_args()
    raw_url = os.environ.get(args.database_url_env, "")
    if not raw_url:
        raise SystemExit(f"Missing environment variable {args.database_url_env}")
    expected_admin = os.environ.get(args.expected_admin_email_env) or None
    engine = sa.create_engine(make_url(raw_url), pool_pre_ping=True)
    try:
        try:
            result = audit_database(
                engine,
                expected_database=args.expected_database,
                expected_admin_email=expected_admin,
            )
        except (AuditFailed, RuntimeError) as exc:
            print(str(exc))
            return 2
    finally:
        engine.dispose()
    print(
        "Fresh database audit passed: "
        f"database={result['database']} head={result['alembic_head']} "
        f"tables={result['tables']} system_catalog_rows={result['system_catalog_rows']} "
        f"admin_rows={result['admin_rows']} business_rows=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
