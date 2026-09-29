"""Add durable Telegram update queue.

Revision ID: 004_update_queue
Revises: 003_private_access
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_update_queue"
down_revision: str | None = "003_private_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "telegram_update_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_update_id"),
    )
    op.create_index(
        "ix_telegram_update_jobs_update_id",
        "telegram_update_jobs",
        ["telegram_update_id"],
        unique=True,
    )
    op.create_index("ix_telegram_update_jobs_status", "telegram_update_jobs", ["status"])
    op.create_index(
        "ix_telegram_update_jobs_available_at",
        "telegram_update_jobs",
        ["available_at"],
    )
    op.create_index(
        "ix_update_jobs_claim",
        "telegram_update_jobs",
        ["status", "available_at"],
    )


def downgrade() -> None:
    op.drop_table("telegram_update_jobs")
    op.drop_table("system_settings")
