# Aegis Legal CLM — Full-Stack Audit Report

**Date:** 2026-08-12
**Scope:** Backend app (FastAPI, ~199 files), Frontend (Next.js 15 / React 19, ~71 files), Database (SQLAlchemy + Postgres/pgvector, 35 migrations), Redis, Celery (worker + beat), Docker/compose, nginx/deploy.
**Lenses:** memory leaks & resource management, redundancy & duplication, exception/error handling, coding standards.

---

## TL;DR

The application is **well-engineered and clearly production-minded** — central exception handlers, Celery reliability config, runtime prod-hardening checks, non-root pinned Docker images, `strict` TypeScript with zero `any`, and centralized frontend HTTP/util layers. There are **no widespread rot problems**.

The real risks are concentrated in a **small number of infra/database items that only bite under real production load**, plus systemic-but-mechanical cleanups (duplication, `utcnow()`, unenforced linters). Nothing here is on fire.

### Must-fix before real prod load (CRITICAL / HIGH)

| # | Area | Issue |
|---|------|-------|
| DB‑C1 | Database | Celery prefork workers inherit the web process's DB pool — no `engine.dispose()` on fork → intermittent connection corruption |
| DB‑C2 | Database | Total connections `(uvicorn + celery procs) × 15` can exceed Postgres `max_connections` |
| INF‑C1 | Infra | No memory/CPU limits on any container or systemd unit → one runaway embedding task OOMs the whole stack |
| RED‑A1 | Redis | Single Redis DB `/0` shared by broker + results + rate-limit + lockout → `FLUSHDB` blast radius |
| CEL‑B1 | Celery | No beat single-instance lock → every scheduled task double-fires if two beats run |

---

## 1. Backend Application (`backend/app/`)

### 1.1 Memory / resources — clean
No high-severity leaks. HTTP clients (Claude/DocuSign/Resend) are proper lifespan-managed singletons; `get_db` rolls back + closes; every Celery task closes its session in `finally`; request-log queue is bounded with a managed daemon + shutdown drain.

- **LOW** — `ideal/` in-memory stores (`ideal/routes.py:38`, `ideal/store.py:157`) grow forever, but the router only mounts in non-prod. Also O(runs) replay per request.
- **LOW/MED** — `assistant/routes.py:299`: request DB session held for the entire Claude stream (tens of seconds), pinning a pool connection per active stream. Not a leak; pool-pressure risk under load.

### 1.2 Exception handling — mostly correct; ~6 silent swallows
Central mapping in `core/exceptions.py` is solid; 200+ catches log-and-reraise correctly. Issues are `except Exception:` that swallow with **no log**, making failures invisible:

- **MED** — `ai/controller.py:887`: context-build failure silently drops the contract from the AI prompt (degrades answers, zero signal).
- **MED** — `intake/gates.py:132`, `intake/service.py:1448`: AI-gate and text-extraction failures swallowed silently.
- **MED** — `ideal/routes.py:270`: `except Exception → HTTPException(422, str(e))` reports bugs as client validation errors and leaks internals.
- **LOW** — `playbooks/routes.py:309`, `notices/service.py:633`, `intake/service.py:530`, `intake/gmail_sync.py:120`: same class, add a log line.
- **TRIVIAL** — `contracts/service.py:347`: use `except ImportError`. `core/audit.py:81`: dead `try/except: raise` no-op, delete it.

*Verified acceptable:* email-send catches in batch sweeps, storage-orphan rollbacks, best-effort degrade paths, optional-dep `except ImportError: pass`. No route returns a fake 200 on error.

