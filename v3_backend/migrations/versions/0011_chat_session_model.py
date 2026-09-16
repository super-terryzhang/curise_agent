"""Add `model` column to `v3_chat_sessions` for per-session model picker.

Revision ID: 0011_chat_session_model
Revises: 0010_upload_pipeline
Create Date: 2026-05-13

The chat UI now lets users pick a model per session (Gemini default,
Kimi as fallback when Gemini OpenAI-compat hallucinates `parse_file`
tool calls — known bug confirmed via Google AI Developers Forum and
gh:google-gemini/gemini-cli #813). NULL = fall back to the env default
(`AGENT_CHAT_MODEL`). Routing to the right provider endpoint is handled
by `agent.runtime.llm._resolve_provider` based on the model-name prefix.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_chat_session_model"
down_revision: str | Sequence[str] | None = "0010_upload_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "v3_chat_sessions",
        sa.Column("model", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v3_chat_sessions", "model")
