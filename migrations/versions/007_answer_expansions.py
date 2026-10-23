"""One paid expansion per original answer, with a frozen context snapshot."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "007_answer_expansions"
down_revision = "006_processing_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "bot_responses",
        sa.Column("context_snapshot", pg.JSONB(), server_default="{}", nullable=False),
    )
    op.create_table(
        "answer_expansions",
        sa.Column(
            "source_response_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("bot_responses.id"),
            primary_key=True,
        ),
        sa.Column("request_id", pg.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column(
            "question_id", pg.UUID(as_uuid=True), sa.ForeignKey("user_questions.id"), nullable=True
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="offered"),
        sa.Column("cost", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade():
    op.drop_table("answer_expansions")
    op.drop_column("bot_responses", "context_snapshot")
