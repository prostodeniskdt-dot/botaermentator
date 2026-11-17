"""Email registration and durable payment intake; existing ledgers remain untouched."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "010_email_payments"
down_revision = "009_editorial_delivery"
branch_labels = None
depends_on = None


def upgrade():
    for column in [
        sa.Column("email", sa.String(254)),
        sa.Column("email_normalized", sa.String(254)),
        sa.Column("email_registered_at", sa.DateTime(timezone=True)),
        sa.Column("email_registered_by", sa.BigInteger()),
    ]:
        op.add_column("telegram_users", column)
    op.create_unique_constraint(
        "uq_telegram_users_email_normalized", "telegram_users", ["email_normalized"]
    )
    op.create_table(
        "email_reservations",
        sa.Column("email_normalized", sa.String(254), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_key", sa.String(64), nullable=False, unique=True),
        sa.Column("payment_id", sa.String(64), unique=True),
        sa.Column("order_id", sa.String(128)),
        sa.Column("lead_id", sa.String(128)),
        sa.Column("email_normalized", sa.String(254)),
        sa.Column("recipient_id", sa.BigInteger()),
        sa.Column("details", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("provider_checked_at", sa.DateTime(timezone=True)),
        sa.Column("credited_at", sa.DateTime(timezone=True)),
        sa.Column("credit_transaction_id", pg.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "payment_notices",
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id", pg.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id"), nullable=False
        ),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("order_id", "recipient_id", "kind"),
    )


def downgrade():
    op.drop_table("payment_notices")
    op.drop_table("purchase_orders")
    op.drop_table("email_reservations")
    op.drop_constraint("uq_telegram_users_email_normalized", "telegram_users", type_="unique")
    for name in ["email_registered_by", "email_registered_at", "email_normalized", "email"]:
        op.drop_column("telegram_users", name)
