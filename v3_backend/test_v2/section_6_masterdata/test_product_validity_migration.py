"""PostgreSQL contract for migrating product validity into purchase pricing.

Set ``MIGRATION_TEST_DATABASE_URL`` to an isolated database whose name ends in
``_test``.  The test resets that database schema; it never accepts a production
database name.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from infrastructure.db.base import Base

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(database_url: str, *args: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "ENV": "development",
        "SECRET_KEY": "migration-test-secret",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _migration_database_url() -> str:
    database_url = os.getenv("MIGRATION_TEST_DATABASE_URL", "")
    if not database_url:
        pytest.skip("MIGRATION_TEST_DATABASE_URL is required for PostgreSQL migration tests")
    database_name = make_url(database_url).database or ""
    assert database_name.endswith("_test"), "migration test refuses a non-test database"
    return database_url


def _prepare_0032_schema(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        # Build the historical 0032 schema, not tables introduced by 0035.
        # Otherwise upgrade(head) would attempt to create these tables twice.
        future_tables = {
            "v3_data_tables",
            "v3_data_fields",
            "v3_data_records",
            "v3_data_links",
            "v3_data_unique_values",
            "v3_data_changes",
            "v3_import_batches",
            "v3_import_rows",
            "v3_import_changes",
        }
        Base.metadata.create_all(
            engine,
            tables=[t for name, t in Base.metadata.tables.items() if name not in future_tables],
        )
        columns = {column["name"] for column in inspect(engine).get_columns("products")}
        with engine.begin() as connection:
            # Base metadata follows the current application, but this fixture must
            # represent the historical 0032 schema.  Strip structures introduced
            # by 0037 so the migration itself remains responsible for creating
            # them during upgrade(head).
            connection.execute(
                text("ALTER TABLE v3_product_price_periods DROP COLUMN revision")
            )
            connection.execute(
                text("DROP INDEX IF EXISTS uq_products_active_normalized_code_port")
            )
            if "effective_from" not in columns:
                connection.execute(text("ALTER TABLE products ADD COLUMN effective_from TIMESTAMP"))
            if "effective_to" not in columns:
                connection.execute(text("ALTER TABLE products ADD COLUMN effective_to TIMESTAMP"))
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version VALUES ('0032_llm_port_resolution')")
            )
    finally:
        engine.dispose()


def _seed_legacy_cases(database_url: str) -> None:
    engine = create_engine(database_url)
    products = [
        # safe complete legacy interval -> compatibility fields + canonical period
        (9001, "SAFE", "Safe legacy", 10, "2026-01-01", "2026-03-31", None, None),
        # one-sided legacy interval -> exact endpoint only, never invent the other
        (9002, "PARTIAL", "Partial legacy", 20, "2026-04-01", None, None, None),
        # explicit purchase interval wins over different legacy values
        (
            9003,
            "EXPLICIT",
            "Explicit purchase",
            30,
            "2026-01-01",
            "2026-12-31",
            "2026-05-01",
            "2026-05-31",
        ),
        # reversed legacy interval -> no compatibility mutation, audit only
        (9004, "REVERSED", "Reversed legacy", 40, "2026-08-01", "2026-07-01", None, None),
        # valid dates without an amount -> dates migrate, canonical period cannot
        (9005, "NO_PRICE", "No purchase price", None, "2026-09-01", "2026-09-30", None, None),
        # proposed interval overlaps an existing different period -> audit, no insert
        (9006, "OVERLAP", "Overlap", 60, "2026-10-15", "2026-11-15", None, None),
        # exact period exists with another amount -> existing canonical data wins
        (9007, "AMOUNT", "Amount conflict", 70, "2026-12-01", "2026-12-31", None, None),
        # one explicit purchase endpoint may be completed by the matching legacy endpoint
        (
            9008,
            "FILL_BLANK",
            "Fill purchase blank",
            80,
            "2027-01-01",
            "2027-01-31",
            "2027-01-05",
            None,
        ),
    ]
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO products (
                    id, code, product_name_en, price, status, revision, price_version,
                    effective_from, effective_to,
                    purchase_price_effective_from, purchase_price_effective_to
                ) VALUES (
                    :id, :code, :name, :price, true, 1, 0,
                    :legacy_from, :legacy_to, :purchase_from, :purchase_to
                )
                """
            ),
            [
                {
                    "id": row[0],
                    "code": row[1],
                    "name": row[2],
                    "price": row[3],
                    "legacy_from": row[4],
                    "legacy_to": row[5],
                    "purchase_from": row[6],
                    "purchase_to": row[7],
                }
                for row in products
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO v3_product_price_periods (
                    product_id, price_type, amount, effective_from, effective_to,
                    status, source
                ) VALUES
                    (9003, 'purchase', 30, '2026-05-01', '2026-05-31', true, 'test'),
                    (9006, 'purchase', 600, '2026-10-01', '2026-10-31', true, 'test'),
                    (9007, 'purchase', 700, '2026-12-01', '2026-12-31', true, 'test')
                """
            )
        )
    engine.dispose()


def test_product_validity_migration_is_loss_aware_and_reversible():
    """Deleting/overwriting legacy values or canonical prices must fail this test."""

    database_url = _migration_database_url()
    _prepare_0032_schema(database_url)
    _seed_legacy_cases(database_url)

    _run_alembic(database_url, "upgrade", "head")

    engine = create_engine(database_url)
    try:
        product_columns = {column["name"] for column in inspect(engine).get_columns("products")}
        assert "effective_from" not in product_columns
        assert "effective_to" not in product_columns
        assert inspect(engine).has_table("v3_product_validity_migration_audit")

        with engine.connect() as connection:
            products = {
                row.code: row
                for row in connection.execute(
                    text(
                        """
                        SELECT code, purchase_price_effective_from::date AS start_date,
                               purchase_price_effective_to::date AS end_date
                        FROM products WHERE id BETWEEN 9001 AND 9008 ORDER BY id
                        """
                    )
                )
            }
            assert str(products["SAFE"].start_date) == "2026-01-01"
            assert str(products["SAFE"].end_date) == "2026-03-31"
            assert str(products["PARTIAL"].start_date) == "2026-04-01"
            assert products["PARTIAL"].end_date is None
            assert str(products["EXPLICIT"].start_date) == "2026-05-01"
            assert str(products["EXPLICIT"].end_date) == "2026-05-31"
            assert products["REVERSED"].start_date is None
            assert products["REVERSED"].end_date is None
            assert str(products["NO_PRICE"].start_date) == "2026-09-01"
            assert str(products["NO_PRICE"].end_date) == "2026-09-30"
            assert str(products["FILL_BLANK"].start_date) == "2027-01-05"
            assert str(products["FILL_BLANK"].end_date) == "2027-01-31"

            periods = connection.execute(
                text(
                    """
                    SELECT p.code, pp.amount, pp.effective_from, pp.effective_to, pp.source
                    FROM v3_product_price_periods pp
                    JOIN products p ON p.id = pp.product_id
                    WHERE p.id BETWEEN 9001 AND 9008 AND pp.status
                    ORDER BY p.id, pp.effective_from
                    """
                )
            ).all()
            assert [(row.code, int(row.amount), row.source) for row in periods] == [
                ("SAFE", 10, "migration_0033"),
                ("EXPLICIT", 30, "test"),
                ("OVERLAP", 600, "test"),
                ("AMOUNT", 700, "test"),
                ("FILL_BLANK", 80, "migration_0033"),
            ]

            audit = {
                row.code: (row.result, row.issue_code)
                for row in connection.execute(
                    text(
                        """
                        SELECT p.code, a.result, a.issue_code
                        FROM v3_product_validity_migration_audit a
                        JOIN products p ON p.id = a.product_id
                        WHERE p.id BETWEEN 9001 AND 9008
                        """
                    )
                )
            }
            assert audit["SAFE"] == ("canonical_created", None)
            assert audit["PARTIAL"] == ("partial_backfilled", "INCOMPLETE_PERIOD")
            assert audit["EXPLICIT"] == ("existing_period_kept", None)
            assert audit["REVERSED"] == ("review_required", "REVERSED_DATES")
            assert audit["NO_PRICE"] == ("dates_backfilled", "PURCHASE_PRICE_MISSING")
            assert audit["OVERLAP"] == ("review_required", "OVERLAPPING_PERIOD")
            assert audit["AMOUNT"] == ("review_required", "AMOUNT_CONFLICT")
            assert audit["FILL_BLANK"] == ("canonical_created", None)
    finally:
        engine.dispose()

    _run_alembic(database_url, "downgrade", "0032_llm_port_resolution")

    engine = create_engine(database_url)
    try:
        product_columns = {column["name"] for column in inspect(engine).get_columns("products")}
        assert {"effective_from", "effective_to"}.issubset(product_columns)
        assert not inspect(engine).has_table("v3_product_validity_migration_audit")
        with engine.connect() as connection:
            restored = connection.execute(
                text(
                    """
                    SELECT code, effective_from::date AS legacy_from,
                           effective_to::date AS legacy_to,
                           purchase_price_effective_from::date AS purchase_from,
                           purchase_price_effective_to::date AS purchase_to
                    FROM products WHERE id BETWEEN 9001 AND 9008 ORDER BY id
                    """
                )
            ).all()
            by_code = {row.code: row for row in restored}
            assert str(by_code["SAFE"].legacy_from) == "2026-01-01"
            assert str(by_code["SAFE"].legacy_to) == "2026-03-31"
            assert by_code["SAFE"].purchase_from is None
            assert by_code["SAFE"].purchase_to is None
            assert str(by_code["REVERSED"].legacy_from) == "2026-08-01"
            assert str(by_code["REVERSED"].legacy_to) == "2026-07-01"
            assert str(by_code["EXPLICIT"].purchase_from) == "2026-05-01"
            assert str(by_code["EXPLICIT"].purchase_to) == "2026-05-31"
            created_periods = connection.scalar(
                text("SELECT count(*) FROM v3_product_price_periods WHERE source='migration_0033'")
            )
            assert created_periods == 0
    finally:
        engine.dispose()
