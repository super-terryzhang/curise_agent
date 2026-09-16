"""baseline — v3 starts against v2's existing production schema (no-op)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-04-24

This migration is intentionally empty. v3 reuses v2's Supabase tables
(`users`, `v2_refresh_tokens`, `countries`, `ports`, `categories`,
`suppliers`, `supplier_categories`, `products`, `v2_exchange_rates`, ...).

Upgrade sets the baseline; nothing is created. Downgrade is a no-op.

Subsequent migrations start from here and add new columns / tables only.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
