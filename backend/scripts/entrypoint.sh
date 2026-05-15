#!/bin/sh
set -e

echo "[entrypoint] applying database migrations (alembic upgrade head)..."
alembic upgrade head
echo "[entrypoint] migrations applied. starting: $*"

exec "$@"
