# syntax=docker/dockerfile:1
#
# Backend image for AEGIS Legal CLM.
# One image serves four roles (selected by `command` in docker-compose.yml):
#   - API      : uvicorn app.main:app
#   - worker   : celery ... worker
#   - beat     : celery ... beat
#   - migrate  : alembic upgrade head
#
# This is the "with Docker" path. The "without Docker" path is start.sh.

# --- Stage 1: build wheels for the pinned dependency set -------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential is only needed here, to compile any dep without a prebuilt
# wheel for this platform (e.g. arm64). It never ships in the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.lock ./
RUN pip wheel --no-deps -r requirements.lock -w /wheels

# --- Stage 2: slim runtime ------------------------------------------------
FROM python:3.12-slim AS runtime

# Runtime OS libraries:
#   libmagic1  -> python-magic (MIME sniffing on contract upload)
#   libgomp1   -> onnxruntime / fastembed (local embeddings)
#   curl       -> container healthcheck against /healthz
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libmagic1 libgomp1 curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the pinned deps from the wheels built in stage 1.
COPY --from=builder /wheels /wheels
COPY requirements.lock ./
RUN pip install --no-deps /wheels/* && rm -rf /wheels

# App source. The package is importable via PYTHONPATH=/app (no editable
# install needed); alembic/celery/uvicorn run from this working directory.
COPY . .

# Non-root user. /data/contracts is the STORAGE_ROOT mount point; creating it
# here (owned by the app user) makes the named volume inherit that ownership
# on first mount, so the non-root process can write contract files.
RUN useradd --system --create-home --uid 10001 aegis \
 && mkdir -p /data/contracts \
 && chown -R aegis:aegis /app /data
USER aegis

EXPOSE 8000

# Default role is the API; compose overrides `command` for worker/beat/migrate.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
