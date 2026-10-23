"""Short-transaction leases, durable notices and per-part delivery progress."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "006_processing_delivery"
down_revision = "005_unlimited_access"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_processing_leases",
        sa.Column("telegram_user_id", sa.BigInteger(), primary_key=True),
        sa.Column("owner", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_user_processing_leases_expires_at", "user_processing_leases", ["expires_at"]
    )
    op.create_table(
        "task_notices",
        sa.Column("request_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(24), server_default="active", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "telegram_delivery_parts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "delivery_job_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("telegram_delivery_jobs.id"),
            nullable=False,
        ),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("state", sa.String(24), server_default="pending", nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("delivery_job_id", "part_index", name="uq_delivery_part"),
    )
    op.create_index(
        "ix_telegram_delivery_parts_delivery_job_id", "telegram_delivery_parts", ["delivery_job_id"]
    )
    op.add_column(
        "telegram_update_jobs",
        sa.Column("lane", sa.String(16), server_default="question", nullable=False),
    )
    op.create_index(
        "ix_update_jobs_lane_claim", "telegram_update_jobs", ["lane", "status", "available_at"]
    )
    # Classify old pending messages without rewriting their payloads.
    op.execute(
        "UPDATE telegram_update_jobs SET lane = 'service' WHERE status = 'pending' AND ("
        "payload->'message'->>'text' ~ "
        "'^/(start|help|new|quick|deep|mode|balance|privacy|remember|profile|forget)"
        "(@[^ ]+)?( |$)' OR payload->'message'->>'text' IN "
        "('Кратко','Подробно','Новая тема','Баланс','Профиль','Помощь') OR "
        "payload->'callback_query'->>'data' LIKE 'forget:%')"
    )


def downgrade():
    op.drop_index("ix_update_jobs_lane_claim", table_name="telegram_update_jobs")
    op.drop_column("telegram_update_jobs", "lane")
    op.drop_table("telegram_delivery_parts")
    op.drop_table("task_notices")
    op.drop_table("user_processing_leases")