### 1.3 Redundancy — biggest ROI
- **#1 — `fetch-or-404` idiom repeated 34× across 11 packages** (`db.get → check org_id → raise 404`). One `core/get_org_scoped(db, Model, id, actor, label)` collapses each to one line, removes ~60–70 lines, and creates **one tenant-isolation chokepoint** (a few sites currently org-check inconsistently — a security win).
- **#2 — Principal machinery triplicated** across grants/authority/walls (`_validate_principal`, `_principal_label`, `PRINCIPAL_TYPES`). `grants/schemas.py:7` is missing the `^(user|role)$` pattern the other two have (real validation gap). Extract `core/principals.py`.
- **#3 — `request_id=getattr(request.state, ...)` 46×** → one `Depends(get_request_id)`.
- **#4/#6 — Pagination params (4×) and Pydantic field-sets** (intake Rule triad re-typed 3×, grant/authority envelopes, inline `confidence`/`risk_level` Literals ~12×) → shared deps/base mixins.
- **Dead code:** unused imports in `devtools.py:15`, `contracts/stage_triggers.py:75`, `word_addin/service.py:6`, `ideal/store.py:30`, `ideal/routes.py:24`.
- **Decision needed:** `ideal/` is a ~1,930-line parallel event-sourced re-implementation of intake/approvals, non-prod only. Promote or delete.

### 1.4 Coding standards — good house style, two systemic gaps
Good: lazy-`%` logging (0 f-string log calls), central enums, keyword-only typed signatures, clean async/sync split.

- **SYSTEMIC — `datetime.utcnow()` used 122× vs 28 timezone-aware.** Naive, deprecated in 3.12, mixed with aware datetimes → comparison-bug risk. `auth/service.py` uses *both* forms. **Highest correctness risk in this section.** Codemod to `datetime.now(UTC)`.
- **SYSTEMIC — Linter configured but not enforced.** `pyproject.toml` sets only `line-length=100` with no `[tool.ruff.lint]`: 70 unsorted-import findings, 777 lines over the "enforced" 100-char limit, stale `noqa`s. Add `[tool.ruff.lint]` with `ignore = ["B008"]` (FastAPI `Depends()` — 775 false positives), run `ruff --fix`, wire CI.
- **Structure:** `obligations/`, `renewals/`, `projects/` (708 lines), `workflows/`, `assistant/` (925-line routes) put queries/commits/serialization directly in `routes.py` with no `service.py`.
- **Localized:** type-hint gaps on intake/approvals service helpers; magic `"pending"` strings where `ApprovalStatus.PENDING` exists (`approvals/routes.py:271`, `ai/tool_runtime.py:428`); stray `print()` at `intake/copilot.py:92`.

---

## 2. Frontend (`frontend/src/`)

**Overall: unusually disciplined.** `strict: true`, **zero `any`, zero `@ts-ignore`**. Single well-designed HTTP/SSE client (`lib/api.ts`) with `AbortController` + 401→refresh retry. Utils centralized (`lib/utils.ts`). Error boundaries present. Findings are mostly polish.

### 2.1 Memory / cleanup
- **MED** — `setTimeout` without cleanup fires after unmount: `intake/page.tsx:2045`, `intake/_governance-ladder.tsx:187`. The identical pattern in `intake/_request-overview.tsx:113` *does* clean up — copy-paste dropped the `clearTimeout` in 2 of 3 sites.
- **Verified clean:** assistant SSE streaming aborts + clears poll interval on unmount (`assistant-workspace.tsx:461`).
- **LOW/theoretical** — `toast.tsx:30` doesn't clear pending timers (root provider, never unmounts).

### 2.2 Redundancy
- **MED** — flow-run "pump" effect triplicated (`intake/page.tsx:2038`, `_governance-ladder.tsx:180`, `_request-overview.tsx:108`) → extract `useFlowRunPump()`; **fixes the 2.1 leak at the source.**
- **LOW** — Message→ChatItem mapper duplicated 3× in `assistant-workspace.tsx` (211, 251, 520); 6 files format dates inline instead of `fmtDate`; `ideal/page.tsx` (~500-line prototype) ships in the prod bundle at `/ideal`.
- **LOW/maintainability** — monolith files: `intake/page.tsx` (2521), `assistant-workspace.tsx` (2312), `admin/page.tsx` (1927).

