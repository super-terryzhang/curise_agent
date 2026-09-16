"""Persist versioned arrangement inquiries and their input snapshots.

Revision ID: 0029_arrangement_inquiries
Revises: 0028_upload_workbench_audit
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_arrangement_inquiries"
down_revision = "0028_upload_workbench_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("v3_inquiries", sa.Column("group_id", sa.Integer(), nullable=True))
    op.add_column(
        "v3_inquiries",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("v3_inquiries", sa.Column("member_snapshot", sa.JSON(), nullable=True))
    op.add_column("v3_inquiries", sa.Column("match_snapshot", sa.JSON(), nullable=True))
    op.add_column("v3_inquiries", sa.Column("unmatched_items", sa.JSON(), nullable=True))
    op.add_column("v3_inquiries", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("v3_inquiries", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    op.add_column("v3_inquiries", sa.Column("next_retry_at", sa.DateTime(), nullable=True))
    op.add_column(
        "v3_inquiries",
        sa.Column("run_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "v3_inquiries",
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
    )
    op.create_foreign_key(
        "fk_v3_inquiries_group_id",
        "v3_inquiries",
        "v3_order_groups",
        ["group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_v3_inquiries_group_id", "v3_inquiries", ["group_id"])

    # The original schema enforced one row per anchor order. Arrangement
    # history deliberately stores multiple versions on the same anchor.
    op.drop_constraint("v3_inquiries_order_id_key", "v3_inquiries", type_="unique")
    op.create_unique_constraint(
        "uq_v3_inquiries_group_version",
        "v3_inquiries",
        ["group_id", "version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_v3_inquiries_group_version", "v3_inquiries", type_="unique"
    )
    # A downgrade cannot recreate the legacy one-row constraint while several
    # versions share an anchor. Keep the newest version for each anchor first.
    op.execute(
        sa.text(
            "DELETE FROM v3_inquiries older USING v3_inquiries newer "
            "WHERE older.order_id = newer.order_id AND older.id < newer.id"
        )
    )
    op.create_unique_constraint(
        "v3_inquiries_order_id_key", "v3_inquiries", ["order_id"]
    )
    op.drop_index("ix_v3_inquiries_group_id", table_name="v3_inquiries")
    op.drop_constraint("fk_v3_inquiries_group_id", "v3_inquiries", type_="foreignkey")
    for name in (
        "max_attempts",
        "run_attempts",
        "next_retry_at",
        "heartbeat_at",
        "error_message",
        "unmatched_items",
        "match_snapshot",
        "member_snapshot",
        "version",
        "group_id",
    ):
        op.drop_column("v3_inquiries", name)
