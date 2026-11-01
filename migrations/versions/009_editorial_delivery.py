"""Persist renderer payloads and fallback relationships without changing existing part rows."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "009_editorial_delivery"
down_revision = "008_expert_checkpoint"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "telegram_delivery_parts",
        sa.Column("payload", pg.JSONB(), server_default="{}", nullable=False),
    )


def downgrade():
    op.drop_column("telegram_delivery_parts", "payload")
