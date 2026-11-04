# Task Breakdown: Intake for External AI Agents (MCP Server)

Feature ID: 007-intake-mcp-server
Plan: ./plan.md (must be APPROVED)
Created: 2026-10-21
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [ ] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- `dependsOn: —` means no dependencies (wave 1).
- Every task maps to requirement(s) from spec.md, noted as `(FR-N)`.
- Two tasks that touch the same file must never carry `[P]` together — watch the
  shared hotspots: `backend/app/models.py`, `backend/app/jobs/tasks.py`,
  `frontend/src/lib/endpoints.ts`, `frontend/src/lib/types.ts`,
  `frontend/src/components/ui.tsx`. (This feature touches only `types.ts` and
  `endpoints.ts`, and only in T005; it touches none of the others.)
- Migration tasks never run in parallel with each other (single Alembic head). This
  feature has exactly one migration task (T001).

Agent roles: the plan assigns `mcp-server/**`, both compose files, `deploy/nginx/aegis.conf`,
`.github/workflows/ci.yml` and the operator docs to **backend-dev** even though they are
outside the standard delegation table (plan.md "Path mapping" flag). Two backend-dev tracks
run side by side — **platform** (T002, T003, T006, T008) and **tool server / deploy**
(T004, T007, T009, T010, T011) — and share no files.

## Waves

### Wave 1 — Foundation (5 parallel tasks)
- [ ] T001 [P] (db-engineer) Add `idempotency_key`, `idempotency_hash`, `filed_via_api_key_id` + FK + index + unique partial index `uq_intake_request_org_idemkey` to `IntakeRequest`; migration `0074_intake_agent_filing` (24 chars) with working `downgrade()`; re-run `alembic heads` first and chain onto the single current head (FR-15, FR-17, FR-18, FR-48) — dependsOn: — — files: backend/app/intake/models.py, backend/alembic/versions/0074_intake_agent_filing.py
- [ ] T002 [P] (backend-dev, platform) API-key identity and containment: new `AuthContext` + `INTERNAL`; `get_current_user` records `auth_method` / `api_key_id`; `request_auth()`; `authenticate_api_key_record` / `authenticate_api_key_with_id`; a key can no longer mint keys (403); administrators (`user:approve`) can list another user's keys and revoke them (org-scoped, 404 otherwise); `api_key.created` / `api_key.revoked` audit metadata; pytest (FR-12, FR-31, FR-35, FR-40, FR-41) — dependsOn: — — files: backend/app/core/auth_context.py, backend/app/core/deps.py, backend/app/auth/service.py, backend/app/auth/routes.py, backend/tests/test_api_key_admin_revoke.py
- [ ] T003 [P] (backend-dev, platform) Rate-limit plumbing: 429 handler adds slowapi `Retry-After` and `retry_after_seconds`; settings `rate_limit_intake_create` (`10/minute;100/hour`), `rate_limit_intake_read` (`120/minute;3000/hour`), `rate_limit_intake_document` (`30/minute;300/hour`); production checks in `validate_runtime_settings()` (rate limiting on, limit strings parse); update the locked-down production fixture + 2 negative tests; document the three env vars and the `ALLOWED_HOSTS` note (FR-22, FR-23, FR-24, FR-60) — dependsOn: — — files: backend/app/core/rate_limit.py, backend/app/core/config.py, backend/.env.example, deploy/backend.env.example, backend/tests/test_phase10_security_hardening.py, backend/tests/test_rate_limit_handler.py
- [ ] T004 [P] (backend-dev, tool server) `mcp-server` foundation: `Dockerfile` (python:3.12-slim, `runtime` + `dev` targets, non-root, read-only-friendly), exact-pinned `requirements.txt` / `requirements-dev.txt`, `pyproject.toml` (ruff line-length 100, pytest), `aegis_intake_mcp/{__init__,config,errors,schemas,limits,aegis_client}.py` (env config dataclass, `ToolError`, `extra="forbid"` input models, attachment pre-flight 5 files/25 MB/40 MB/empty/base64/URL-or-path refusal, allow-listed 9-pair `httpx` client with `trust_env=False`, `follow_redirects=False`, per-call auth header, bounded retries), `tests/conftest.py` (MockTransport fake platform), `tests/test_limits.py`, `tests/test_aegis_client.py`. Record the resolved `mcp` version in status; the tool contract in plan.md stays frozen (FR-2, FR-10, FR-11, FR-13, FR-26, FR-27, FR-29, FR-30, FR-51, FR-52, FR-54, FR-57) — dependsOn: — — files: mcp-server/Dockerfile, mcp-server/requirements.txt, mcp-server/requirements-dev.txt, mcp-server/pyproject.toml, mcp-server/aegis_intake_mcp/__init__.py, mcp-server/aegis_intake_mcp/config.py, mcp-server/aegis_intake_mcp/errors.py, mcp-server/aegis_intake_mcp/schemas.py, mcp-server/aegis_intake_mcp/limits.py, mcp-server/aegis_intake_mcp/aegis_client.py, mcp-server/tests/conftest.py, mcp-server/tests/test_limits.py, mcp-server/tests/test_aegis_client.py
- [ ] T005 [P] (frontend-dev) Staff-queue agent flag: `IntakeRequest.filed_by_agent?` / `filed_by_agent_name?`, `IntakeRequestCreateResult`, `intakeApi.create` return type; `agent · <name>` pill in `rowFlags` using an existing pill class; "Filed by" row in the request overview shown only when flagged; vitest; `npm run typecheck` and `npm run test` (never `npm run lint`) (FR-48) — dependsOn: — — files: frontend/src/lib/types.ts, frontend/src/lib/endpoints.ts, frontend/src/app/(app)/intake/_legal-intake.tsx, frontend/src/app/(app)/intake/_request-overview.tsx, frontend/src/app/(app)/intake/_legal-intake.test.tsx, frontend/src/app/(app)/intake/_request-overview.test.tsx

