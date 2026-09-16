"""settings + inquiry baseline — registers the v2 tables under v3 Alembic.

Revision ID: 0003_settings_inquiry
Revises: 0002_orders_expand
Create Date: 2026-04-26

Phase 4 migration. The tables (`v2_field_schemas`, `v2_field_definitions`,
`v2_order_format_templates`, `v2_supplier_templates`, `v2_delivery_locations`,
`v2_company_config`) already exist in v2 production. This migration is a no-op
on production — its only purpose is to record the new domain models in v3's
Alembic history.

For fresh dev environments (SQLite), `Base.metadata.create_all` handles
table creation; this migration is still chained as the head so future Phase 4+
migrations layer on top.

Rollback: no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0003_settings_inquiry"
down_revision: str | Sequence[str] | None = "0002_orders_expand"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
