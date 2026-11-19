"""Explicit owner request: finite 100-credit balance for Telegram user 392172803."""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "014_podernik_finite_credits"
down_revision = "013_registration_email"
branch_labels = None
depends_on = None

TARGET_ID = 392172803
ADMIN_ID = 1221087257
REQUEST_ID = uuid.uuid5(uuid.NAMESPACE_URL, "fermentator:podernik-finite-100:2026-11-19")


def upgrade():
    db = op.get_bind()
    user = db.execute(
        sa.text(
            "SELECT telegram_user_id FROM telegram_users WHERE telegram_user_id=:uid FOR UPDATE"
        ),
        {"uid": TARGET_ID},
    ).first()
    if user is None:
        return
    applied = db.execute(
        sa.text(
            "SELECT id FROM credit_transactions WHERE request_id=:request "
            "AND transaction_type='adjustment'"
        ),
        {"request": REQUEST_ID},
    ).first()
    if applied is None:
        db.execute(
            sa.text(
                "INSERT INTO credit_accounts(id,telegram_user_id,balance,reserved) "
                "VALUES(:id,:uid,0,0) ON CONFLICT (telegram_user_id) DO NOTHING"
            ),
            {"id": uuid.uuid4(), "uid": TARGET_ID},
        )
        account = db.execute(
            sa.text(
                "SELECT balance,reserved FROM credit_accounts "
                "WHERE telegram_user_id=:uid FOR UPDATE"
            ),
            {"uid": TARGET_ID},
        ).one()
        if account.reserved > 100:
            raise RuntimeError("Active credit reservations exceed 100; retry after completion")
        db.execute(
            sa.text(
                "INSERT INTO credit_transactions(id,telegram_user_id,request_id,transaction_type,"
                "status,amount,balance_after,reason,admin_telegram_user_id) "
                "VALUES(:id,:uid,:request,'adjustment','committed',:delta,100,"
                "'owner_requested_podernik_finite_100_2026_11_19',:admin)"
            ),
            {
                "id": uuid.uuid4(),
                "uid": TARGET_ID,
                "request": REQUEST_ID,
                "delta": 100 - account.balance,
                "admin": ADMIN_ID,
            },
        )
        db.execute(
            sa.text(
                "UPDATE credit_accounts SET balance=100,updated_at=now() "
                "WHERE telegram_user_id=:uid"
            ),
            {"uid": TARGET_ID},
        )
    # Also override implicit administrator exemption. Never change email or access status.
    db.execute(
        sa.text(
            "UPDATE telegram_users SET is_unlimited=false,admin_credits_limited=true,"
            "updated_at=now() "
            "WHERE telegram_user_id=:uid"
        ),
        {"uid": TARGET_ID},
    )


def downgrade():
    # Preserve the financial correction and its marker: upgrade cannot reset subsequent spending.
    pass
