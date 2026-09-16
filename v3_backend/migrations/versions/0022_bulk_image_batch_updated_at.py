"""Bulk image batch heartbeat — add updated_at column (2026-07-03).

Revision ID: 0022_bulk_image_batch_updated_at
Revises: 0021_bulk_image_batches
Create Date: 2026-07-03

Why:
    The `sweep_stale_batches` GC job needs a way to detect batches
    stuck in status="processing" — the AsyncioRunner background job
    died before the batch could reach completed/error, so status
    hasn't advanced. Without a heartbeat column we can only look at
    `created_at`, which doesn't distinguish "started 10min ago but
    still processing normally" from "died 10min ago".

    `updated_at` is bumped every time the row changes (SQLAlchemy
    `onupdate=`), AND we explicitly bump it every 5 rows inside the
    commit loop so a live worker's heartbeat is < 60s old under normal
    load. Sweep flags any processing batch with `updated_at > 15min`
    stale as a dead worker.

Backfill:
    Existing rows get `updated_at = created_at` — otherwise every
    old row would look "stuck since epoch" to the sweep query. There
    are only a few rows so backfill is a single UPDATE.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_bulk_image_batch_updated_at"
down_revision: str | Sequence[str] | None = "0021_bulk_image_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v3_bulk_image_batches",
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    # Backfill: every existing row's heartbeat starts equal to
    # created_at. Otherwise sweep would think they've been stuck since
    # the Unix epoch.
    op.execute(
        "UPDATE v3_bulk_image_batches SET updated_at = created_at "
        "WHERE updated_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("v3_bulk_image_batches", "updated_at")