### Wave 2 — Platform service + read tools (2 parallel tasks)
- [ ] T006 [P] (backend-dev, platform) Intake schemas and service: `RequestCreate.idempotency_key` (pattern `^[A-Za-z0-9._:-]{8,128}$`), `RequestResponse.filed_by_agent(_name)`, `RequestCreateResponse.idempotent_repeat`; `create_request(..., auth)` — forced `source="api"` and ignored `requester_name` for key filings, `filed_via_api_key_id`, key-present path skips the 2-minute duplicate check, fast-path lookup + `IntegrityError` fold-in (copy `ingest.py:356-402`), SHA-256 fingerprint excluding priority/department/requester_name/source, one 409 message for different content or different requester, `intake.create_repeated` audit (never a second `intake.created`); `intake.created` / `intake.document.added` audit metadata (`auth_method`, `api_key_id`); `serialize_request` flag fields; every query keeps its `org_id` filter; service-level pytest (FR-15, FR-16, FR-17, FR-18, FR-19, FR-31, FR-32, FR-33, FR-34, FR-48, FR-49, FR-58) — dependsOn: T001, T002 — files: backend/app/intake/schemas.py, backend/app/intake/service.py, backend/tests/test_intake_idempotency.py
- [ ] T007 [P] (backend-dev, tool server) Shaping and read tools: `shaping.py` (`RequestSummary`, triage state, fixed-template `next_step`, poll hint, error mapping table, REQ-ref resolution) and `tools.py` with `list_request_forms` (with `register_options` from `/legal-entities` and `/counterparties`), `get_intake_request`, `list_my_requests`, `get_request_sla`, `get_request_documents` (descriptions for a model, frozen in plan.md); pytest (FR-3, FR-5, FR-6, FR-7, FR-9, FR-46, FR-50, FR-59) — dependsOn: T004 — files: mcp-server/aegis_intake_mcp/shaping.py, mcp-server/aegis_intake_mcp/tools.py, mcp-server/tests/test_read_tools.py, mcp-server/tests/test_auth_passthrough.py

