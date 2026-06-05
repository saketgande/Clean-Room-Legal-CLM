# syntax=docker/dockerfile:1
#
# Multi-stage production image for the AEGIS Legal CLM backend.
#   builder  — installs all (production-only) deps into a self-contained venv
#   runtime  — slim, non-root; carries only the venv + app source
#
# Dev still uses docker-compose.yml which builds this same context but bind-mounts
# the source and runs uvicorn --reload, so this image stays the prod source of truth.

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build toolchain + headers needed to compile/link the wheels that lack a pure
# manylinux build (psycopg, etc.). None of this leaks into the runtime stage.
RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential libpq-dev \
  && rm -rf /var/lib/apt/lists/*

# Self-contained venv we can copy wholesale into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip

WORKDIR /build

# Copy only what the dependency resolution / build needs first, so the heavy
# dependency layer caches across source-only changes.
COPY pyproject.toml ./
# requirements.lock is optional — present in the repo for reproducible builds.
# When it exists we install the fully-pinned set; otherwise we fall back to the
# ranges in pyproject.toml. (COPY of a possibly-absent file: the wildcard makes
# it optional without failing the build.)
COPY requirements.loc[k] ./

# Production deps ONLY (never .[dev]). Prefer the lockfile when present.
RUN if [ -f requirements.lock ]; then \
        echo "[build] installing pinned deps from requirements.lock" && \
        pip install --no-deps -r requirements.lock ; \
    else \
        echo "[build] no lockfile — resolving from pyproject.toml ranges" ; \
    fi

# Now bring in the source and install the app package itself. With a lockfile the
# transitive deps are already satisfied, so --no-deps keeps the resolver from
# second-guessing the pins; without one this resolves the full tree.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
RUN if [ -f requirements.lock ]; then \
        pip install --no-deps . ; \
    else \
        pip install . ; \
    fi

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Runtime shared libraries only (no compilers): libpq for psycopg, libmagic for
# python-magic upload sniffing. curl is used by the container HEALTHCHECK.
RUN apt-get update \
  && apt-get install -y --no-install-recommends libpq5 libmagic1 curl \
  && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. Contract storage lives on a mounted volume owned by it.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin aegis

WORKDIR /app

# Bring over the prebuilt venv and only the app/runtime files (no tests, no .env,
# no .venv, no caches — see .dockerignore for the full exclusion list).
COPY --from=builder /opt/venv /opt/venv
COPY --chown=aegis:aegis app ./app
COPY --chown=aegis:aegis alembic ./alembic
COPY --chown=aegis:aegis alembic.ini ./alembic.ini
COPY --chown=aegis:aegis scripts ./scripts

# Default in-container storage root (overridable via STORAGE_ROOT); created and
# owned up front so the non-root user can write even before a volume is mounted.
RUN mkdir -p /data/contracts && chown -R aegis:aegis /data/contracts

USER aegis

EXPOSE 8000

# Unauthenticated, DB-free liveness endpoint. /healthz is registered in ALL
# environments (the /api/v1/debug/* router is gated to non-prod). The compose
# files override the command; entrypoint stays the migration guard.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

ENTRYPOINT ["sh", "/app/scripts/entrypoint.sh"]
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", \
     "--timeout", "300", "--graceful-timeout", "30", \
     "--bind", "0.0.0.0:8000", "app.main:app"]
