"""Document folders — hierarchical document organization (P3B 2026-06-21).

Revision ID: 0018_document_folders
Revises: 0017_document_display_name
Create Date: 2026-06-21

Adds `v3_document_folders` (self-ref tree) + `v2_documents.folder_id` so
users can organize documents into nested folders alongside the existing
`user_tags` system. Folders are the primary location; tags remain the
orthogonal cross-cutting label.

Backfill: not needed. NULL `folder_id` means root-level — every existing
document silently lands at root with no visible change.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_document_folders"
down_revision: str | Sequence[str] | None = "0017_document_display_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v3_document_folders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "parent_folder_id",
            sa.Integer(),
            sa.ForeignKey("v3_document_folders.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "v2_documents",
        sa.Column(
            "folder_id",
            sa.Integer(),
            sa.ForeignKey("v3_document_folders.id"),
            nullable=True,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("v2_documents", "folder_id")
    op.drop_table("v3_document_folders")