### 2.3 Error handling
- **MED** — single-`useQuery` pages with no error state → failed fetch renders a silent blank: `renewals`, `obligations`, `jobs`, `tabular-reviews`, `notifications`, `playbooks`. Add an error branch / shared component.
- **LOW** — `assistant-workspace.tsx` `syncFromServer` (224) and `selectSession` (532) swallow message-load failures with no toast.

### 2.4 Standards
- **MED** — no standalone ESLint/Prettier config; relies on `next lint` defaults, no CI gate. The good TS hygiene is by convention, not enforced.
- **LOW** — `bg-white` at `assistant-workspace.tsx:1289` breaks dark mode (known project no-no). `as never` tone casts confined to `ideal/page.tsx`. 6 deliberate `exhaustive-deps` disables.

---

## 3. Database (SQLAlchemy + Postgres/pgvector)

### 3.1 CRITICAL
- **C1 — Celery prefork shares the web pool.** `core/database.py:76` builds the engine/pool at import; forked workers inherit open sockets and there is **no `worker_process_init` → `engine.dispose()`** anywhere → intermittent `SSL error`/`connection already closed`. Fix: add the signal handler in `celery_app.py`.
- **C2 — Connection ceiling.** Per-process pool `5 + 10 overflow = 15`; peak `(uvicorn + celery procs) × 15` can exceed default Postgres `max_connections=100`. Once C1 is fixed the worker pools become real and additive. Add PgBouncer or per-role pool sizing; document the ceiling.

### 3.2 HIGH
- **H1 — No eager loading anywhere** (`selectinload`/`joinedload` → 0 matches). Confirmed N+1 `db.get()` in loops: `tabular_review/service.py:118`, `51`; `contract_files/service.py:326,574,626`. Batch with `WHERE id IN (...)`.
- **H2 — App-only tenant isolation.** RLS intentionally dropped (`0018`, single-tenant). org_id filtering is purely app-enforced (270 filters vs 19 raw queries). Fine while one-org-per-install holds; **keep documented** as an architectural constraint.

### 3.3 MEDIUM
- **M1 — pgvector index drift.** HNSW index exists only in migration `0005:156`; model `ContractEmbedding` declares no matching `Index()` → autogenerate could emit a DROP. Add to `__table_args__` or pin as manually managed.
- **M2 — Sparse FK `ondelete`** (27 of 171 FKs). Mitigated by pervasive `SoftDeleteMixin`; real exposure only where code hard-deletes a parent. Do an explicit ondelete pass on the contract graph.

### 3.4 Verified clean
Downgrades present (only `0028` is a documented one-way stub); non-nullable adds carry defaults; destructive migrations guarded with `IF EXISTS`/`RAISE EXCEPTION`; `org_id` indexed via `OrgScopedMixin` + composite indexes (`0009`); pgvector queries use the HNSW index (no full scan); log tables indexed on `created_at` + retention sweepers; pool config sane (`pool_pre_ping`, `pool_recycle=1800`, `pool_timeout=30`).

---

## 4. Redis / Celery / Infra

### 4.1 Redis
- **HIGH (A1)** — single Redis DB `/0` for broker + result backend + slowapi rate-limit + share-passcode lockout. Key prefixes differ so daily collisions are unlikely, but any `FLUSHDB` wipes queues, results, rate-limit counters, and lockouts at once. Split into DBs `/0../3`.
- **Clean** — rate limiter IS Redis-backed and cross-worker correct (`rate_limit.py:45`); note `in_memory_fallback_enabled=True` + `swallow_errors=True` means a Redis outage silently degrades limits (availability over strictness). Result keys TTL'd (`result_expires=3600`); lockout writes set TTLs; Redis clients pooled/singleton.