### Wave 3 — Platform routes + write tools (2 parallel tasks)
- [ ] T008 [P] (backend-dev, platform) Intake routes: `@limiter.limit` directly above `create_request` (create setting) and on `list_forms`, `list_my_requests`, `get_request`, `sla_legs`, `list_request_documents` (read setting) and `upload_document` (document setting), each with `request: Request, response: Response`; `response_model=RequestCreateResponse`, status 200 on repeat, `auth=request_auth(request)` passed to filing and document upload; permissions and screen gates unchanged; pytest for rate limits, API-key audit, agent identity (create-only role: file/read own, 404 for others, 403 on staff routes, 403 without the screen grant) and HTTP-status cases for idempotency (201/200/409) — run on a stack with RBAC enforced (FR-14, FR-16, FR-22, FR-23, FR-24, FR-25, FR-32, FR-34, FR-37, FR-38, FR-43, FR-44, FR-45, FR-60, FR-61) — dependsOn: T003, T006 — files: backend/app/intake/routes.py, backend/tests/test_intake_rate_limits.py, backend/tests/test_intake_api_key_audit.py, backend/tests/test_intake_agent_identity.py, backend/tests/test_intake_idempotency.py
- [ ] T009 [P] (backend-dev, tool server) Write tools: `create_intake_request` (required `form_key` sets `field_values.request_form`, generated `mcp-<uuid>` key when absent, retry once with the same body/key, `created` / `already_filed` / `conflict` / `outcome_unknown` outcomes, priority derived as in plan.md) and `add_document` (no retry; `outcome_unknown` on timeout); register all 7 tools; pytest for create, add_document, error mapping, untrusted text, tool catalog (exactly 7, `additionalProperties: false`), and `test_no_key_leak` (every tool under DEBUG logging, no default `Authorization` header) (FR-4, FR-8, FR-20, FR-21, FR-27, FR-28, FR-29, FR-30, FR-47, FR-49, FR-57) — dependsOn: T007 — files: mcp-server/aegis_intake_mcp/tools.py, mcp-server/tests/test_create_tool.py, mcp-server/tests/test_add_document.py, mcp-server/tests/test_error_mapping.py, mcp-server/tests/test_untrusted_text.py, mcp-server/tests/test_tool_catalog.py, mcp-server/tests/test_no_key_leak.py

### Wave 4 — Server runtime, deployment and docs (2 parallel tasks)
- [ ] T010 [P] (backend-dev, tool server) `server.py`: Starlette app with `/mcp` (`StreamableHTTPSessionManager`, `stateless=True`, `json_response=True`, DNS-rebinding protection from `MCP_ALLOWED_HOSTS`), `/healthz`, `/readyz` (200 only when the platform `/healthz` answers), body-size guard (60 MiB), log redaction filter, server `instructions` text, per-call Bearer extraction (`tools/list` needs no key; every `tools/call` does); `README.md` (config, run, connect a client, TLS, DISABLE_RBAC warning, offline note); pytest for health and an over-the-wire end-to-end (FR-12, FR-53, FR-54, FR-55, FR-56) — dependsOn: T009 — files: mcp-server/aegis_intake_mcp/server.py, mcp-server/tests/test_health.py, mcp-server/tests/test_http_e2e.py, mcp-server/README.md
- [ ] T011 [P] (backend-dev, tool server) Deploy and operator procedure: `mcp` service in `docker-compose.yml` (frontend pattern: own build, `8001:8001`, `depends_on: backend: service_healthy`, `/readyz` healthcheck, no `env_file`, read-only filesystem), dev override in `docker-compose.override.yml`, `/mcp` upstream + location in `deploy/nginx/aegis.conf` (60 MB body, TLS), `mcp-server` job in `.github/workflows/ci.yml` (`ruff check .` + `pytest -q`), and `mcp-server/AGENT_IDENTITY.md` — the manual procedure (invite a dedicated user, assign the `intake_agent` role with `PUT /roles/user/{id}`, add the `intake` screen grant at ADD with `POST /screen-access/grants`, sign in, mint the key, revoke, rotate), checked against the routes as they stand after T008 (FR-37, FR-39, FR-42, FR-53, FR-55) — dependsOn: T004, T008 — files: docker-compose.yml, docker-compose.override.yml, deploy/nginx/aegis.conf, .github/workflows/ci.yml, mcp-server/AGENT_IDENTITY.md

### Wave 5 — Verification
- [ ] T012 (qa-engineer) Verify AC-1..AC-40 and write verification.md: the CI triple from `backend/` (`ruff check .`, `alembic upgrade head`, `pytest -q`) plus `alembic downgrade -1 && alembic upgrade head`; `ruff check .` and `pytest -q` in `mcp-server`; frontend `npm run typecheck` and `npm run test`; a live run on the compose stack **with `DISABLE_RBAC=false`** using an agent identity created exactly as `AGENT_IDENTITY.md` says (list tools, file with an attachment and poll triage, retry with the same key, exceed the limit, revoke as an administrator and call again, stop `backend` and observe `/readyz` 503 and `service_unavailable`, check the staff-queue flag). Record AC-39's refused branch as not applicable and AC-32's platform-triage caveat (R4) (AC-1..AC-40) — dependsOn: T005, T008, T010, T011 — files: specs/007-intake-mcp-server/verification.md

## Parallel-safety check

