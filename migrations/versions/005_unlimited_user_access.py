"""Add per-user unlimited credit access.

Revision ID: 005_unlimited_access
Revises: 004_update_queue
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005_unlimited_access"
down_revision: str | None = "004_update_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNLIMITED_TELEGRAM_USER_ID = 392172803


def upgrade() -> None:
    op.add_column(
        "telegram_users",
        sa.Column(
            "is_unlimited",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE telegram_users "
            "SET is_unlimited = true "
            "WHERE telegram_user_id = :telegram_user_id"
        ).bindparams(telegram_user_id=UNLIMITED_TELEGRAM_USER_ID)
    )


def downgrade() -> None:
    op.drop_column("telegram_users", "is_unlimited")
