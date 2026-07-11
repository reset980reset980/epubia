#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

export PORT="${PORT:-5010}"
exec "$PROJECT_DIR/venv/bin/gunicorn" app:app \
  --bind "127.0.0.1:${PORT}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout 300 \
  --access-logformat '%(h)s %(t)s "%(m)s %(H)s" %(s)s %(b)s %(L)s "%(a)s"' \
  --access-logfile - \
  --error-logfile -
