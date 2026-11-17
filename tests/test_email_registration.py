import pytest

from app.services.payment_email import mask_email, normalize_email


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("  A.B+tag@Example.COM ", "a.b+tag@example.com"),
        ("name@пример.рф", "name@xn--e1afmkfd.xn--p1ai"),
    ],
)
def test_email_normalization(source, expected):
    assert normalize_email(source) == expected


@pytest.mark.parametrize(
    "email",
    ["a", "a@@x.com", ".a@x.com", "a..b@x.com", "a@-x.com", "a@x", "имя@example.com", "a b@x.com"],
)
def test_invalid_email(email):
    with pytest.raises(ValueError):
        normalize_email(email)


def test_mask_hides_domain_and_local_part():
    assert mask_email("alice@example.com") == "a***@***"


@pytest.mark.parametrize("handler_name", ["admin_email", "admin_payment", "admin_payment_resolve"])
async def test_email_and_payment_commands_reject_nonadmin(handler_name):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.bot.routers import admin

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=7), answer=AsyncMock(), text="/admin_set_email 7 a@example.com"
    )
    await getattr(admin, handler_name)(message, SimpleNamespace(admin_user_ids=[42]))
    message.answer.assert_not_awaited()
