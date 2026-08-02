"""Add answer_cache table for reuse of expert replies.

Revision ID: 002_answer_cache
Revises: 001_initial
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_answer_cache"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_key", sa.Text(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("source_question_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_question_id"], ["user_questions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("question_key"),
    )
    op.create_index("ix_answer_cache_question_key", "answer_cache", ["question_key"])


def downgrade() -> None:
    op.drop_index("ix_answer_cache_question_key", table_name="answer_cache")
    op.drop_table("answer_cache")