### 4.2 Celery
- **HIGH (B1)** — no beat single-instance lock (default `PersistentScheduler`). Two beats → every task double-fires. Use RedBeat (Redis lock) or enforce/document single-instance.
- **MED (B3)** — `send_obligation_reminders` (`tasks.py:460`) and the renewal email task mark `sent_at`/flag *after* the email send + commit; a crash mid-task or a double-beat re-sends. Other daily sweepers are naturally idempotent. `run_ai_job` is well-guarded (`SELECT FOR UPDATE` + status flip).
- **MED (B4)** — `_run_ai_job` holds a DB session + `FOR UPDATE` row lock for the full ≤900s run (network calls to Claude/embeddings inside). Under `--concurrency=4`, up to 4 connections pinned for minutes. Consider committing the RUNNING flip before the long AI call.
- **Clean** — reliability config correct: `acks_late`, `reject_on_worker_lost`, `prefetch=1`, `visibility_timeout=3600 > task_time_limit=900`; retries narrowed to `TRANSIENT_ERRORS`; dead-lettering; exceptions don't kill the worker.

### 4.3 Infra / Docker / nginx
- **HIGH (C1)** — no memory/CPU limits on any container or systemd unit; worker runs `--concurrency=4` + loads onnxruntime/fastembed. One runaway task can OOM the host and take down Postgres/Redis (single stack). Redis has no `maxmemory`. Add `mem_limit`/`MemoryMax` + Redis eviction policy.
- **MED (C2)** — nginx `proxy_read_timeout 300s` (`aegis.conf:56`) can cut long Ask-Aegis SSE streams; `proxy_buffering off` is correctly set. Raise timeouts for the streaming location or keep heartbeat chunks < 300s.
- **MED (C3)** — nginx sets no HSTS / `X-Content-Type-Options` / `X-Frame-Options` / CSP at the TLS terminator. Add HSTS at minimum.
- **LOW (C5)** — weak/no Postgres+Redis auth (`POSTGRES_PASSWORD: legal_clm`, no Redis `requirepass`), mitigated only by unpublished internal ports.
- **Clean** — Dockerfiles run non-root, multi-stage, pinned base images; healthcheck/`depends_on` graph correct; `validate_runtime_settings` genuinely enforces prod hardening (rejects default secrets, wildcard CORS/hosts, left-on mocks, insecure cookies) and runs in both web and worker at import.

---

## 5. Recommended order of attack

**Phase 1 — production-load blockers (do first)**
1. DB‑C1: `worker_process_init` → `engine.dispose()` in `celery_app.py`.
2. DB‑C2 + INF‑C1: set pool sizes per role + container `mem_limit`/`MemoryMax`; add Redis `maxmemory`.
3. RED‑A1: split Redis into DBs `/0../3`.
4. CEL‑B1: RedBeat or enforced single-beat.

**Phase 2 — one mechanical PR each, low risk**
5. Wire up ruff (`ignore=["B008"]`, `--fix`, CI) + commit frontend ESLint/Prettier.
6. Codemod `utcnow()` → `datetime.now(UTC)`.
7. Add the ~6 missing backend log lines; add frontend error states to single-query pages.

**Phase 3 — structural cleanup (as you touch the code)**
8. Extract `get_org_scoped` + `core/principals.py`; `useFlowRunPump` hook (kills the FE leak).
9. Decide `ideal/`'s fate (backend + frontend); backfill service layers for logic-heavy routeless packages.
10. DB: batch the N+1 `db.get()` loops; pin the pgvector index in the model; ondelete pass on the contract graph.

---

## Appendix — what was checked and found clean

- **Backend:** HTTP-client singletons + lifespan shutdown; `get_db` rollback/close; Celery session hygiene; bounded request-log queue; central exception mapping; no fake-200-on-error; lazy-`%` logging; async/sync split.
- **Frontend:** `strict` TS, zero `any`/`@ts-ignore`; SSE abort + interval cleanup; centralized API/util layers; error boundaries; `console.*` only in error boundary; security headers in `next.config.mjs`.
- **Database:** migration downgrades + destructive-migration guards; non-null-with-default adds; org_id indexing; pgvector HNSW usage; log retention sweepers; sane pool config.
- **Infra:** Celery reliability config; Redis TTLs + pooled clients; Redis-backed cross-worker rate limiting; non-root pinned multi-stage Docker; healthcheck/depends_on graph; `validate_runtime_settings` prod hardening in web + worker.
