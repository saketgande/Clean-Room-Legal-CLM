# syntax=docker/dockerfile:1
#
# Aegis Intake MCP tool server. A thin, stateless proxy: no database, no secrets, no
# volumes. Deliberately independent of backend/requirements.lock (FR-52).
#
# Build context is ./mcp-server, so only the files below can ever enter the image.

# --- runtime: the default, production target ------------------------------
FROM python:3.12-slim AS runtime

# PYTHONDONTWRITEBYTECODE: the container runs with a read-only root filesystem
# (compose: read_only: true + a /tmp tmpfs), so nothing may try to write .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY aegis_intake_mcp ./aegis_intake_mcp

# Non-root, no home directory, no login shell. App files stay root-owned and
# read-only to this user. No curl: the compose healthcheck uses Python.
RUN useradd --system --uid 10002 --no-create-home --shell /usr/sbin/nologin aegis
USER aegis

EXPOSE 8001

CMD ["uvicorn", "aegis_intake_mcp.server:app", "--host", "0.0.0.0", "--port", "8001"]

# --- dev: runtime + test/lint tooling --------------------------------------
# Selected by docker-compose.override.yml (target: dev). Never the default, so a plain
# production build cannot pick it up.
FROM runtime AS dev
USER root
COPY requirements-dev.txt pyproject.toml ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
USER aegis
