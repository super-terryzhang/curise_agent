"""User capabilities — per-user feature access (2026-06-22).

Revision ID: 0020_user_capabilities
Revises: 0019_product_images
Create Date: 2026-06-22

Why this table exists:
    The 4 role tiers (superadmin > admin > finance > employee) gate
    coarse surfaces — but the product needs PER-USER opt-in for a few
    high-sensitivity features (financial PnL today, others tomorrow).
    Adding a `can_view_financials` boolean on users would solve today's
    request but force a schema change + code edit every time a new
    sensitive surface appears. A first-class capabilities table is the
    canonical generic solution.

Design notes:
    - Composite PK (user_id, capability) — naturally idempotent, double-
      grant is a no-op, no duplicate rows possible.
    - `capability` is a kebab-case string key (e.g. "financials.view").
      No FK to a "capabilities catalog" table — the set of known keys
      lives in Python (`infrastructure/capabilities.py`) so adding a new
      capability is a code change, not a data migration.
    - CASCADE on user delete — grants follow the user they belong to.
    - granted_by_user_id + granted_at form the audit trail for "who
      gave this user access, when".

Semantics (encoded in `require_capability` dependency, not the DB):
    - superadmin auto-passes EVERY capability check (root bypass).
    - All other roles: must have an explicit row in this table.
    - Cold-cutover: no rows are pre-populated on this migration. After
      upgrade, only superadmin can see financials until a superadmin
      uses the user-management UI to grant `financials.view`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_user_capabilities"
down_revision: str | Sequence[str] | None = "0019_product_images"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_user_capabilities",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column(
            "granted_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("user_id", "capability"),
    )
    # (capability, user_id) accelerates the "who has this cap?" admin
    # query path used by the user-list UI's capability chip column.
    op.create_index(
        "ix_user_capabilities_cap_user",
        "v3_user_capabilities",
        ["capability", "user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_capabilities_cap_user", table_name="v3_user_capabilities"
    )
    op.drop_table("v3_user_capabilities")
