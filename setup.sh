#!/usr/bin/env bash
# One-command local setup for AEGIS Legal CLM.
# Builds and starts the full Docker stack (Postgres, Redis, API, worker, beat,
# frontend), waits for it to be healthy, and seeds the default admin.
# Idempotent — safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }

# 1. Prerequisites -----------------------------------------------------------
command -v docker >/dev/null || { echo "Docker is not installed — see https://docs.docker.com/get-docker/"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required (comes with Docker Desktop)."; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker isn't running — start Docker Desktop / the daemon and re-run."; exit 1; }

# 2. Config (keyless local defaults, integration mocks ON) -------------------
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  say "Created backend/.env from backend/.env.example (mocks ON — no API keys needed)."
fi

# 3. Build + start. Migrations run automatically (the one-shot 'migrate'
#    service gates the backend via depends_on). ---------------------------
say "Building and starting the stack (first run pulls images + builds — a few minutes)…"
docker compose up -d --build

# 4. Wait for the API to report healthy -------------------------------------
say "Waiting for the API to become healthy…"
for i in $(seq 1 60); do
  if curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then ok=1; break; fi
  sleep 3
done
[ "${ok:-}" = 1 ] || { echo "API didn't come up in time. Check logs: docker compose logs backend migrate"; exit 1; }

# 5. Seed the default admin (no-op if it already exists) --------------------
say "Seeding the default admin…"
docker compose exec -T backend python -m app.devtools seed || \
  echo "  (seed reported nonzero — the admin most likely already exists; continuing)"

# 6. Done -------------------------------------------------------------------
cat <<'DONE'

✅ AEGIS is running.

   Frontend   http://localhost:3000
   API        http://localhost:8000  (health /healthz, docs /docs)

   Login      admin@example.com / local-dev-password

   Logs       docker compose logs -f
   Stop       docker compose down          (keeps your data)
   Reset      docker compose down -v        (deletes all data)

DONE
