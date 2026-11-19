"""Collect a new user's email for manual technician registration."""

import sqlalchemy as sa
from alembic import op

revision = "013_registration_email"
down_revision = "012_correct_owner_account"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy users keep their current workflow; new ORM-created users require email.
    op.add_column(
        "telegram_users",
        sa.Column(
            "registration_email_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("telegram_users", sa.Column("registration_email", sa.String(254)))
    for name in ["registration_email_confirmed_at", "registration_email_notified_at"]:
        op.add_column("telegram_users", sa.Column(name, sa.DateTime(timezone=True)))


def downgrade():
    for name in [
        "registration_email_notified_at",
        "registration_email_confirmed_at",
        "registration_email",
        "registration_email_required",
    ]:
        op.drop_column("telegram_users", name)
