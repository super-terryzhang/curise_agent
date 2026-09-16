"""Add user_tags column to v2_documents.

Revision ID: 0007_user_tags
Revises: 0006_document_tags_and_summary
Create Date: 2026-05-10

User-managed tags (manually added by humans, not the workflow). Lives
alongside `tags` (system + LLM auto-generated, regenerated on every
re-extract). Splitting into two columns means re-extraction can rebuild
`tags` without trampling the user's manual annotations.

Both columns are JSON arrays of strings. The list endpoint and the
agent's `list_documents` tool will treat the two together for filtering
("does any tag match?") but the UI shows them as distinct chip styles.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_user_tags"
down_revision: str | Sequence[str] | None = "0006_document_tags_and_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v2_documents",
        sa.Column("user_tags", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_documents", "user_tags")
