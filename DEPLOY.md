# AEGIS Legal CLM — Production Deployment

A from-scratch operator guide for a single-host deployment driven by
`backend/docker-compose.prod.yml` behind Caddy with automatic TLS.

The stack: **Caddy** (TLS + reverse proxy) → **frontend** (Next.js standalone
Node server) + **api** (gunicorn/uvicorn FastAPI) + **worker**/**beat**
(Celery) → **postgres** (pgvector) + **redis**. Only Caddy is exposed on the
host (ports 80/443); Caddy routes `/api/*` to the api service and everything
else to the frontend service.

---

## 1. Prerequisites

- A Linux host with **Docker Engine + Compose v2** (`docker compose version`).
- Two DNS **A/AAAA records** pointing at the host's public IP, e.g.
  - `api.example.com`  → API (split-origin clients)
  - `app.example.com`  → web app (also same-origin-proxies `/api`)
- Ports **80 and 443** open to the internet (Let's Encrypt HTTP/TLS challenge).
- Outbound network access for the host to reach Let's Encrypt and your vendors
  (Anthropic, DocuSign, Reducto, Resend).

> Recommended alternative to self-managed Postgres: a **managed Postgres** with
> point-in-time recovery (RDS / Cloud SQL / etc.). If you use one, drop the
> `postgres` and `db-backup` services and set `DATABASE_URL` to the managed
> instance.

---

## 2. Clone and check out

```bash
git clone <repo-url> aegis && cd aegis
git checkout main            # or the release tag you intend to deploy
cd backend
```

All commands below run from `aegis/backend` unless noted.

---

## 3. Create `.env` from the example

```bash
cp .env.example .env
```

Generate strong secrets and write them into `.env` (replace the placeholders):

```bash
# 32+ byte secrets — copy each value into the matching key in .env
openssl rand -hex 32   # -> SECRET_KEY
openssl rand -hex 32   # -> SETUP_TOKEN
openssl rand -hex 24   # -> POSTGRES_PASSWORD   (no weak 'legal_clm'!)
openssl rand -hex 32   # -> DOCUSIGN_CONNECT_HMAC_KEY  (if using DocuSign Connect)
```

Then set the production-mode flags and domains in `.env`:

```dotenv
ENVIRONMENT=production
DEBUG=false
FORCE_HTTPS=true

# Domains consumed by Caddy
API_DOMAIN=api.example.com
APP_DOMAIN=app.example.com

# Locked-down CORS / hosts (no wildcards in production)
ALLOWED_HOSTS=api.example.com,app.example.com
CORS_ORIGINS=https://app.example.com
APP_BASE_URL=https://app.example.com

# Cookie + token hardening (validate_runtime_settings enforces these)
REFRESH_COOKIE_SECURE=true
EXPOSE_REFRESH_TOKEN_IN_BODY=false
EXPOSE_PASSWORD_RESET_TOKEN_IN_RESPONSE=false

# Real integrations — all mocks MUST be off in production
MOCK_CLAUDE=false
MOCK_DOCUSIGN=false
MOCK_REDUCTO=false
MOCK_RESEND=false
CLAUDE_API_KEY=...
REDUCTO_API_KEY=...
RESEND_API_KEY=...
```

The app refuses to boot in production with weak secrets, enabled mocks, or
wildcard hosts (`app/core/config.py: validate_runtime_settings`), and the
container entrypoint additionally refuses the weak default `POSTGRES_PASSWORD`.

> **Frontend API base (no-CORS, default):** the frontend image bakes
> `NEXT_PUBLIC_API_BASE_URL=/api/v1` at build time, so the browser calls the
> same origin and Caddy same-origin-proxies `/api/*` to the api service —
> first-party cookies work and you need no `CORS_ORIGINS` entry. To point the
> SPA at a separate API origin instead, rebuild the frontend with
> `--build-arg NEXT_PUBLIC_API_BASE_URL=https://api.<domain>/api/v1` (or set
> `NEXT_PUBLIC_API_BASE_URL` in `.env`, which the compose build reads) and add
> that origin to the backend's `CORS_ORIGINS`.

> **Frontend build note:** the frontend is a Next.js app built with
> `output: "standalone"`; `docker compose ... up -d --build` builds it
> automatically. The build runs `next build` inside the image — no Node
> toolchain is needed on the host.

---

## 4. Bucket-B manual steps (do these by hand — not automatable)

These cannot be scripted and must be performed in the vendors' admin consoles:

1. **Rotate vendor API keys** to production credentials and paste them into
   `.env` (`CLAUDE_API_KEY`, `REDUCTO_API_KEY`, `RESEND_API_KEY`,
   `DOCUSIGN_INTEGRATION_KEY` / `DOCUSIGN_USER_ID` / `DOCUSIGN_ACCOUNT_ID`).
   Never reuse the dev/sandbox keys.
2. **DocuSign Connect HMAC:** in DocuSign Admin → *Connect* → your configuration,
   enable **HMAC** and set the key to the value you generated for
   `DOCUSIGN_CONNECT_HMAC_KEY`. The inbound webhook self-rejects unsigned
   callbacks when this key is set, so the value in DocuSign and in `.env` must
   match exactly.
3. Mount the **DocuSign JWT private key** if using JWT auth: place the PEM where
   `DOCUSIGN_PRIVATE_KEY_PATH` points inside the container (add a read-only
   volume for it in a compose override; do **not** bake it into the image).

---

## 5. One-shot migration (separate release step)

In production the entrypoint does **not** auto-migrate (so multiple replicas
don't race Alembic on boot). Run migrations explicitly before bringing the API
up — this also implicitly builds the image:

```bash
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
```

---

## 6. Bring up the stack

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Caddy will provision TLS certificates for `API_DOMAIN` and `APP_DOMAIN` on first
request (allow a few seconds). Optional heavy services are off by default:

```bash
# On-host antivirus scanning of uploads (heavy):
docker compose -f docker-compose.prod.yml --profile scan up -d
# Local pg_dump backups (prefer managed Postgres PITR instead):
docker compose -f docker-compose.prod.yml --profile backup up -d
```

---

## 7. Verify

```bash
# Liveness (unauthenticated, no DB):
curl -fsS https://api.example.com/healthz
# -> {"status":"ok"}

# Readiness (checks DB + Redis):
curl -fsS https://api.example.com/readyz
# -> {"status":"ready",...}

# Frontend shell:
curl -fsSI https://app.example.com/ | head -n1   # -> HTTP/2 200
```

> `/healthz` (liveness) and `/readyz` (readiness) are always on. The richer
> `/api/v1/debug/*` traces are intentionally **disabled in production**.

---

## 7a. Logs & errors

All services log structured JSON to stdout; Docker captures it. **Both backend
and frontend errors are visible here** — the browser ships uncaught JS errors,
unhandled promise rejections, and React error-boundary failures to
`POST /api/v1/client-logs`, which logs them under the api service (logger
`app.client`), so you never have to ask a user to open DevTools.

```bash
cd backend
# Follow everything (Ctrl-C to stop):
docker compose -f docker-compose.prod.yml logs -f api worker beat frontend caddy

# Backend + browser errors only (api covers both):
docker compose -f docker-compose.prod.yml logs -f api | grep -Ei '"level":"(ERROR|WARNING)"'

# Browser/client errors specifically:
docker compose -f docker-compose.prod.yml logs api | grep '"logger":"app.client"'

# Correlate a user-facing 500 by its request id (returned as X-Request-ID):
docker compose -f docker-compose.prod.yml logs api | grep '<request-id>'
```

Log rotation is set per service in the compose file (`json-file`, 10 MB × 5).
To apply the same cap to **every** container on the VM, set a Docker daemon
default in `/etc/docker/daemon.json` and `sudo systemctl restart docker`:

```json
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "5" } }
```

**Optional — error tracking:** set `SENTRY_DSN` in `.env` to also stream
backend (and, with the DSN exposed to the build, frontend) errors to Sentry for
alerting and stack-trace grouping. Leaving it unset keeps everything in the
container logs above.

---

## 8. First-admin bootstrap (via SETUP_TOKEN)

Create the first organization + admin user with the `SETUP_TOKEN` you generated:

```bash
curl -fsS -X POST https://api.example.com/api/v1/auth/setup/first-admin \
  -H 'Content-Type: application/json' \
  -d '{
    "setup_token": "<SETUP_TOKEN from .env>",
    "organization_name": "Example Corp",
    "organization_slug": "example",
    "allowed_domains": ["example.com"],
    "email": "admin@example.com",
    "full_name": "Admin User",
    "password": "<a strong password, 10+ chars>"
  }'
```

Then log in at `https://app.example.com`. For security, consider rotating
`SETUP_TOKEN` (re-run `openssl rand -hex 32`, update `.env`, recreate the api)
once the first admin exists.

---

## 9. Backup & restore

**Backups.** If managed Postgres: rely on the provider's automated PITR.
If self-managing with the `backup` profile, dumps land in the `db_backups`
volume (`@daily`, with 7d/4w/6m retention). Also snapshot the
`contract_file_storage` volume (the contract files), since the DB only stores
metadata + pointers.

Manual dump / restore:

```bash
# Dump
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U legal_clm -d legal_clm -Fc > backup_$(date +%F).dump

# Restore into a fresh DB (stop api/worker/beat first)
docker compose -f docker-compose.prod.yml stop api worker beat
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U legal_clm -d legal_clm --clean --if-exists < backup_YYYY-MM-DD.dump
docker compose -f docker-compose.prod.yml start api worker beat
```

Back up the contract files volume:

```bash
docker run --rm -v backend_contract_file_storage:/data -v "$PWD":/out \
  alpine tar czf /out/contracts_$(date +%F).tgz -C /data .
```

---

## 10. Rollback to a previous image tag

`deploy.yml` publishes `ghcr.io/<owner>/<repo>/api:<git-sha>` for every push to
main. To run a specific tag instead of building locally, add a compose override
that pins the image, then re-up:

```bash
# docker-compose.override.yml  (or pass via -f)
# services:
#   api:    { image: ghcr.io/<owner>/<repo>/api:<good-sha> }
#   worker: { image: ghcr.io/<owner>/<repo>/api:<good-sha> }
#   beat:   { image: ghcr.io/<owner>/<repo>/api:<good-sha> }

docker compose -f docker-compose.prod.yml -f docker-compose.override.yml pull
docker compose -f docker-compose.prod.yml -f docker-compose.override.yml up -d
```

If the rollback target predates a migration, restore the matching DB backup
(section 9) — schema downgrades are not assumed to be safe.

---

## 11. Key rotation

1. **App secrets** (`SECRET_KEY`, `SETUP_TOKEN`): generate a new value
   (`openssl rand -hex 32`), update `.env`, then
   `docker compose -f docker-compose.prod.yml up -d api worker beat`.
   Rotating `SECRET_KEY` invalidates existing JWTs/sessions (users re-login).
2. **POSTGRES_PASSWORD:** change it in Postgres and in `.env` together:
   ```bash
   docker compose -f docker-compose.prod.yml exec postgres \
     psql -U legal_clm -c "ALTER USER legal_clm WITH PASSWORD 'NEW';"
   # set the same value as POSTGRES_PASSWORD in .env, then recreate consumers:
   docker compose -f docker-compose.prod.yml up -d api worker beat
   ```
3. **Vendor keys** (Claude/Reducto/Resend/DocuSign): rotate in the vendor
   console, update `.env`, recreate `api` and `worker`.
4. **DocuSign Connect HMAC:** update the key in DocuSign Admin **and**
   `DOCUSIGN_CONNECT_HMAC_KEY`, then recreate `api`. Keep both in sync or
   inbound callbacks will be rejected.