| Wave | Tasks | Shared files |
|---|---|---|
| 1 | T001 ∥ T002 ∥ T003 ∥ T004 ∥ T005 | None. T001 `intake/models.py` + migration; T002 `core/{auth_context,deps}.py` + `auth/**`; T003 `core/{rate_limit,config}.py` + env examples + hardening test; T004 `mcp-server` foundation; T005 `frontend/**` |
| 2 | T006 ∥ T007 | None. `backend/app/intake/{schemas,service}.py` vs `mcp-server/**` |
| 3 | T008 ∥ T009 | None. `backend/app/intake/routes.py` + tests vs `mcp-server/**` |
| 4 | T010 ∥ T011 | None. `mcp-server/aegis_intake_mcp/server.py`, its tests and README vs compose files, nginx, CI, `AGENT_IDENTITY.md` |
| 5 | T012 | `verification.md` only |

Sequential on purpose (shared file, so never `[P]` together): `tools.py` (T007 → T009); `backend/tests/test_intake_idempotency.py` (T006 → T008); the `mcp-server/tests` set is split by task so no two tasks write the same test file. One migration (T001), so Alembic stays single-head. `backend/app/models.py`, `app/jobs/tasks.py` and `src/components/ui.tsx` are not touched.

Ownership check: every file above is inside the owning agent's paths in plan.md "Path mapping". Not in the standard delegation table, assigned explicitly by the plan: `mcp-server/**`, `docker-compose*.yml`, `deploy/nginx/aegis.conf`, `.github/workflows/ci.yml`.

## Workload per agent

| Agent | Tasks | Notes |
|---|---|---|
| db-engineer | T001 | 1 task; blocks T006 |
| backend-dev (platform) | T002, T003, T006, T008 | T002 ∥ T003 in wave 1; T006 wave 2; T008 wave 3 |
| backend-dev (tool server / deploy) | T004, T007, T009, T010, T011 | 5 tasks, sequential through wave 3 then T010 ∥ T011 in wave 4 |
| frontend-dev | T005 | 1 task, independent |
| qa-engineer | T012 | final wave |

Critical path: T004 → T007 → T009 → T010 → T012 (tool server). The platform chain T001/T002 → T006 → T008 → T011 → T012 is the same length.

## Task detail notes

- **Orchestrator, before wave 1:** regenerate the repo graph (`python scripts/build_repo_graph.py`), per the playbook. Re-run `alembic heads` before T001 writes the migration; the plan recorded `0073_merge_trademark_suite` as the only head at plan time.
- **T001:** revision ID must stay ≤ 32 characters (`0074_intake_agent_filing` is 24). `IntakeRequest` is already in the `app/models.py` registry — verify only, no edit. The migration is additive and nullable with no backfill.
- **T002:** `authenticate_api_key` stays as a wrapper so existing callers keep working. `deps.py` falls back to `INTERNAL` when no auth state is present, so internal callers (ingest, assistant tools, chat) need no change. Administrator revoke uses the `user:approve` permission and is always org-scoped (404 across orgs).
- **T003:** the three new settings are not `MOCK_*` flags, but the constitution still requires the production-config fixture to stay complete: update `test_runtime_settings_accepts_a_correctly_locked_down_production_config`. The limiter runs with `swallow_errors=True`, so unparseable limit strings must fail at startup, not at request time.
- **T004:** the plan calls for an exact pin of the `mcp` 1.x SDK (≥ 1.12). If the resolved version's API differs from what plan.md assumes, record it in the status detail and keep the tool contract frozen. Do not add `mcp` to `backend/requirements.lock`.
- **T006:** the 2-minute duplicate check is skipped only when a key is present (plan F2). The audit row for a repeat is `intake.create_repeated`, never a second `intake.created` (FR-33).
- **T008:** slowapi checks the limit after dependency resolution and before the handler body, so a 429 creates nothing (FR-25); keep the decorator on the line directly above `def` (pattern in `test_intake_webhook_rate_limit.py`). Run these tests with RBAC enforced (conftest forces `DISABLE_RBAC=false`).
- **T009:** retries reuse the identical body and idempotency key; `add_document` never retries; no retry on any 4xx or on 500.
- **T011:** `AGENT_IDENTITY.md` must work on a role created after migration 0043: the `intake` screen grant at ADD is a required step (plan F1). The production `ALLOWED_HOSTS` must include the `backend` hostname.
- **T012:** the dev stack's `DISABLE_RBAC=true` (override file) hides permission failures, so isolation checks must run with it off (plan R1).
