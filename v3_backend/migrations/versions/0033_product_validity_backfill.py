"""Backfill product validity dates into canonical purchase-price periods.

Revision ID: 0033_product_validity_backfill
Revises: 0032_llm_port_resolution
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "0033_product_validity_backfill"
down_revision: str | Sequence[str] | None = "0032_llm_port_resolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _date(value: date | datetime | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value


def upgrade() -> None:
    op.create_table(
        "v3_product_validity_migration_audit",
        sa.Column("product_id", sa.Integer(), primary_key=True),
        sa.Column("legacy_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legacy_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purchase_from_before", sa.DateTime(), nullable=True),
        sa.Column("purchase_to_before", sa.DateTime(), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("issue_code", sa.String(length=40), nullable=True),
        sa.Column("created_period_id", sa.Integer(), nullable=True),
        sa.Column(
            "migrated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    bind = op.get_bind()
    products = sa.table(
        "products",
        sa.column("id", sa.Integer()),
        sa.column("price", sa.Numeric(10, 2)),
        sa.column("currency", sa.String(20)),
        sa.column("effective_from", sa.DateTime(timezone=True)),
        sa.column("effective_to", sa.DateTime(timezone=True)),
        sa.column("purchase_price_effective_from", sa.DateTime()),
        sa.column("purchase_price_effective_to", sa.DateTime()),
    )
    periods = sa.table(
        "v3_product_price_periods",
        sa.column("id", sa.Integer()),
        sa.column("product_id", sa.Integer()),
        sa.column("price_type", sa.String(16)),
        sa.column("amount", sa.Numeric(10, 2)),
        sa.column("currency", sa.String(20)),
        sa.column("effective_from", sa.Date()),
        sa.column("effective_to", sa.Date()),
        sa.column("status", sa.Boolean()),
        sa.column("source", sa.String(20)),
        sa.column("source_batch_id", sa.Integer()),
        sa.column("created_by", sa.Integer()),
        sa.column("updated_by", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    audit = sa.table(
        "v3_product_validity_migration_audit",
        sa.column("product_id", sa.Integer()),
        sa.column("legacy_from", sa.DateTime(timezone=True)),
        sa.column("legacy_to", sa.DateTime(timezone=True)),
        sa.column("purchase_from_before", sa.DateTime()),
        sa.column("purchase_to_before", sa.DateTime()),
        sa.column("result", sa.String(32)),
        sa.column("issue_code", sa.String(40)),
        sa.column("created_period_id", sa.Integer()),
        sa.column("migrated_at", sa.DateTime(timezone=True)),
    )

    legacy_rows = bind.execute(
        sa.select(
            products.c.id,
            products.c.price,
            products.c.currency,
            products.c.effective_from,
            products.c.effective_to,
            products.c.purchase_price_effective_from,
            products.c.purchase_price_effective_to,
        )
        .where(
            sa.or_(
                products.c.effective_from.is_not(None),
                products.c.effective_to.is_not(None),
            )
        )
        .order_by(products.c.id)
    ).mappings()

    now = datetime.now(timezone.utc)
    for row in legacy_rows:
        legacy_from = row["effective_from"]
        legacy_to = row["effective_to"]
        purchase_from_before = row["purchase_price_effective_from"]
        purchase_to_before = row["purchase_price_effective_to"]
        proposed_from = purchase_from_before or legacy_from
        proposed_to = purchase_to_before or legacy_to
        proposed_from_date = _date(proposed_from)
        proposed_to_date = _date(proposed_to)

        result = "unchanged"
        issue_code: str | None = None
        created_period_id: int | None = None

        if (
            proposed_from_date is not None
            and proposed_to_date is not None
            and proposed_from_date > proposed_to_date
        ):
            result = "review_required"
            issue_code = "REVERSED_DATES"
        else:
            update_values: dict[str, datetime] = {}
            if purchase_from_before is None and legacy_from is not None:
                update_values["purchase_price_effective_from"] = legacy_from
            if purchase_to_before is None and legacy_to is not None:
                update_values["purchase_price_effective_to"] = legacy_to
            if update_values:
                bind.execute(
                    products.update()
                    .where(products.c.id == row["id"])
                    .values(**update_values)
                )

            if proposed_from_date is None or proposed_to_date is None:
                result = "partial_backfilled"
                issue_code = "INCOMPLETE_PERIOD"
            else:
                existing_periods = bind.execute(
                    sa.select(
                        periods.c.id,
                        periods.c.amount,
                        periods.c.effective_from,
                        periods.c.effective_to,
                        periods.c.status,
                    ).where(
                        periods.c.product_id == row["id"],
                        periods.c.price_type == "purchase",
                    )
                ).mappings().all()
                exact = next(
                    (
                        period
                        for period in existing_periods
                        if period["effective_from"] == proposed_from_date
                        and period["effective_to"] == proposed_to_date
                    ),
                    None,
                )
                overlap = next(
                    (
                        period
                        for period in existing_periods
                        if period["status"]
                        and period["effective_from"] <= proposed_to_date
                        and period["effective_to"] >= proposed_from_date
                    ),
                    None,
                )

                if exact is not None and not exact["status"]:
                    result = "review_required"
                    issue_code = "INACTIVE_PERIOD_CONFLICT"
                elif exact is not None:
                    if row["price"] is not None and Decimal(exact["amount"]) != Decimal(
                        row["price"]
                    ):
                        result = "review_required"
                        issue_code = "AMOUNT_CONFLICT"
                    else:
                        result = "existing_period_kept"
                elif overlap is not None:
                    result = "review_required"
                    issue_code = "OVERLAPPING_PERIOD"
                elif row["price"] is None:
                    result = "dates_backfilled"
                    issue_code = "PURCHASE_PRICE_MISSING"
                else:
                    created_period_id = bind.execute(
                        periods.insert()
                        .values(
                            product_id=row["id"],
                            price_type="purchase",
                            amount=row["price"],
                            currency=row["currency"],
                            effective_from=proposed_from_date,
                            effective_to=proposed_to_date,
                            status=True,
                            source="migration_0033",
                            source_batch_id=None,
                            created_by=None,
                            updated_by=None,
                            created_at=now.replace(tzinfo=None),
                            updated_at=now.replace(tzinfo=None),
                        )
                        .returning(periods.c.id)
                    ).scalar_one()
                    result = "canonical_created"

        bind.execute(
            audit.insert().values(
                product_id=row["id"],
                legacy_from=legacy_from,
                legacy_to=legacy_to,
                purchase_from_before=purchase_from_before,
                purchase_to_before=purchase_to_before,
                result=result,
                issue_code=issue_code,
                created_period_id=created_period_id,
                migrated_at=now,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    audit_rows = bind.execute(
        sa.text(
            """
            SELECT product_id, purchase_from_before, purchase_to_before,
                   created_period_id
            FROM v3_product_validity_migration_audit
            ORDER BY product_id
            """
        )
    ).mappings()
    for row in audit_rows:
        if row["created_period_id"] is not None:
            bind.execute(
                sa.text(
                    """
                    DELETE FROM v3_product_price_periods
                    WHERE id = :period_id AND source = 'migration_0033'
                    """
                ),
                {"period_id": row["created_period_id"]},
            )
        bind.execute(
            sa.text(
                """
                UPDATE products
                SET purchase_price_effective_from = :purchase_from,
                    purchase_price_effective_to = :purchase_to
                WHERE id = :product_id
                """
            ),
            {
                "product_id": row["product_id"],
                "purchase_from": row["purchase_from_before"],
                "purchase_to": row["purchase_to_before"],
            },
        )
    op.drop_table("v3_product_validity_migration_audit")

