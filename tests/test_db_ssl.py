"""PostgreSQL SSL connect args tests."""

from __future__ import annotations

import ssl

from app.db.ssl import build_asyncpg_ssl_connect_args


def test_ssl_disabled_when_not_required() -> None:
    assert build_asyncpg_ssl_connect_args(ssl_required=False) == {}


def test_ssl_uses_insecure_context_for_timeweb_without_cert() -> None:
    args = build_asyncpg_ssl_connect_args(
        ssl_required=True,
        hostname="be268ca92e5d53a93fd0cac8.twc1.net",
    )
    context = args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_NONE
