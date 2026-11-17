"""Correct the owner ID explicitly supplied by the owner after revision 011."""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "012_correct_owner_account"
down_revision = "011_admin_finite_balance"
branch_labels = None
depends_on = None

OWNER_ID = 1221087257
MISTAKEN_ID = 392172803
EMAIL = "prostodeniskdt@gmail.com"
PREVIOUS_REQUEST = uuid.uuid5(uuid.NAMESPACE_URL, "fermentator:admin-finite-balance:2026-11-17")
REQUEST = uuid.uuid5(uuid.NAMESPACE_URL, "fermentator:correct-owner:1221087257:2026-11-17")
REVERSAL_REQUEST = uuid.uuid5(uuid.NAMESPACE_URL, "fermentator:reverse-mistaken-owner:2026-11-17")


def upgrade():
    db = op.get_bind()
    db.execute(
        sa.text(
            "ALTER TABLE telegram_users ADD COLUMN IF NOT EXISTS "
            "admin_credits_limited boolean NOT NULL DEFAULT false"
        )
    )
    if db.execute(
        sa.text(
            "SELECT id FROM credit_transactions WHERE request_id=:request "
            "AND transaction_type='adjustment'"
        ),
        {"request": REQUEST},
    ).first():
        db.execute(
            sa.text(
                "UPDATE telegram_users SET admin_credits_limited=true WHERE telegram_user_id=:uid"
            ),
            {"uid": OWNER_ID},
        )
        return
    # Lock both accounts in a stable order. Do not create fictitious Telegram users.
    users = (
        db.execute(
            sa.text(
                "SELECT telegram_user_id FROM telegram_users WHERE telegram_user_id IN "
                "(:owner,:wrong) "
                "ORDER BY telegram_user_id FOR UPDATE"
            ),
            {"owner": OWNER_ID, "wrong": MISTAKEN_ID},
        )
        .scalars()
        .all()
    )
    if OWNER_ID not in users:
        return
    previous = db.execute(
        sa.text(
            "SELECT amount FROM credit_transactions WHERE request_id=:request "
            "AND transaction_type='adjustment' AND telegram_user_id=:wrong"
        ),
        {"request": PREVIOUS_REQUEST, "wrong": MISTAKEN_ID},
    ).first()
    reservation = db.execute(
        sa.text(
            "SELECT telegram_user_id FROM email_reservations WHERE "
            "email_normalized=:email FOR UPDATE"
        ),
        {"email": EMAIL},
    ).scalar_one_or_none()
    if reservation not in (None, OWNER_ID):
        if reservation != MISTAKEN_ID or previous is None:
            raise RuntimeError("Owner email is reserved by an unrelated account")
        # This is an explicit correction of 011, not general email reuse.
        db.execute(
            sa.text(
                "UPDATE telegram_users SET email=NULL,email_normalized=NULL,"
                "email_registered_at=now(),email_registered_by=:owner,updated_at=now() "
                "WHERE telegram_user_id=:wrong AND email_normalized=:email"
            ),
            {"owner": OWNER_ID, "wrong": MISTAKEN_ID, "email": EMAIL},
        )
        db.execute(
            sa.text(
                "UPDATE email_reservations SET telegram_user_id=:owner WHERE "
                "email_normalized=:email"
            ),
            {"owner": OWNER_ID, "email": EMAIL},
        )
        # Existing orders retain their original recipient and cannot silently move.
        db.execute(
            sa.text(
                "UPDATE purchase_orders SET status='manual_review',"
                "error_code='owner_identity_corrected' WHERE recipient_id=:wrong "
                "AND email_normalized=:email AND credited_at IS NULL"
            ),
            {"wrong": MISTAKEN_ID, "email": EMAIL},
        )
    elif reservation is None:
        db.execute(
            sa.text(
                "INSERT INTO email_reservations(email_normalized,telegram_user_id) "
                "VALUES(:email,:owner)"
            ),
            {"email": EMAIL, "owner": OWNER_ID},
        )
    if previous is not None:
        account = db.execute(
            sa.text(
                "SELECT balance,reserved FROM credit_accounts WHERE "
                "telegram_user_id=:wrong FOR UPDATE"
            ),
            {"wrong": MISTAKEN_ID},
        ).one()
        restored_balance = account.balance - previous.amount
        if restored_balance < account.reserved:
            raise RuntimeError("Mistaken-account reversal requires manual reconciliation")
        adjustment(
            db,
            MISTAKEN_ID,
            -previous.amount,
            restored_balance,
            REVERSAL_REQUEST,
            "reverse_mistaken_admin_correction_011",
        )
        db.execute(
            sa.text(
                "UPDATE credit_accounts SET balance=:balance,updated_at=now() WHERE "
                "telegram_user_id=:uid"
            ),
            {"balance": restored_balance, "uid": MISTAKEN_ID},
        )
        db.execute(
            sa.text(
                "UPDATE telegram_users SET is_unlimited=true,updated_at=now() WHERE "
                "telegram_user_id=:uid"
            ),
            {"uid": MISTAKEN_ID},
        )
    db.execute(
        sa.text(
            "INSERT INTO credit_accounts(id,telegram_user_id,balance,reserved) "
            "VALUES(:id,:uid,0,0) ON CONFLICT (telegram_user_id) DO NOTHING"
        ),
        {"id": uuid.uuid4(), "uid": OWNER_ID},
    )
    account = db.execute(
        sa.text(
            "SELECT balance,reserved FROM credit_accounts WHERE telegram_user_id=:uid FOR UPDATE"
        ),
        {"uid": OWNER_ID},
    ).one()
    if account.reserved > 100:
        raise RuntimeError("Owner credit reservations exceed 100; retry after completion")
    adjustment(
        db,
        OWNER_ID,
        100 - account.balance,
        100,
        REQUEST,
        "owner_confirmed_id_finite_balance_2026_11_17",
    )
    db.execute(
        sa.text(
            "UPDATE credit_accounts SET balance=100,updated_at=now() WHERE telegram_user_id=:uid"
        ),
        {"uid": OWNER_ID},
    )
    db.execute(
        sa.text(
            "UPDATE telegram_users SET is_unlimited=false,admin_credits_limited=true,"
            "email=:email,email_normalized=:email,"
            "email_registered_at=now(),email_registered_by=:uid,updated_at=now() "
            "WHERE telegram_user_id=:uid"
        ),
        {"uid": OWNER_ID, "email": EMAIL},
    )


def adjustment(db, user_id, delta, balance, request, reason):
    db.execute(
        sa.text(
            "INSERT INTO "
            "credit_transactions(id,telegram_user_id,request_id,transaction_type,status,"
            "amount,balance_after,reason,admin_telegram_user_id) "
            "VALUES(:id,:uid,:request,'adjustment','committed',:delta,:balance,:reason,:admin)"
        ),
        {
            "id": uuid.uuid4(),
            "uid": user_id,
            "request": request,
            "delta": delta,
            "balance": balance,
            "reason": reason,
            "admin": OWNER_ID,
        },
    )


def downgrade():
    # Preserve financial corrections and ledger markers.
    op.drop_column("telegram_users", "admin_credits_limited")
