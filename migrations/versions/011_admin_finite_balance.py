"""Owner-requested account correction for the formerly unlimited administrator.

Applied once by Alembic; does not change other administrators or future balances.
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "011_admin_finite_balance"
down_revision = "010_email_payments"
branch_labels = None
depends_on = None

TARGET_USER_ID = 392172803
TARGET_EMAIL = "prostodeniskdt@gmail.com"
REQUEST_ID = uuid.uuid5(uuid.NAMESPACE_URL, "fermentator:admin-finite-balance:2026-11-17")


def upgrade():
    db = op.get_bind()
    applied = db.execute(
        sa.text(
            "SELECT id FROM credit_transactions WHERE request_id=:request "
            "AND transaction_type='adjustment'"
        ),
        {"request": REQUEST_ID},
    ).first()
    if applied:
        return
    user = db.execute(
        sa.text(
            "SELECT telegram_user_id FROM telegram_users WHERE telegram_user_id=:uid FOR UPDATE"
        ),
        {"uid": TARGET_USER_ID},
    ).first()
    if user is None:
        # No fictitious Telegram users on fresh installations.
        return
    db.execute(
        sa.text(
            "INSERT INTO email_reservations(email_normalized,telegram_user_id) "
            "VALUES(:email,:uid) ON CONFLICT DO NOTHING"
        ),
        {"email": TARGET_EMAIL, "uid": TARGET_USER_ID},
    )
    owner = db.execute(
        sa.text("SELECT telegram_user_id FROM email_reservations WHERE email_normalized=:email"),
        {"email": TARGET_EMAIL},
    ).scalar_one()
    if owner != TARGET_USER_ID:
        raise RuntimeError("Requested administrator email is reserved by another account")
    db.execute(
        sa.text(
            "INSERT INTO credit_accounts(id,telegram_user_id,balance,reserved) "
            "VALUES(:id,:uid,0,0) ON CONFLICT (telegram_user_id) DO NOTHING"
        ),
        {"id": uuid.uuid4(), "uid": TARGET_USER_ID},
    )
    account = db.execute(
        sa.text(
            "SELECT balance,reserved FROM credit_accounts WHERE telegram_user_id=:uid FOR UPDATE"
        ),
        {"uid": TARGET_USER_ID},
    ).one()
    if account.reserved > 100:
        raise RuntimeError(
            "Active credit reservations exceed requested balance; retry after completion"
        )
    db.execute(
        sa.text(
            "INSERT INTO credit_transactions"
            "(id,telegram_user_id,request_id,transaction_type,status,amount,balance_after,reason,"
            "admin_telegram_user_id) VALUES(:id,:uid,:request,'adjustment','committed',:delta,100,"
            "'owner_requested_finite_balance_2026_11_17',:uid)"
        ),
        {
            "id": uuid.uuid4(),
            "uid": TARGET_USER_ID,
            "request": REQUEST_ID,
            "delta": 100 - account.balance,
        },
    )
    db.execute(
        sa.text(
            "UPDATE credit_accounts SET balance=100,updated_at=now() WHERE telegram_user_id=:uid"
        ),
        {"uid": TARGET_USER_ID},
    )
    db.execute(
        sa.text(
            "UPDATE telegram_users SET is_unlimited=false,email=:email,email_normalized=:email,"
            "email_registered_at=now(),email_registered_by=:uid,updated_at=now() "
            "WHERE telegram_user_id=:uid"
        ),
        {"uid": TARGET_USER_ID, "email": TARGET_EMAIL},
    )


def downgrade():
    # Preserve the correction and its ledger marker. A later upgrade detects REQUEST_ID
    # and cannot reset credits already spent since the original correction.
    pass
