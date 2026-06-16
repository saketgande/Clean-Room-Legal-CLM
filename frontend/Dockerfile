# syntax=docker/dockerfile:1
#
# Frontend image for AEGIS Legal CLM (Next.js, production `next start`).

# --- Stage 1: install deps + build ----------------------------------------
FROM node:22-slim AS builder

ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# NEXT_PUBLIC_* vars are inlined at build time. Left empty by default so the
# app falls back to its runtime default (http://localhost:8000/api/v1 when the
# page is served on localhost). Override via the build arg if the API is
# reached through a different origin (e.g. "/api/v1" behind a reverse proxy).
ARG NEXT_PUBLIC_API_BASE_URL=""
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}

RUN npm run build && npm prune --omit=dev

# --- Dev stage: full deps + `next dev` (hot reload) -----------------------
# Selected by docker-compose.override.yml (build target: dev). At runtime the
# source is bind-mounted over /app and an anonymous volume preserves this
# stage's node_modules (which still includes devDependencies). Kept BEFORE the
# runtime stage so the default (no target) build is still production.
FROM node:22-slim AS dev
ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=development \
    PORT=3000
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# --- Stage 2: runtime -----------------------------------------------------
FROM node:22-slim AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000
WORKDIR /app

RUN useradd --system --create-home --uid 10001 aegis
COPY --from=builder --chown=aegis:aegis /app ./
USER aegis

EXPOSE 3000

# package.json: "start": "next start -p 3000"
CMD ["npm", "run", "start"]
