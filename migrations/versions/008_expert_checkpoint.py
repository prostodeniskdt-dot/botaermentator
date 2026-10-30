"""Durable expert fragments and finalization context; existing rows stay unchanged."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "008_expert_checkpoint"
down_revision = "007_answer_expansions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_questions",
        sa.Column("generation_state", pg.JSONB(), server_default="{}", nullable=False),
    )


def downgrade():
    op.drop_column("user_questions", "generation_state")
