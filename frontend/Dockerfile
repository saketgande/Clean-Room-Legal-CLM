# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# AEGIS frontend — Next.js (standalone) production image.
# Multi-stage: install deps -> build -> minimal non-root runtime.
# Caddy terminates TLS + sets security headers in front of this service; the
# container only needs to be reachable on the internal compose network.
# ---------------------------------------------------------------------------

# ---- deps -----------------------------------------------------------------
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- builder --------------------------------------------------------------
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* values are inlined at build time. Default to a same-origin
# path so the browser calls https://<app-domain>/api/v1, which Caddy proxies
# to the backend — no CORS, no per-deploy rebuild needed. Override with
# --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.com/api/v1 if the
# API lives on a separate origin.
ARG NEXT_PUBLIC_API_BASE_URL=/api/v1
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- runner ---------------------------------------------------------------
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Run as a non-root user.
RUN addgroup --system --gid 1001 nodejs \
  && adduser --system --uid 1001 nextjs

# The standalone output bundles a minimal server + only the deps it needs.
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

# Liveness probe — the Next server answers "/" once booted.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
