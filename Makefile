# AEGIS Legal CLM — switch between Docker and native (no-Docker) runs.
#
#   make up            -> run everything in Docker
#   make local         -> run everything as host processes (no Docker)
#
# Run `make help` for the full list.

COMPOSE      ?= docker compose
# Production = base file only (ignores docker-compose.override.yml).
COMPOSE_PROD ?= docker compose -f docker-compose.yml

.PHONY: help dev dev-build up up-build prod prod-build down stop restart logs ps \
        migrate seed shell rebuild nuke local local-backend local-frontend

help:
	@echo "WITH Docker — DEVELOPMENT (hot reload, source bind-mounted; default):"
	@echo "  make dev          start in dev mode (= make up)"
	@echo "  make dev-build    rebuild dev images, then start"
	@echo ""
	@echo "WITH Docker — PRODUCTION (built images, no bind mounts):"
	@echo "  make prod         start in production mode"
	@echo "  make prod-build   rebuild prod images, then start"
	@echo ""
	@echo "Shared:"
	@echo "  make down         stop & remove containers (keeps data volumes)"
	@echo "  make stop         stop containers (keep them)"
	@echo "  make restart      restart all services"
	@echo "  make logs         tail logs for all services"
	@echo "  make ps           show service status"
	@echo "  make migrate      run Alembic migrations (alembic upgrade head)"
	@echo "  make seed         seed the default admin user"
	@echo "  make shell        open a shell in the backend container"
	@echo "  make nuke         stop & DELETE data volumes (full reset)"
	@echo ""
	@echo "WITHOUT Docker (needs local Postgres+Redis running):"
	@echo "  make local            api + worker + beat + frontend"
	@echo "  make local-backend    backend only"
	@echo "  make local-frontend   frontend only"

# ---- Docker -------------------------------------------------------------
backend/.env:
	@cp backend/.env.example backend/.env
	@echo "[make] created backend/.env from backend/.env.example — add your API keys + secrets there."

# --- Development (default): docker-compose.override.yml is auto-merged ----
dev: up
dev-build: up-build

up: backend/.env
	$(COMPOSE) up -d

up-build: backend/.env
	$(COMPOSE) up -d --build

# --- Production: base compose file only ----------------------------------
prod: backend/.env
	$(COMPOSE_PROD) up -d

prod-build: backend/.env
	$(COMPOSE_PROD) up -d --build

down:
	$(COMPOSE) down

stop:
	$(COMPOSE) stop

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

migrate: backend/.env
	$(COMPOSE) run --rm migrate

seed:
	$(COMPOSE) exec backend python -m app.devtools seed

shell:
	$(COMPOSE) exec backend bash

rebuild: backend/.env
	$(COMPOSE) build --no-cache

# Full reset: removes containers AND the postgres/redis/contract volumes.
nuke:
	$(COMPOSE) down -v

# ---- Native (no Docker) -------------------------------------------------
local:
	ENVIRONMENT=development ./start.sh

local-backend:
	ENVIRONMENT=development ./start.sh backend

local-frontend:
	ENVIRONMENT=development ./start.sh frontend
