#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/reset980/epubia"
cd "$PROJECT_DIR"

export PORT="${PORT:-5010}"
exec "$PROJECT_DIR/venv/bin/gunicorn" app:app \
  --bind "127.0.0.1:${PORT}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout 300 \
  --access-logfile - \
  --error-logfile -

