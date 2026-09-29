#!/bin/sh
set -eu

PORT="${PORT:-${APP_PORT:-8080}}"

if [ -n "${DATABASE_URL:-}" ]; then
  echo "start.sh: running alembic upgrade head"
  alembic upgrade head
fi

echo "start.sh: launching uvicorn on 0.0.0.0:${PORT} (module app.main:app)"

exec python -m uvicorn app.main:app --host "0.0.0.0" --port "${PORT}"
