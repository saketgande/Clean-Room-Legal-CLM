# Local Setup — every step

How to run the full AEGIS Legal CLM stack on your machine with Docker. Two
paths: the **one-command** script, or the **manual** steps it runs (do these if
you want to understand or debug each stage).

The Docker stack is self-contained — it brings up its own Postgres (with
pgvector) and Redis, so nothing but Docker is needed on the host. Integration
mocks are ON by default, so **no API keys are required** to run everything.

---

## 1. Prerequisites

| Need | Why | Check |
| --- | --- | --- |
| **Docker Engine 20.10+** with **Compose v2** | Runs the whole stack | `docker info` and `docker compose version` |
| ~4 GB free RAM, ~3 GB disk | Images + database | — |
| Ports **3000** and **8000** free | Frontend + API (published to the host) | `lsof -i :3000 -i :8000` should be empty |

Get Docker: <https://docs.docker.com/get-docker/> (Docker Desktop on
macOS/Windows includes Compose v2). Make sure Docker is actually **running**
before you start.

> Postgres (5432) and Redis (6379) run **inside** the Compose network and are
> *not* published to the host, so they won't clash with anything you already
> run locally.

---

## 2. One-command setup (recommended)

```bash
git clone <this-repo> && cd Clean-Room-Legal-CLM-prod
./setup.sh
```

`setup.sh` is idempotent and does everything in section 3 for you: checks
prerequisites, creates `backend/.env`, builds and starts the stack, waits until
the API is healthy, and seeds the default admin. First run takes a few minutes
(pulling images + building); re-runs are fast.

When it finishes you'll see the URLs and login below. Skip to section 4.

---

## 3. Manual setup (the same steps, one at a time)

### 3.1 Create the backend config

```bash
cp backend/.env.example backend/.env
```

This is the single config file for the app. Its defaults run everything locally
with integration **mocks ON** (Claude, DocuSign, Reducto, Resend all faked), so
no keys are needed. `DATABASE_URL`, `REDIS_URL` and `STORAGE_ROOT` are
deliberately absent — `docker-compose.yml` pins those to the in-stack services.

### 3.2 Build and start the stack

```bash
docker compose up -d --build
```

This builds the images and starts, in dependency order:

| Service | Port | Role |
| --- | --- | --- |
| `postgres` | (internal) | Database with pgvector |
| `redis` | (internal) | Celery broker + rate-limit store |
| `migrate` | — | Runs `alembic upgrade head` once, then exits |
| `backend` | 8000 | FastAPI — waits for Postgres/Redis healthy **and** migrate to finish |
| `worker` | — | Celery worker |
| `beat` | — | Celery scheduler |
| `frontend` | 3000 | Next.js — waits for the API to be healthy |

**Migrations run automatically** — the `migrate` service gates the backend, so
the schema is always current before the API starts. You don't run Alembic by
hand.

### 3.3 Wait until the API is healthy

```bash
curl -fsS http://localhost:8000/healthz     # {"status":"ok"} when ready
```

Or watch it come up: `docker compose ps` (backend shows `healthy`).

### 3.4 Seed the default admin

```bash
docker compose exec backend python -m app.devtools seed
```

Creates the organization + the default admin (and a few dev users). Safe to
re-run — it no-ops if they already exist.

---

## 4. Use it

- **Frontend** — <http://localhost:3000>
- **API** — <http://localhost:8000> (health `/healthz`, readiness `/readyz`, OpenAPI docs `/docs`)

**Log in:**

```
admin@example.com / local-dev-password
```

In **dev mode** (the default), everything under `backend/` and `frontend/` is
bind-mounted — edit a file, save, and it hot-reloads. No rebuild needed.

---

## 5. Everyday commands

```bash
docker compose logs -f              # tail all services
docker compose logs -f backend      # one service
docker compose ps                   # status
docker compose stop                 # pause (keeps containers + data)
docker compose up -d                # resume
docker compose down                 # stop + remove containers (KEEPS data)
docker compose down -v              # stop + DELETE all data (full reset)
docker compose exec backend bash    # shell in the API container
```

There's also a `Makefile` with shortcuts (`make dev-build`, `make seed`,
`make migrate`, `make logs`, `make nuke`) if you prefer — run `make help`.

---

## 6. Using real integrations (optional)

Everything works mocked. To exercise a real service, edit `backend/.env`: set
its `MOCK_*` flag to `false` and add the key, e.g. for Claude:

```dotenv
MOCK_CLAUDE=false
CLAUDE_API_KEY=sk-ant-...
```

Then recreate the containers so the new env is picked up (a hot-reload alone
won't apply env changes):

```bash
docker compose up -d
```

---

## 7. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Docker isn't running` | Start Docker Desktop / the daemon, then re-run. |
| Port 3000 or 8000 already in use | Stop whatever holds it, or change the published port in `docker-compose.yml`. |
| API never becomes healthy | `docker compose logs backend migrate` — a failed migration or bad `backend/.env` value is the usual cause. |
| Login fails | Run the seed step (3.4). If you reset the DB, seed again. |
| Schema looks stale after pulling new code | `docker compose up -d --build` re-runs `migrate` automatically; or `docker compose run --rm migrate`. |
| Want a clean slate | `docker compose down -v` (deletes the Postgres/Redis/contract volumes), then `./setup.sh`. |

---

## 8. Related docs

- **[DOCKER.md](DOCKER.md)** — dev vs production Docker modes, service internals, config reference.
- **[DEPLOY.md](DEPLOY.md)** — native (no-Docker) and production/VM deployment.
