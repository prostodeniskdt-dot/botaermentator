"""PostgreSQL SSL connect options for asyncpg."""

from __future__ import annotations

import os
import ssl
from pathlib import Path


def build_asyncpg_ssl_connect_args(
    *,
    ssl_required: bool,
    hostname: str | None = None,
    root_cert_path: str | None = None,
) -> dict:
    """Build asyncpg connect_args for Timeweb and other managed PostgreSQL hosts."""
    if not ssl_required:
        return {}

    for cert_path in _certificate_candidates(root_cert_path, hostname):
        if cert_path.is_file():
            context = ssl.create_default_context(cafile=str(cert_path))
            return {"ssl": context}

    # Encrypted connection without CA verification (Timeweb self-signed CA).
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return {"ssl": context}


def _certificate_candidates(
    root_cert_path: str | None,
    hostname: str | None,
) -> list[Path]:
    candidates: list[Path] = []
    for value in (root_cert_path, os.environ.get("PGSSLROOTCERT")):
        if value:
            candidates.append(Path(value))

    candidates.extend(
        [
            Path("/app/certs/timeweb-root.crt"),
            Path.home() / ".cloud-certs" / "root.crt",
        ]
    )

    if hostname and hostname.endswith(".twc1.net"):
        candidates.append(Path("/etc/ssl/certs/timeweb-cloud-ca.crt"))

    return candidates
