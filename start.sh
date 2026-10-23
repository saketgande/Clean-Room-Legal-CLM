#!/usr/bin/env bash
#
# start.sh - run AEGIS Legal CLM locally without Docker.
#
# Requirements on the host:
#   - Docker (for Postgres+pgvector and Redis, started automatically), or your
#     own Postgres on localhost:5432 and Redis on localhost:6379
#   - Python >=3.11 + venv support
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

# backend/.env is written for the Docker stack (ENVIRONMENT=production, and
# DATABASE_URL/REDIS_URL pointing at the compose service names). Real env vars
# outrank the env file in pydantic-settings, so the exports above already
# redirect the app at localhost. These mirror docker-compose.override.yml's
# x-dev-app-env so a native dev run behaves like `make dev` instead of booting
# with production hardening and RBAC on.
if [ "$ENVIRONMENT" = "development" ]; then
  export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}"
  export ALLOWED_HOSTS="${ALLOWED_HOSTS:-*}"
  export FORCE_HTTPS="${FORCE_HTTPS:-false}"
  export REFRESH_COOKIE_SECURE="${REFRESH_COOKIE_SECURE:-false}"
  export EXPOSE_REFRESH_TOKEN_IN_BODY="${EXPOSE_REFRESH_TOKEN_IN_BODY:-true}"
  export DISABLE_RBAC="${DISABLE_RBAC:-true}"
  export INTAKE_DEMO_AGENTS="${INTAKE_DEMO_AGENTS:-true}"
fi

PIDS=()
cleanup() {
  echo ""
  echo "[start.sh] stopping app processes..."
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "[start.sh] done. PostgreSQL and Redis were left running (docker compose stop postgres redis)."
}
trap cleanup EXIT INT TERM

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[start.sh] missing required tool: $1" >&2
    exit 1
  }
}

# bash's /dev/tcp, so neither psql nor redis-cli has to be installed on the
# host just to answer "is the port open?".
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

wait_for_port() {
  for _ in $(seq 1 "${2:-30}"); do
    port_open "$1" && return 0
    sleep 1
  done
  return 1
}

# Postgres needs the pgvector extension and Redis needs nothing special, so
# rather than make the host install and manage both, reuse the two containers
# the compose file already defines (with their existing pgdata/redisdata
# volumes). Only these two services are started; the app itself runs here.
start_infra() {
  local missing=()
  port_open 5432 || missing+=(postgres)
  port_open 6379 || missing+=(redis)
  if [ ${#missing[@]} -eq 0 ]; then
    echo "[start.sh] PostgreSQL and Redis already reachable."
    return
  fi
  need docker
  echo "[start.sh] starting ${missing[*]} in Docker..."
  (cd "$ROOT" && docker compose up -d "${missing[@]}")
  for service in "${missing[@]}"; do
    case "$service" in
      postgres) wait_for_port 5432 || { echo "[start.sh] PostgreSQL never came up on 5432" >&2; exit 1; } ;;
      redis)    wait_for_port 6379 || { echo "[start.sh] Redis never came up on 6379" >&2; exit 1; } ;;
    esac
  done
  echo "[start.sh] infra ready."
}

start_backend() {
  need python3
  start_infra
  cd "$BACKEND"
  mkdir -p "$STORAGE_ROOT"

  if [ ! -d .venv ]; then
    # pyproject requires >=3.11 and macOS still ships 3.9 as `python3`, so pick
    # a new-enough interpreter rather than building a venv `pip install -e .`
    # will then refuse. Override with PYTHON=/path/to/python3.x.
    local py="${PYTHON:-}"
    if [ -z "$py" ]; then
      for candidate in python3.12 python3.11 python3.13 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && \
           "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
          py="$candidate"
          break
        fi
      done
    fi
    [ -n "$py" ] || { echo "[start.sh] no Python >=3.11 found; set PYTHON=/path/to/python3.12" >&2; exit 1; }
    echo "[start.sh] creating backend venv with $py ($($py -V))..."
    "$py" -m venv .venv
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

  # macOS defaults multiprocessing to "spawn", and celery's fast_trace_task
  # reads a module global that only a *forked* child inherits — so every task
  # dies with "not enough values to unpack (expected 3, got 0)" before reaching
  # its handler. Linux (Docker, systemd) forks and is unaffected, so this is a
  # host-launcher workaround, not a code change: the threads pool skips billiard
  # entirely. Tasks here are DB/HTTP-bound, so threads cost little.
  # ponytail: threads, not fork. Revisit if a task turns CPU-bound enough that
  # the GIL shows up in local runs.
  local pool="${CELERY_POOL:-}"
  if [ -z "$pool" ]; then
    [ "$(uname -s)" = "Darwin" ] && pool=threads || pool=prefork
  fi

  echo "[start.sh] starting Celery worker (pool=$pool)..."
  celery -A app.jobs.celery_app.celery_app worker --loglevel=info \
    --pool="$pool" --concurrency="${CELERY_CONCURRENCY:-4}" & PIDS+=($!)

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
