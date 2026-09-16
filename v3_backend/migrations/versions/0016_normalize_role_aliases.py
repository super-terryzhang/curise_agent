"""Normalize role aliases — TD-4 (2026-06-16).

Revision ID: 0016_normalize_role_aliases
Revises: 0015_order_groups
Create Date: 2026-06-16

`ROLE_LEVELS` historically had two aliases — `super_admin` (synonym
for `superadmin`) and `user` (synonym for `employee`). Two strings
for one role was a latent privilege bug: a guard like
`if user.role == "superadmin"` would silently miss accounts created
with `super_admin`. This migration backfills existing rows to the
canonical name; new writes go through `canonicalize_role()` in
`identity/service.py`.

Downgrade is intentionally one-way (we don't know which `employee`
rows came from `user`). The upgrade is idempotent — re-running is a
no-op once values are canonical.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_normalize_role_aliases"
down_revision: str | Sequence[str] | None = "0015_order_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE users SET role = 'superadmin' WHERE role = 'super_admin'")
    op.execute("UPDATE users SET role = 'employee' WHERE role = 'user'")


def downgrade() -> None:
    # Intentional no-op — the alias collapse is one-way. See module
    # docstring; we can't reconstruct which `employee` rows originally
    # held `user`.
    pass
