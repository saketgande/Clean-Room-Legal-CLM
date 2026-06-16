# Running AEGIS Legal CLM with Docker

You can run the app **two ways**, and switch whenever you like:

| Mode | Command | What it needs on the host |
| --- | --- | --- |
| **With Docker** | `make up` (or `docker compose up -d --build`) | Docker Desktop / Docker Engine only |
| **Without Docker** | `make local` (or `./start.sh`) | Python 3.11+, Node, **a running Postgres + Redis** |

The Docker path brings up its **own** Postgres (with pgvector) and Redis, so
you don't need anything else installed. `start.sh` is unchanged.

---

## Quick start (Docker)

```bash
cp backend/.env.example backend/.env    # one-time; mocks are ON, no API keys needed
make dev-build                          # development mode (hot reload) — see below
```

Then open:

- Frontend: <http://localhost:3000>
- API:      <http://localhost:8000>  (health: `/healthz`, readiness: `/readyz`, docs in dev: `/docs`)

Create the first admin (either use the **First Admin** tab in the UI with the
`SETUP_TOKEN` from `backend/.env`, or seed the default admin):

```bash
make seed
# default login: admin@example.com / local-dev-password
```

Stop / start / reset:

```bash
make logs        # tail all services
make down        # stop & remove containers (DATA IS KEPT)
make up          # start again
make nuke        # stop & DELETE the postgres/redis/contract volumes (full reset)
```

## Development vs production mode

There are two Docker modes. Development is the **default** (it uses
`docker-compose.override.yml`, which Compose auto-merges).

| | Development (default) | Production |
| --- | --- | --- |
| Start | `make dev` / `make dev-build` | `make prod` / `make prod-build` |
| Frontend | `next dev` + Fast Refresh | `next start` (built) |
| Backend | `uvicorn --reload` | plain uvicorn |
| Worker/beat | auto-restart on change (watchfiles) | plain celery |
| Source | **bind-mounted** — edit & save, no rebuild | baked into the image |

In **development**, editing anything under `backend/` or `frontend/` reloads
automatically — no rebuild. (File watching is forced to polling so changes on
the macOS host reliably reach the containers.) Use this while you work.

In **production**, code is built into the images, so a code change needs a
rebuild (`make prod-build`). Use this to validate a release-like run.

`make prod` runs `docker compose -f docker-compose.yml up` — the `-f` skips the
override file, which is what flips it from dev to prod.

## What runs

| Service    | Image / build        | Port  | Role |
| ---------- | -------------------- | ----- | ---- |
| `postgres` | `pgvector/pgvector`  | 5432  | Database (pgvector extension) |
| `redis`    | `redis:7`            | 6379  | Celery broker + rate-limit store |
| `migrate`  | `backend/Dockerfile` | —     | One-shot `alembic upgrade head`, then exits |
| `backend`  | `backend/Dockerfile` | 8000  | FastAPI (uvicorn) |
| `worker`   | `backend/Dockerfile` | —     | Celery worker |
| `beat`     | `backend/Dockerfile` | —     | Celery beat scheduler |
| `frontend` | `frontend/Dockerfile`| 3000  | Next.js (`next start`) |

`backend`, `worker`, and `beat` all share one image (the role is chosen by the
`command:` in `docker-compose.yml`). The API waits for migrations to finish and
for Postgres/Redis to be healthy; the frontend waits for the API to be healthy.

Data persists in named volumes: `pgdata`, `redisdata`, `contracts` (uploaded
contract files, mounted at `/data/contracts`).

## Configuration

All backend settings live in **`backend/.env`** (copied from
`backend/.env.example`) — the **same file** the no-Docker `start.sh` uses, so
there's one place to manage config. Defaults run everything locally with
integration **mocks ON**, so no real keys are required.

To use a real integration, set its `MOCK_*` flag to `false` and add the key,
e.g. for Claude:

```dotenv
MOCK_CLAUDE=false
CLAUDE_API_KEY=sk-ant-...
```

then **`make dev`** (recreates the containers so the new env is picked up — a
hot-reload alone won't apply env changes).

Notes:
- `DATABASE_URL`, `REDIS_URL`, `STORAGE_ROOT` are **pinned by
  docker-compose.yml** to the in-stack services + contract volume — leave them
  in `backend/.env` as-is; compose overrides them.
- In **dev mode**, `ENVIRONMENT` is forced to `development` and the web settings
  (`APP_BASE_URL`, cookies, hosts, CORS) are forced to localhost-safe values so
  the app runs over `http://localhost`, even if `backend/.env` says
  `ENVIRONMENT=production`. Your real keys and `MOCK_*` flags are still honoured.
- **`make prod`** uses `backend/.env` exactly as written (real production mode),
  which requires a complete production config (HTTPS `APP_BASE_URL`, secure
  cookies, non-wildcard hosts, etc.).

> The browser calls the API at `http://localhost:8000/api/v1` automatically
> (the frontend's built-in default for localhost), and `CORS_ORIGINS` already
> allows `http://localhost:3000`. If you serve the API behind a reverse proxy
> on the same origin instead, rebuild the frontend with
> `--build-arg NEXT_PUBLIC_API_BASE_URL=/api/v1`.

## Common commands

```bash
make migrate     # run DB migrations on demand
make shell       # bash inside the backend container
make ps          # service status
make rebuild     # docker compose build --no-cache
docker compose logs -f backend worker    # logs for specific services
```

## Switching back to the no-Docker setup

Nothing to undo — just stop Docker and use `start.sh`:

```bash
make down                 # or: docker compose down
make local                # = ENVIRONMENT=development ./start.sh
```

Note the no-Docker path expects Postgres (with pgvector) and Redis already
running on `localhost:5432` / `localhost:6379`. See [DEPLOY.md](DEPLOY.md) for
the full native / VM setup.
