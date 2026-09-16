"""Independent product price ledger and optimistic product revisions.

Run with product writes paused; then enable only the matching new writers.
Preserves all existing production-only columns, constraints and legacy tables.
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_product_price_history"
down_revision = "0024_auth_hardening"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "products", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "products", sa.Column("price_version", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("v3_staging_products", sa.Column("expected_revision", sa.Integer()))
    op.add_column("v3_product_changelog", sa.Column("product_revision", sa.Integer()))
    op.add_column("v3_product_changelog", sa.Column("restored_at", sa.DateTime()))
    op.create_table(
        "v3_product_price_history",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer()),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("before_price", sa.Numeric(10, 2)),
        sa.Column("after_price", sa.Numeric(10, 2)),
        sa.Column("before_contract_price", sa.Numeric(10, 2)),
        sa.Column("after_contract_price", sa.Numeric(10, 2)),
        sa.Column("before_context", sa.JSON()),
        sa.Column("after_context", sa.JSON()),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.Integer()),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("source_batch_id", sa.Integer()),
        sa.Column("source_key", sa.String(120)),
        sa.Column("restores_id", sa.String(32)),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id", "version", name="uq_product_price_version"),
        sa.UniqueConstraint("source_key", name="uq_product_price_source"),
        sa.CheckConstraint("version IS NULL OR version >= 0", name="ck_price_history_version"),
        sa.CheckConstraint(
            "event_type IN ('initial','baseline','change','restore','delete','legacy')",
            name="ck_price_history_event",
        ),
    )
    op.create_index(
        "ix_price_history_product_time",
        "v3_product_price_history",
        ["product_id", "recorded_at", "id"],
    )
    # Data baseline uses only this migration's fixed columns. No runtime imports.
    import uuid
    from datetime import datetime, timezone

    bind = op.get_bind()
    history = sa.table(
        "v3_product_price_history",
        *[
            sa.column(n, t)
            for n, t in [
                ("id", sa.String()),
                ("product_id", sa.Integer()),
                ("version", sa.Integer()),
                ("event_type", sa.String()),
                ("after_price", sa.Numeric(10, 2)),
                ("after_contract_price", sa.Numeric(10, 2)),
                ("after_context", sa.JSON()),
                ("changed_fields", sa.JSON()),
                ("source", sa.String()),
                ("source_key", sa.String()),
                ("recorded_at", sa.DateTime(timezone=True)),
            ]
        ],
    )
    fields = "id product_name_en code price contract_price currency unit unit_size pack_size supplier_id country_id port_id".split()
    # Page bounded memory without mutating the rows being read.
    last_id = -1
    while True:
        rows = (
            bind.execute(
                sa.text(
                    "SELECT "
                    + ", ".join(fields)
                    + " FROM products WHERE id>:last_id ORDER BY id LIMIT 500"
                ),
                {"last_id": last_id},
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        entries = []
        for row in rows:
            snapshot = {
                k: (
                    str(row[k])
                    if k in ("price", "contract_price") and row[k] is not None
                    else row[k]
                )
                for k in fields
            }
            entries.append(
                dict(
                    id=uuid.uuid4().hex,
                    product_id=row["id"],
                    version=0,
                    event_type="baseline",
                    after_price=row["price"],
                    after_contract_price=row["contract_price"],
                    after_context=snapshot,
                    changed_fields=[],
                    source="migration",
                    source_key=f"baseline:{row['id']}",
                    recorded_at=datetime.now(timezone.utc),
                )
            )
        bind.execute(history.insert(), entries)
        last_id = rows[-1]["id"]


def downgrade():
    raise RuntimeError(
        "Price history must be retained. Use a compatible application rollback, not a destructive downgrade."
    )
