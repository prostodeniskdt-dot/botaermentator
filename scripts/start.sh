#!/bin/sh
set -eu

PORT="${PORT:-${APP_PORT:-8080}}"

echo "start.sh: launching uvicorn on [::]:${PORT} (module app.main:app)"

# Bind on :: so Timeweb probes to localhost (often IPv6 ::1) reach the app.
exec python -m uvicorn app.main:app --host "::" --port "${PORT}"
