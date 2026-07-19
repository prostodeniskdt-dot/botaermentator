#!/bin/sh
set -eu

echo "entrypoint: starting FastAPI with: $*"
echo "entrypoint: module must be app.main:app (package dir is app/, not botpodergun)"

exec "$@"
