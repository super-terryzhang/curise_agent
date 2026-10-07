"""Install the current CruiseAgent schema into an explicitly empty PostgreSQL DB.

This is intentionally separate from the incremental Alembic chain: revision
0001 is a legacy baseline and assumes the old v2 schema already exists.
"""

from __future__ import annotations

import argparse
import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session

from infrastructure.config import settings
from infrastructure.db.base import Base
from infrastructure.db.model_registry import import_all_models

SYSTEM_TABLES = (
    (UUID("025588dd-ae63-5607-9e78-1179a500ed6e"), "products", "产品"),
    (UUID("480cc5e5-5882-58d1-a227-e8ee734c866f"), "suppliers", "供应商"),
    (UUID("0bc67ecc-ccd3-53b5-8327-74f4b482b7b4"), "orders", "订单"),
)

PROTECTED_DATABASE_NAMES = {
    "postgres",
    "template0",
    "template1",
    "cruise_v3",
    "cruise_v3_prod",
    "cruise_agent",
}


class BootstrapRefused(RuntimeError):
    """The target failed a safety precondition; no installation was attempted."""


def validate_target(url: URL, *, expected_database: str) -> None:
    if url.get_backend_name() != "postgresql":
        raise BootstrapRefused("Fresh bootstrap supports PostgreSQL only")
    if not expected_database or url.database != expected_database:
        raise BootstrapRefused(
            f"URL database {url.database!r} does not match explicitly expected "
            f"database {expected_database!r}"
        )
    if url.database.lower() in PROTECTED_DATABASE_NAMES:
        raise BootstrapRefused(f"Database {url.database!r} is protected")


def _alembic_head() -> str:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise BootstrapRefused(f"Expected one Alembic head, found {heads}")
    return heads[0]


def _migration_audit_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "v3_product_validity_migration_audit",
        metadata,
        sa.Column("product_id", sa.Integer(), primary_key=True),
        sa.Column("legacy_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legacy_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purchase_from_before", sa.DateTime(), nullable=True),
        sa.Column("purchase_to_before", sa.DateTime(), nullable=True),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("issue_code", sa.String(40), nullable=True),
        sa.Column("created_period_id", sa.Integer(), nullable=True),
        sa.Column(
            "migrated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def _existing_relations(connection: sa.Connection) -> list[str]:
    inspector = inspect(connection)
    relations = set(inspector.get_table_names(schema="public"))
    relations.update(inspector.get_view_names(schema="public"))
    with suppress(NotImplementedError):
        relations.update(inspector.get_materialized_view_names(schema="public"))
    return sorted(relations)


def bootstrap_database(
    engine: Engine,
    *,
    expected_database: str,
    admin_email: str | None = None,
    admin_password: str | None = None,
) -> dict[str, object]:
    validate_target(engine.url, expected_database=expected_database)
    if bool(admin_email) != bool(admin_password):
        raise BootstrapRefused("Admin email and password must be supplied together")
    if admin_password and len(admin_password) < 12:
        raise BootstrapRefused("Admin password must contain at least 12 characters")

    import_all_models()
    head = _alembic_head()
    now = datetime.now(UTC)

    # PostgreSQL DDL is transactional. The advisory lock prevents two bootstrap
    # processes from passing the empty check at the same time.
    with engine.begin() as connection:
        connection.execute(sa.text("SELECT pg_advisory_xact_lock(871185913501)"))
        existing = _existing_relations(connection)
        if existing:
            raise BootstrapRefused(
                "Target public schema is not empty; existing relations: " + ", ".join(existing)
            )

        Base.metadata.create_all(connection)
        extra = sa.MetaData()
        _migration_audit_table(extra).create(connection)
        connection.execute(
            sa.text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        connection.execute(
            sa.text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
            {"head": head},
        )

        data_tables = Base.metadata.tables["v3_data_tables"]
        connection.execute(
            data_tables.insert(),
            [
                {
                    "id": table_id,
                    "name": name,
                    "description": None,
                    "table_kind": "system",
                    "system_key": key,
                    "status": "active",
                    "schema_version": 1,
                    "display_field_id": None,
                    "created_by": 0,
                    "updated_by": 0,
                    "created_at": now,
                    "updated_at": now,
                }
                for table_id, key, name in SYSTEM_TABLES
            ],
        )

        from scripts.seed_clean_product_fields import seed_product_business_classification

        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            seed_product_business_classification(db, actor_id=0)
            db.commit()

        if admin_email and admin_password:
            from infrastructure.security import hash_password

            users = Base.metadata.tables["users"]
            connection.execute(
                users.insert().values(
                    email=admin_email.strip().lower(),
                    hashed_password=hash_password(admin_password),
                    full_name="Initial Administrator",
                    role="superadmin",
                    is_active=True,
                    is_superuser=True,
                    is_default_password=True,
                    temporary_password_expires_at=now
                    + timedelta(hours=settings.TEMPORARY_PASSWORD_HOURS),
                    created_at=now,
                    updated_at=now,
                    failed_login_attempts=0,
                )
            )

    return {
        "database": expected_database,
        "alembic_head": head,
        "model_tables": len(Base.metadata.tables),
        "admin_created": bool(admin_email),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument(
        "--database-url-env",
        default="FRESH_DATABASE_URL",
        help="Environment variable containing the PostgreSQL URL",
    )
    parser.add_argument("--admin-email-env", default="FRESH_ADMIN_EMAIL")
    parser.add_argument("--admin-password-env", default="FRESH_ADMIN_PASSWORD")
    return parser


def main() -> int:
    args = _parser().parse_args()
    raw_url = os.environ.get(args.database_url_env, "")
    if not raw_url:
        raise SystemExit(f"Missing environment variable {args.database_url_env}")
    admin_email = os.environ.get(args.admin_email_env) or None
    admin_password = os.environ.get(args.admin_password_env) or None
    engine = sa.create_engine(make_url(raw_url), pool_pre_ping=True)
    try:
        try:
            result = bootstrap_database(
                engine,
                expected_database=args.expected_database,
                admin_email=admin_email,
                admin_password=admin_password,
            )
        except BootstrapRefused as exc:
            print(f"Fresh database bootstrap refused: {exc}")
            return 2
    finally:
        engine.dispose()
    print(
        "Fresh database installed: "
        f"database={result['database']} head={result['alembic_head']} "
        f"model_tables={result['model_tables']} admin_created={result['admin_created']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
