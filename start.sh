#!/usr/bin/env bash
#
# start.sh — run AEGIS Legal CLM locally with one command.
#
#   Postgres + Redis : Docker (host-exposed, gives you pgvector for free)
#   Backend API+worker: bare-metal in a venv
#   Frontend          : Next.js dev server
#
# Usage:
#   ./start.sh              start everything
#   REINSTALL=1 ./start.sh  force re-install of python/npm deps
#   ./start.sh backend      start only Postgres/Redis + API + worker
#   ./start.sh frontend     start only the frontend
#
# Ctrl-C stops the app processes. Postgres/Redis stay up (data persists);
# stop them with:  (cd backend && docker compose stop postgres redis)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
TARGET="${1:-all}"

# --- local dev settings (process env overrides .env, non-destructive) -------
export ENVIRONMENT="local"
export DEBUG="true"
export DATABASE_URL="postgresql+psycopg://legal_clm:legal_clm@localhost:5432/legal_clm"
export REDIS_URL="redis://localhost:6379/0"

PIDS=()
cleanup() {
  echo ""
  echo "[start.sh] stopping app processes…"
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "[start.sh] done. Postgres/Redis left running."
  echo "           stop them with: (cd backend && docker compose stop postgres redis)"
}
trap cleanup EXIT INT TERM

need() { command -v "$1" >/dev/null 2>&1 || { echo "[start.sh] missing required tool: $1" >&2; exit 1; }; }

start_infra() {
  need docker
  echo "[start.sh] starting Postgres + Redis (Docker)…"
  ( cd "$BACKEND" && docker compose up -d postgres redis \
      && docker compose stop api worker >/dev/null 2>&1 || true )
  echo "[start.sh] waiting for Postgres…"
  for _ in $(seq 1 30); do
    if ( cd "$BACKEND" && docker compose exec -T postgres pg_isready -U legal_clm -d legal_clm >/dev/null 2>&1 ); then
      echo "[start.sh] Postgres ready."; return 0
    fi
    sleep 2
  done
  echo "[start.sh] Postgres did not become ready in time" >&2; exit 1
}

start_backend() {
  need python3
  cd "$BACKEND"
  if [ ! -d .venv ]; then
    echo "[start.sh] creating venv…"
    python3 -m venv .venv
    ./.venv/bin/pip install -U pip >/dev/null
  fi
  # Self-healing: (re)install whenever a required dep is missing (e.g. a
  # pre-existing venv predating new deps like slowapi/pyjwt) or on REINSTALL=1.
  if [ "${REINSTALL:-0}" = "1" ] || \
     ! ./.venv/bin/python -c "import slowapi, jwt, tenacity" >/dev/null 2>&1; then
    echo "[start.sh] installing/refreshing backend deps (pip install -e .)…"
    ./.venv/bin/pip install -e . >/dev/null
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  # Preflight: python-magic needs the native libmagic C library (Docker bundles
  # it; macOS does not). Fail fast with an actionable message.
  if ! python -c "import magic" >/dev/null 2>&1; then
    echo "[start.sh] ERROR: libmagic not found (needed by python-magic for upload MIME sniffing)." >&2
    if command -v brew >/dev/null 2>&1; then
      echo "[start.sh]   fix:  brew install libmagic   then re-run ./start.sh" >&2
    else
      echo "[start.sh]   install the libmagic system library, then re-run ./start.sh" >&2
    fi
    exit 1
  fi
  echo "[start.sh] applying migrations (alembic upgrade head)…"
  alembic upgrade head
  echo "[start.sh] starting API on http://localhost:8000 …"
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload & PIDS+=($!)
  echo "[start.sh] starting Celery worker…"
  celery -A app.jobs.celery_app.celery_app worker --loglevel=info & PIDS+=($!)
}

start_frontend() {
  need npm
  cd "$FRONTEND"
  if [ ! -d node_modules ] || [ "${REINSTALL:-0}" = "1" ]; then
    echo "[start.sh] installing frontend deps (npm install)…"
    npm install
  fi
  echo "[start.sh] starting frontend on http://localhost:3000 …"
  npm run dev & PIDS+=($!)
}

case "$TARGET" in
  backend)  start_infra; start_backend ;;
  frontend) start_frontend ;;
  all)      start_infra; start_backend; start_frontend ;;
  *) echo "usage: ./start.sh [all|backend|frontend]" >&2; exit 1 ;;
esac

echo ""
echo "[start.sh] up.  API: http://localhost:8000   Frontend: http://localhost:3000"
echo "[start.sh] press Ctrl-C to stop."
wait
