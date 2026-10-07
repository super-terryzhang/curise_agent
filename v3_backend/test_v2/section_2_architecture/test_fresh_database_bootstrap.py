from __future__ import annotations

import importlib
from uuid import UUID

import pytest
from sqlalchemy.engine import make_url

EXPECTED_MODEL_TABLES = {
    "categories",
    "countries",
    "ports",
    "products",
    "supplier_categories",
    "suppliers",
    "users",
    "v2_company_config",
    "v2_delivery_locations",
    "v2_documents",
    "v2_exchange_rates",
    "v2_field_definitions",
    "v2_field_schemas",
    "v2_order_format_templates",
    "v2_orders",
    "v2_refresh_tokens",
    "v2_supplier_templates",
    "v3_agent_memories",
    "v3_auth_rate_windows",
    "v3_auth_sessions",
    "v3_bulk_image_batches",
    "v3_bulk_image_product_plans",
    "v3_bulk_image_staging",
    "v3_chat_messages",
    "v3_chat_sessions",
    "v3_data_changes",
    "v3_data_fields",
    "v3_data_links",
    "v3_data_records",
    "v3_data_tables",
    "v3_data_unique_values",
    "v3_document_folders",
    "v3_inquiries",
    "v3_inquiry_suppliers",
    "v3_import_batches",
    "v3_import_changes",
    "v3_import_rows",
    "v3_line_bind_tokens",
    "v3_line_event_log",
    "v3_line_users",
    "v3_oracle_po_imports",
    "v3_oracle_scan_runs",
    "v3_order_cost_items",
    "v3_order_groups",
    "v3_pending_actions",
    "v3_product_changelog",
    "v3_product_images",
    "v3_product_price_history",
    "v3_product_price_periods",
    "v3_security_events",
    "v3_staging_products",
    "v3_unit_conversion_rules",
    "v3_upload_batches",
    "v3_user_capabilities",
}


def test_model_registry_loads_the_complete_application_inventory():
    from infrastructure.db.base import Base
    from infrastructure.db.model_registry import import_all_models

    import_all_models()

    assert set(Base.metadata.tables) == EXPECTED_MODEL_TABLES


def test_bootstrap_system_catalog_ids_match_migration_contract():
    from scripts.bootstrap_fresh_database import SYSTEM_TABLES

    migration = importlib.import_module("migrations.versions.0036_unified_data_tables")

    assert SYSTEM_TABLES == migration.SYSTEM_TABLES


@pytest.mark.parametrize(
    ("database_url", "expected_database", "message"),
    [
        ("sqlite:///fresh.db", "fresh", "PostgreSQL"),
        ("postgresql://u:p@127.0.0.1/fresh", "another", "does not match"),
        ("postgresql://u:p@127.0.0.1/postgres", "postgres", "protected"),
    ],
)
def test_bootstrap_target_validation_fails_closed(database_url, expected_database, message):
    from scripts.bootstrap_fresh_database import BootstrapRefused, validate_target

    with pytest.raises(BootstrapRefused, match=message):
        validate_target(make_url(database_url), expected_database=expected_database)


def test_bootstrap_target_validation_accepts_explicit_fresh_postgres_database():
    from scripts.bootstrap_fresh_database import validate_target

    url = make_url("postgresql+psycopg://u:p@127.0.0.1/cruise_v3_clean_20261006")

    validate_target(url, expected_database="cruise_v3_clean_20261006")


def test_bootstrap_system_catalog_uses_stable_ids():
    from scripts.bootstrap_fresh_database import SYSTEM_TABLES

    assert (
        (UUID("025588dd-ae63-5607-9e78-1179a500ed6e"), "products", "产品"),
        (UUID("480cc5e5-5882-58d1-a227-e8ee734c866f"), "suppliers", "供应商"),
        (UUID("0bc67ecc-ccd3-53b5-8327-74f4b482b7b4"), "orders", "订单"),
    ) == SYSTEM_TABLES


def test_audit_snapshot_accepts_only_bootstrap_rows():
    from scripts.audit_fresh_database import (
        evaluate_snapshot,
        expected_business_classification_snapshot,
        expected_tables,
    )

    counts = dict.fromkeys(expected_tables(), 0)
    counts.update(alembic_version=1, v3_data_tables=3, v3_data_fields=1, users=1)

    issues = evaluate_snapshot(
        actual_tables=set(counts),
        row_counts=counts,
        alembic_heads=["0037_temporary_product_import"],
        system_keys=["orders", "products", "suppliers"],
        system_table_versions={"orders": 1, "products": 2, "suppliers": 1},
        classification_fields=[expected_business_classification_snapshot()],
        users=[("clean-admin@example.com", "superadmin", True, True)],
        expected_admin_email="clean-admin@example.com",
        expected_head="0037_temporary_product_import",
    )

    assert issues == []


def test_audit_snapshot_names_every_unexpected_business_row():
    from scripts.audit_fresh_database import (
        evaluate_snapshot,
        expected_business_classification_snapshot,
        expected_tables,
    )

    counts = dict.fromkeys(expected_tables(), 0)
    counts.update(
        alembic_version=1,
        v3_data_tables=4,
        users=1,
        products=2,
        v2_orders=1,
    )

    issues = evaluate_snapshot(
        actual_tables=set(counts),
        row_counts=counts,
        alembic_heads=["0036_unified_data_tables"],
        system_keys=["orders", "products", "suppliers"],
        system_table_versions={"orders": 1, "products": 2, "suppliers": 1},
        classification_fields=[expected_business_classification_snapshot()],
        users=[("clean-admin@example.com", "superadmin", True, True)],
        expected_admin_email="clean-admin@example.com",
        expected_head="0036_unified_data_tables",
    )

    assert "products contains 2 unexpected row(s)" in issues
    assert "v2_orders contains 1 unexpected row(s)" in issues
    assert "v3_data_tables must contain exactly 3 rows, found 4" in issues
