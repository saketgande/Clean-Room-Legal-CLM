# AEGIS — Legal CLM

Contract lifecycle management with an AI assistant. Next.js frontend, FastAPI
backend, Celery worker/beat, Postgres (pgvector) + Redis.

## Run it locally with Docker

Only Docker Desktop / Docker Engine is required — the stack brings up its own
Postgres and Redis.

```bash
git clone <this-repo> && cd Clean-Room-Legal-CLM-prod
./setup.sh                             # builds, starts, migrates, seeds — one command
```

Prefer to run the steps yourself? See **[SETUP.md](SETUP.md)** for every step
(and troubleshooting). The manual equivalent is:

```bash
cp backend/.env.example backend/.env   # local defaults, integration mocks ON — no API keys needed
make dev-build                         # build + start everything (migrations run automatically)
make seed                              # create the default admin
```

Open:

- Frontend — <http://localhost:3000>
- API — <http://localhost:8000> (health `/healthz`, docs `/docs`)

Log in with the seeded admin:

```
admin@example.com / local-dev-password
```

That's it. Edit anything under `backend/` or `frontend/` and it hot-reloads —
no rebuild.

## Everyday commands

```bash
make logs     # tail all services
make down     # stop (keeps data)
make up       # start again
make migrate  # run DB migrations on demand
make nuke     # stop + delete all volumes (full reset)
```

## More

- **[DOCKER.md](DOCKER.md)** — dev vs prod modes, real integrations, what each service does, troubleshooting.
- **[DEPLOY.md](DEPLOY.md)** — native / VM (no-Docker) and production deployment.
