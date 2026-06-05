#!/bin/sh
# Container entrypoint. POSIX sh (compose calls: sh /app/scripts/entrypoint.sh).
#
# Adds a few production startup guards in front of the existing migrate+exec
# behaviour. All guards no-op in local/dev so the dev compose and start.sh flows
# are unchanged.
set -e

# Normalise the environment name (default matches Settings.environment = "local").
ENV_NAME="$(echo "${ENVIRONMENT:-local}" | tr '[:upper:]' '[:lower:]')"
STORAGE_ROOT="${STORAGE_ROOT:-/data/contracts}"

is_production() {
  case "$ENV_NAME" in
    local|development|dev|test) return 1 ;;
    *) return 0 ;;
  esac
}

# --- Guard 1: refuse the weak default Postgres password in production ---------
# 'legal_clm' is the documented local/dev password; shipping it to a real
# environment would be a critical credential. Refuse to start instead.
if is_production && [ "${POSTGRES_PASSWORD:-}" = "legal_clm" ]; then
  echo "[entrypoint] FATAL: POSTGRES_PASSWORD is the weak default 'legal_clm' in" \
       "a non-local environment (ENVIRONMENT=$ENV_NAME)." >&2
  echo "[entrypoint]   Set a strong POSTGRES_PASSWORD (see DEPLOY.md: openssl rand)." >&2
  exit 1
fi

# --- Guard 2: storage root must exist and be writable -------------------------
# Contract files are written here (Settings.storage_root). A read-only or
# missing mount is a silent data-loss trap, so fail fast with a clear message.
mkdir -p "$STORAGE_ROOT" 2>/dev/null || true
if [ ! -w "$STORAGE_ROOT" ]; then
  echo "[entrypoint] FATAL: storage root '$STORAGE_ROOT' is not writable by" \
       "$(id -un) (uid $(id -u))." >&2
  echo "[entrypoint]   Check the contract_file_storage volume mount / ownership." >&2
  exit 1
fi

# --- Migrations ---------------------------------------------------------------
# In local/dev we keep the convenient auto-migrate-on-boot. In production,
# migrations are a deliberate, separate release step (see DEPLOY.md) so multiple
# api/worker replicas don't race alembic on startup.
if is_production; then
  echo "[entrypoint] production: skipping auto-migrate." \
       "Run 'docker compose run --rm api alembic upgrade head' as a release step."
else
  echo "[entrypoint] applying database migrations (alembic upgrade head)..."
  alembic upgrade head
  echo "[entrypoint] migrations applied."
fi

echo "[entrypoint] starting: $*"
exec "$@"
