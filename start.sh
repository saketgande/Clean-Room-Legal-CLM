#!/usr/bin/env bash
#
# start.sh - run AEGIS Legal CLM locally without Docker.
#
# Requirements on the host:
#   - PostgreSQL with pgvector, reachable on localhost:5432
#   - Redis, reachable on localhost:6379
#   - Python 3 + venv support
#   - Node.js + npm
#   - libmagic1
#
# Usage:
#   ENVIRONMENT=development ./start.sh              start API, worker, beat, and frontend
#   ENVIRONMENT=development REINSTALL=1 ./start.sh  refresh Python/npm dependencies
#   ./start.sh backend      start API, worker, and beat
#   ./start.sh frontend     start only the frontend

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
TARGET="${1:-all}"

: "${ENVIRONMENT:?Set ENVIRONMENT=development for local/sandbox or production for live deploys}"
export DEBUG="${DEBUG:-true}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://legal_clm:legal_clm@localhost:5432/legal_clm}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export STORAGE_ROOT="${STORAGE_ROOT:-$BACKEND/.local-contract-storage}"
export APP_BASE_URL="${APP_BASE_URL:-http://localhost:3000}"
export NEXT_PUBLIC_API_BASE_URL="${NEXT_PUBLIC_API_BASE_URL:-http://localhost:8000/api/v1}"

PIDS=()
cleanup() {
  echo ""
  echo "[start.sh] stopping app processes..."
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "[start.sh] done. PostgreSQL and Redis were left running."
}
trap cleanup EXIT INT TERM

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[start.sh] missing required tool: $1" >&2
    exit 1
  }
}

check_infra() {
  need pg_isready
  need redis-cli
  echo "[start.sh] checking PostgreSQL..."
  pg_isready -h localhost -p 5432 >/dev/null
  echo "[start.sh] checking Redis..."
  redis-cli -u "$REDIS_URL" ping >/dev/null
}

start_backend() {
  need python3
  check_infra
  cd "$BACKEND"
  mkdir -p "$STORAGE_ROOT"

  if [ ! -d .venv ]; then
    echo "[start.sh] creating backend venv..."
    python3 -m venv .venv
    ./.venv/bin/pip install -U pip >/dev/null
  fi

  if [ "${REINSTALL:-0}" = "1" ] || \
     ! ./.venv/bin/python -c "import slowapi, jwt, tenacity" >/dev/null 2>&1; then
    echo "[start.sh] installing/refreshing backend deps..."
    ./.venv/bin/pip install -e . >/dev/null
  fi

  # shellcheck disable=SC1091
  source .venv/bin/activate

  if ! python -c "import magic" >/dev/null 2>&1; then
    echo "[start.sh] ERROR: libmagic not found (needed by python-magic)." >&2
    echo "[start.sh]   Ubuntu fix: sudo apt-get install -y libmagic1" >&2
    exit 1
  fi

  echo "[start.sh] applying migrations..."
  alembic upgrade head

  echo "[start.sh] starting API on http://localhost:8000 ..."
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload & PIDS+=($!)

  echo "[start.sh] starting Celery worker..."
  celery -A app.jobs.celery_app.celery_app worker --loglevel=info --concurrency="${CELERY_CONCURRENCY:-4}" & PIDS+=($!)

  echo "[start.sh] starting Celery beat..."
  celery -A app.jobs.celery_app.celery_app beat --loglevel=info --schedule="$BACKEND/.local-celerybeat-schedule" & PIDS+=($!)
}

start_frontend() {
  need npm
  cd "$FRONTEND"
  if [ ! -d node_modules ] || [ "${REINSTALL:-0}" = "1" ]; then
    echo "[start.sh] installing frontend deps..."
    npm install
  fi
  echo "[start.sh] starting frontend on http://localhost:3000 ..."
  npm run dev & PIDS+=($!)
}

case "$TARGET" in
  backend)  start_backend ;;
  frontend) start_frontend ;;
  all)      start_backend; start_frontend ;;
  *) echo "usage: ./start.sh [all|backend|frontend]" >&2; exit 1 ;;
esac

echo ""
echo "[start.sh] up. API: http://localhost:8000  Frontend: http://localhost:3000"
echo "[start.sh] press Ctrl-C to stop."
wait
