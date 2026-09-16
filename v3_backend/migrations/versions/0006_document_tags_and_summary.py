"""Add tags + summary columns to v2_documents.

Revision ID: 0006_document_tags_and_summary
Revises: 0005_chat_and_memory
Create Date: 2026-05-08

Both columns power document search + AI-context retrieval (see TODO 2026-05-08
"tags + summary v1"):
  - `tags` is a JSON array of free-form strings, mixing system tags like
    `file_type:pdf`, `doc_type:purchase_order`, `extractor:pypdfium2` with
    LLM-generated topic tags like `beef-supplier`, `celebrity-cruise`.
  - `summary` is a 2-3 paragraph LLM-generated abstract used as the
    document detail headline + part of agent retrieval context.

Both default to empty/null so existing rows survive the migration; the
new summarizer step in the workflow back-fills them on next extract.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_document_tags_and_summary"
down_revision: str | Sequence[str] | None = "0005_chat_and_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v2_documents",
        sa.Column("tags", sa.JSON, nullable=True),
    )
    op.add_column(
        "v2_documents",
        sa.Column("summary", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_documents", "summary")
    op.drop_column("v2_documents", "tags")
