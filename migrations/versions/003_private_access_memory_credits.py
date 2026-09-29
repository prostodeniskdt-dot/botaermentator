"""Add private access, response modes, credits, and feedback.

Revision ID: 003_private_access
Revises: 002_answer_cache
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_private_access"
down_revision: str | None = "002_answer_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_users",
        sa.Column("access_status", sa.String(length=32), server_default="pending", nullable=False),
    )
    op.add_column(
        "telegram_users",
        sa.Column("response_mode", sa.String(length=16), server_default="quick", nullable=False),
    )
    op.add_column("telegram_users", sa.Column("memory_summary", sa.Text(), nullable=True))
    op.add_column(
        "telegram_users",
        sa.Column(
            "profile_facts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "telegram_users", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("telegram_users", sa.Column("approved_by", sa.BigInteger(), nullable=True))
    op.create_index("ix_telegram_users_access_status", "telegram_users", ["access_status"])

    op.add_column(
        "chat_sessions",
        sa.Column("is_private", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("chat_sessions", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "chat_sessions", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_chat_sessions_is_private", "chat_sessions", ["is_private"])
    op.create_index(
        "ix_active_private_session",
        "chat_sessions",
        ["telegram_user_id", "is_private", "status"],
    )

    op.add_column(
        "user_questions",
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
    )
    op.add_column(
        "user_questions",
        sa.Column("response_mode", sa.String(length=16), server_default="quick", nullable=False),
    )
    op.add_column(
        "user_questions",
        sa.Column("credits_charged", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_user_questions_request_id", "user_questions", ["request_id"], unique=True)

    op.add_column(
        "ai_usage_events",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "ai_usage_events", sa.Column("response_mode", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "ai_usage_events",
        sa.Column("attempt_number", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("ai_usage_events", sa.Column("transport", sa.String(length=64), nullable=True))
    op.add_column("ai_usage_events", sa.Column("model_name", sa.String(length=128), nullable=True))
    op.create_index("ix_ai_usage_events_request_id", "ai_usage_events", ["request_id"])

    op.create_table(
        "credit_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("balance", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved", sa.Integer(), server_default="0", nullable=False),
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
        sa.UniqueConstraint("telegram_user_id"),
    )
    op.create_index("ix_credit_accounts_telegram_user_id", "credit_accounts", ["telegram_user_id"])

    op.create_table(
        "credit_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transaction_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="committed", nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("admin_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "transaction_type", name="uq_credit_request_type"),
    )
    op.create_index(
        "ix_credit_transactions_telegram_user_id",
        "credit_transactions",
        ["telegram_user_id"],
    )
    op.create_index("ix_credit_transactions_request_id", "credit_transactions", ["request_id"])

    op.create_table(
        "access_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("old_status", sa.String(length=32), nullable=True),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("admin_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_access_events_telegram_user_id", "access_events", ["telegram_user_id"])

    op.create_table(
        "user_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["question_id"], ["user_questions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id", "question_id", name="uq_feedback_user_question"),
    )
    op.create_index("ix_user_feedback_telegram_user_id", "user_feedback", ["telegram_user_id"])
    op.create_index("ix_user_feedback_question_id", "user_feedback", ["question_id"])


def downgrade() -> None:
    op.drop_table("user_feedback")
    op.drop_table("access_events")
    op.drop_table("credit_transactions")
    op.drop_table("credit_accounts")
    op.drop_index("ix_ai_usage_events_request_id", table_name="ai_usage_events")
    for column in ("model_name", "transport", "attempt_number", "response_mode", "request_id"):
        op.drop_column("ai_usage_events", column)
    op.drop_index("ix_user_questions_request_id", table_name="user_questions")
    for column in ("credits_charged", "response_mode", "request_id"):
        op.drop_column("user_questions", column)
    op.drop_index("ix_active_private_session", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_is_private", table_name="chat_sessions")
    for column in ("closed_at", "summary", "is_private"):
        op.drop_column("chat_sessions", column)
    op.drop_index("ix_telegram_users_access_status", table_name="telegram_users")
    for column in (
        "approved_by",
        "approved_at",
        "profile_facts",
        "memory_summary",
        "response_mode",
        "access_status",
    ):
        op.drop_column("telegram_users", column)
