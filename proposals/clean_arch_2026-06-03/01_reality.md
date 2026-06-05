# AEGIS Legal CLM — Architecture Reality Document

**Agent 1: The Archaeologist** · **Date:** 2026-06-03
**Scope:** Structural map for the Clean Architecture migration pipeline (Agents 2/3/4). Describes the system *as it actually stands today* — no critique, no targets, no recommendations.
**Builds on:** `proposals/audit_2026-06-02/01_architecture.md` (the prior full map). This document adds the migration-specific artifacts: actual layer wiring, transactional control sites, three end-to-end traces, the module dependency map, and the exhaustive `db.commit()`-outside-repository inventory. Where it diverges from the prior doc it is noted inline.

---

## 1. One-sentence statement

AEGIS Legal CLM is a multi-tenant FastAPI contract-lifecycle-management backend whose distinguishing feature is a server-owned Claude AI engine (structured "skills" + a tool-using streaming assistant) that reads, edits, reviews, and routes contracts and emits cited, validated, audit-logged outputs.

---

## 2. Layer diagram — ACTUAL structure (not ideal)

There is **no repository layer and no Unit of Work**. Transactional control is held at the *edges that call the DB session*: FastAPI route functions, the `AIController`/`ToolRuntime` orchestration methods, and the Celery task. The `get_db()` dependency only opens the session and rolls back on exception — it never commits (`app/core/deps.py:17-31`). Service functions are plain module-level functions that take `(db: Session, *, ...)`; some commit, most only `db.flush()` and leave the commit to their caller. The audit log runs in its **own** `SessionLocal()` and commits independently of the caller's transaction.

```
                          HTTP client / SSE consumer
                                     │
            ┌────────────────────────┼─────────────────────────────┐
            │              FastAPI app (app/main.py:create_app)      │
            │  middleware: SlowAPI → RequestContext → CORS → hosts   │
            └────────────────────────┬─────────────────────────────┘
                                     │  Depends(get_db) → SessionLocal()
                                     │  (deps.py: yields, rollback-on-exc, NO commit)
                                     ▼
   ┌─────────────────────────── ROUTES (per domain routes.py) ───────────────────────────┐
   │  OWN the transaction for human-driven endpoints. ~⅔ of all commits live here.        │
   │  Pattern: call service fn(s) → db.commit() → db.refresh(obj) → return ORM object.    │
   │  e.g. contracts/routes.py:112, approvals/routes.py:112, projects/routes.py (13×),    │
   │       playbooks/routes.py (11×), assistant/routes.py (10×), tabular_review (8×)      │
   └───────────────┬──────────────────────────────────────────────┬──────────────────────┘
                   │ (db, *, user=..., request_id=...)              │ (async; SSE generator)
                   ▼                                                ▼
   ┌──────── SERVICES (per domain service.py) ────────┐   ┌──────── AI ENGINE (app/ai/) ─────────┐
   │  plain module-level functions. NOT classes        │   │  AIController (singleton ai_controller│
   │  (except AI). Inline side effects:                │   │  ToolRuntime  (singleton tool_runtime)│
   │   - db.add(...) / db.flush()                       │   │  SkillRegistry, ToolRegistry          │
   │   - write_audit_log(...) / write_timeline_event   │   │                                       │
   │   - transition_contract_stage(...)                │   │  COMMITS INLINE, repeatedly:          │
   │   - integration calls (resend/docusign/reducto)   │   │   controller.py 16 commits            │
   │  MIXED commit ownership:                          │   │   tool_runtime.py 2 commits + ~40     │
   │   - auth/service.py commits (19×)                  │   │     flushes                            │
   │   - contract_files/service.py commits (6×)         │   │   stream_assistant_run commits inside │
   │   - contracts/service.py (1×), tabular (2×)        │   │     the tool loop (per iteration)     │
   │   - approvals/service.py, signatures/service.py,   │   └───────────────┬──────────────────────┘
   │     playbooks/service.py: flush only, caller       │                   │ run_structured_skill /
   │     commits                                        │                   │ stream / resume
   └───────────────┬───────────────────────────────────┘                   │
                   │ db.add / relationship writes                          │ claude_client.complete_*
                   ▼                                                        ▼
   ┌──────── MODELS (per domain models.py, SQLAlchemy 2.0 DeclarativeBase) ────────────────┐
   │  registered en masse via app/models.py. Mixins in core/database.py:                    │
   │  IdMixin (str-36 UUID), TimestampMixin, OrgScopedMixin (org_id, no FK),                 │
   │  ActorTrackedMixin, SoftDeleteMixin (deleted_at/legal_hold), TableNameMixin.            │
   │  Engine + SessionLocal (expire_on_commit=False) in core/database.py:72-86.              │
   └───────────────────────────────────────────────────────────────────────────────────────┘

   ── CROSS-CUTTING (called from services, routes, engine, and jobs alike) ──
   core/audit.py  write_audit_log()  ─► opens its OWN SessionLocal(), pg_advisory_xact_lock,
                                         commits + closes  (durable_db.commit() audit.py:82)
                                         ⇒ SEPARATE TRANSACTION from the operation it records
   core/audit.py  write_timeline_event() ─► db.add() on the CALLER's session (no commit)
   integrations/* singletons: claude_client, docusign_client, reducto_client,
                  resend_client, storage_service  (module-level, long-lived where async)

   ── BACKGROUND (separate process; its OWN session lifecycle) ──
   jobs/tasks.py  run_ai_job(job_id)  ─► db = SessionLocal(); ... ; finally db.close()
        dispatched via Celery/Redis from jobs/service.py:dispatch_job → run_ai_job.delay()
        OWNS its transaction: 11 db.commit() calls in the single task body.
        calls into the SAME AIController.run_structured_skill (commit=True by default).
```

**Where `db.commit()` actually happens:** in **routes AND services AND the AI engine AND the Celery task** — all four. There is no single transactional owner. (Confirmed by section 4 inventory below.)

---

## 3. Critical-path traces (file:function by file:function)

Legend: `[FLUSH]` = `db.flush()`; `[COMMIT]` = `db.commit()`; `[MUT]` = in-memory state mutation; `[EXT]` = external network call; `[AUDIT]` = `write_audit_log` (autonomous session, self-commits).

### (a) Contract lifecycle transition (e.g. Draft → AI Review)

1. `POST /api/v1/contracts/{id}/lifecycle` → `app/contracts/routes.py:90 transition_lifecycle` (sync route; `Depends(require_permission("contract:update"))`).
2. `app/contracts/service.py:get_contract_for_user` — loads the `Contract`, applies `user_can_access_contract` (`contracts/access.py`). No write.
3. Route computes `override_authorized = has_permission(user.permission_values, "contract:lifecycle_override")` (`core/rbac.py`).
4. `app/contracts/lifecycle.py:66 transition_contract_stage(db, contract=..., to_stage=..., actor_user_id=..., override=..., override_authorized=...)`:
   - Reads `ALLOWED_TRANSITIONS[from_stage]`; raises `HTTPException(409)` if disallowed and not override; `403` if override without permission (`lifecycle.py:80-90`).
   - If `to_stage == ACTIVE` and not authorized override: queries for a `ContractVersion` with `source == SIGNED`; raises `409` if none and no `signed_confirmation` (`lifecycle.py:92-108`).
   - `[MUT]` `contract.lifecycle_stage = to_stage`; `contract.updated_by_user_id = actor_user_id` (`lifecycle.py:109-110`).
   - `[MUT]` `db.add(ContractStageHistory(...))` (`lifecycle.py:111-124`) — added to caller session, **not** flushed here.
   - `[AUDIT]` `write_audit_log(db, action="contract.lifecycle_changed", before={...}, after={...})` (`lifecycle.py:125`) → `core/audit.py:34` opens **its own** `SessionLocal()`, takes `pg_advisory_xact_lock`, computes hash chain, `durable_db.commit()` (`audit.py:82`), closes. **This audit row commits even if step 5 later rolls back.**
   - `write_timeline_event(db, ...)` (`lifecycle.py:142`) → `core/audit.py:135` does `db.add(row)` on the **caller** session; no commit.
   - returns `contract`.
5. Back in route: `[COMMIT]` `db.commit()` (`contracts/routes.py:112`) — this is the only commit that persists the stage change + `ContractStageHistory` + timeline event. Then `db.refresh(contract)`; return.

> Same `transition_contract_stage` is also invoked (with `override=True, override_authorized=True`) from `approvals/service.py`, `signatures/service.py`, `renewals/routes.py`, and `tool_runtime.py` — none of which commit inside the function; their callers own the commit.

### (b) `run_structured_skill` call (HTTP/job → Claude → validate → cite → write)

Entry varies (HTTP rerun routes in `ai/routes.py`, playbook runs, or the Celery task via `run_job_skill`). Core method: `app/ai/controller.py:885 AIController.run_structured_skill(db, *, skill_name, org_id, ..., commit=True)`.

1. `skill_registry.get(skill_name)` → `SkillSpec` (`ai/registry.py`); `_ensure_skill_enabled` checks `AdminSetting` feature flag (`controller.py:1101`), raises `RuntimeError` if disabled.
2. `get_active_prompt_bundle(db, org_id, prompt_key, ...)` (`ai/prompt_versions.py`) — resolves DB-backed prompt version.
3. `_maybe_contract_context` → `app/ai/context.py:build_contract_context` — loads contract → authoritative `ContractVersion` → `ContractTextSnapshot.text` (truncated to 25 000 chars).
4. `prompt_builder.build_structured_skill_prompt(...)` (`ai/prompt_builder.py`) → `BuiltPrompt`.
5. `[MUT]` create `AISkillRun(status=RUNNING, ...)`; `db.add(skill_run)`; `[FLUSH]` `db.flush()` (`controller.py:951-952`).
6. `write_timeline_event(db, event_type="ai.skill_started", ...)` (`controller.py:953`) — caller-session add.
7. `[EXT]` `await claude_client.complete_structured(system_prompt, user_prompt, tool_name=spec.return_tool_name, input_schema=spec.output_model.model_json_schema(), ...)` (`controller.py:969`) → `app/integrations/claude.py:66 complete_structured` → if `mock_claude` returns canned payload, else `POST https://api.anthropic.com/v1/messages` with `tool_choice={type:tool, name:return_<skill>}`, wrapped in `tenacity.AsyncRetrying` (`claude.py` retry helper, 3 attempts on 429/5xx/transport).
8. `_extract_structured_output(provider_response, spec)` (`controller.py:978`) → `spec.output_model.model_validate(raw_output)` (`controller.py:979`) — Pydantic validation.
9. `_validate_and_store_citations(db, ...)` (`controller.py:980`) → `app/ai/citations.py:validate_citations` fuzz-matches each quote vs `contract_context.text`; persists `AICitation` rows (`db.add` on caller session); returns a `validation_status`.
10. `[MUT]` set `skill_run.validation_status`, `skill_run.status` (SUCCEEDED / NEEDS_REVIEW), `skill_run.output_payload`, `skill_run.finished_at` (`controller.py:990-997`).
11. `_log_ai_call(db, ...)` (`controller.py:998`) → builds an `AICallLog`, `db.add`, `db.flush` (internal), records usage. Returns the row.
12. `_persist_skill_output(db, spec=..., output=validated, ...)` (`controller.py:1017`) — **long `if/elif` on `spec.name`** (`controller.py:_persist_skill_output`, dispatch begins ~1239 with a `db.flush()`): writes derived domain rows — `_persist_metadata` (writes `Contract.metadata_json`), `_persist_clauses` (`ClauseExtraction`), `_persist_obligations` (`Obligation` + `ObligationReminder`, `controller.py:1446-…`, flush at 1508), `_persist_renewal` (`RenewalEvent`, flush at 1606). Each `db.add`/`db.flush`, no commit.
13. `_record_usage(db, ...)` (`controller.py:1027`) — `UsageRecord` rows.
14. `write_timeline_event(db, event_type="ai.skill_succeeded", ...)` (`controller.py:1036`).
15. If `job_id`: `_finish_job(db, job_id=..., status="succeeded")` (`controller.py:1050`).
16. `[COMMIT]` `if commit: db.commit()` (`controller.py:1052-1053`) — single commit persists skill_run + call log + citations + derived domain rows + usage + timeline.
- **Failure branch** (`controller.py:1055`): `_fallback_output` (metadata skill only → `ai/fallback.py`); if fallback used, persist + `[COMMIT]` at `controller.py:1076`; else mark `AISkillRun` FAILED, timeline `ai.skill_failed`, `[COMMIT]` at `controller.py:1098`, re-raise.

### (c) Celery background job (representative: clause extraction → auto-chained brain ingestion)

1. **Enqueue:** during contract upload, `app/contract_files/service.py:_queue_initial_contract_jobs` creates `JobRun(job_type ∈ {metadata_extraction, clause_extraction, embeddings})`; `dispatch_job(db, job=...)` (`app/jobs/service.py`) calls `run_ai_job.delay(job.id)` → Celery → Redis. (Also enqueued manually by e.g. `obligations/routes.py:extract`.)
2. **Worker:** `app/jobs/tasks.py:20 run_ai_job(self, job_id)` → `asyncio.run(_run_ai_job(job_id))`. `bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3`.
3. `app/jobs/tasks.py:25 _run_ai_job`: `db = SessionLocal()` (its own session).
   - `job = db.get(JobRun, job_id)`; if `CANCELLED` return early.
   - `[MUT]` `job.status = RUNNING`; `job.started_at`; `job.attempt_count += 1`; `job.progress`. `[COMMIT]` `db.commit()` (`tasks.py:37`).
   - branch `job_type == "clause_extraction"` (`tasks.py:51`): `[EXT via controller]` `await ai_controller.run_job_skill(db, job=job, skill_name="clause_extraction", input_payload={contract_id, contract_version_id, text_snapshot_id})` → `run_structured_skill` (trace b; commits internally at `controller.py:1053`).
   - `_sync_job_from_skill_runs(db, job_id=...)` (`tasks.py:182`) — reads latest `AISkillRun`, sets `JobRun.status`/metadata, `[COMMIT]` `db.commit()` (`tasks.py:205`).
   - `_queue_contract_brain_ingestion(db, job=job, reason="after_clause_extraction")` (`tasks.py:216`): if job SUCCEEDED, builds `idempotency_key=f"contract_brain_ingestion:{version_id}:{snapshot_id}:{reason}"`, dedupes against existing `JobRun`, `create_job(...)`, `[FLUSH]` (`tasks.py:244`), `[COMMIT]` (`tasks.py:246`), then `dispatch_job(db, job=brain_job)` → `run_ai_job.delay(...)`, `[COMMIT]` (`tasks.py:251`).
4. **Chained worker run:** a second `run_ai_job` fires for `job_type == "contract_brain_ingestion"` (`tasks.py:98`): loads `Contract` + `ContractVersion` + optional `ContractTextSnapshot`; `[EXT none]` `ingest_contract_brain(db, ...)` (`app/contract_brain/ingestion.py:14`) — marks prior `KnowledgeNode`/`KnowledgeEdge` stale, walks `ContractParty`/`ClauseExtraction`/`Obligation`/`ApprovalRequest`/`SignatureRequest`/`PlaybookDeviation`, adds new nodes+edges (flushes at `ingestion.py:47,63`), writes audit+timeline. Back in task: `_mark_job_succeeded(job)`, set `graph_node_counts`, `[COMMIT]` `db.commit()` (`tasks.py:118`).
- **Failure:** outer `except` (`tasks.py:169`) sets `JobRun` FAILED + `error_stack`, `[COMMIT]` (`tasks.py:176`), re-raises (triggers Celery autoretry). `finally: db.close()` (`tasks.py:179`).

---

## 4. Dependency map

### 4.1 Module-level "X imports/calls Y" (hot modules)

- **`ai/controller.py`** → `ai/{citations,context,fallback,models,prompt_builder,prompt_versions,registry,schemas,skill,tool_policy,tool_registry,tool_runtime}`; `auth.models`; `assistant.models`; `contract_brain.models`; `contract_files.models`; `contracts.models`; `obligations.models`; `renewals.models`; `core.{audit,config,database,enums,models,rbac}`; `integrations.claude`; `jobs.models`. (Imports models from **7 domains** directly.)
- **`ai/tool_runtime.py`** → `ai/{confirmations,models,tool_policy,tool_registry,schemas}`; `assistant.models`; `auth.models`; **`approvals.service`** (`submit_contract_for_approval`); `contract_brain.retrieval`; **`contract_files.service`** (`_queue_initial_contract_jobs`, `next_version_number`); `contracts.{models,lifecycle,service}`; `obligations.models`; `approvals.models`; `renewals.models`; `jobs.{models,service}`; `core.{audit,database,enums,rbac}`; `integrations.{docusign,resend,storage}`; **`playbooks.service`**; `projects.{access,models}`; **`signatures.service`**; **`tabular_review.service`**; `workflows.models`. (Imports/calls **service functions across ≥6 domains** — the widest fan-out in the codebase.)
- **`approvals/service.py`** → `approvals.models`; `auth.models`; `contracts.{lifecycle,models}`; `core.{audit,config,enums,security}`; `integrations.resend`.
- **`contract_files/service.py`** → `auth.models`; `contract_files.{models,text_extraction}`; `contracts.models`; `core.{audit,config,enums}`; `integrations.{reducto,storage}`; `jobs.{models,service}`; `projects.{access,models}`.
- **`signatures/service.py`** → `auth.models`; `contract_files.{models,service}`; `contracts.{lifecycle,models}`; `core.{audit,enums}`; `signatures.models`.
- **`jobs/tasks.py`** → `ai.controller`; `ai.embeddings`; `ai.models`; `ai.schemas`; `contract_brain.ingestion`; `contract_files.models`; `contracts.models`; `core.{database,enums}`; `jobs.{celery_app,models}`; `tabular_review.models`.
- **`core/audit.py`** → `core.{database,models}` only (deliberately leaf-ish; but opens its own `SessionLocal`).
- **`integrations/*`** → `core.config` (+ `claude.py` → `integrations._claude_mock`). Integrations do **not** import domain modules (clean inward boundary on this side).

### 4.2 Tightly coupled / deep import chains

1. **`ai/tool_runtime.py` ↔ the whole domain layer.** It imports the *service functions* of approvals, contract_files, contracts, playbooks, signatures, tabular_review, plus jobs.service and projects.access. It is the single most coupled module — effectively a god-object dispatcher (string `tool_name` → method → cross-domain service call).
2. **`ai/controller.py` → 7 domains' models.** The skill-output persister (`_persist_skill_output`) writes directly into `Contract`, `ClauseExtraction`, `Obligation`/`ObligationReminder`, `RenewalEvent` — the AI engine reaches straight into domain tables with no service indirection.
3. **`contract_files.service` ⇄ `signatures.service` ⇄ `tool_runtime`.** `signatures.service` imports `contract_files.service`; `tool_runtime` imports both. `contract_files.service` and `tool_runtime` both import `jobs.service` (`create_job`, `dispatch_job`) — job creation is duplicated across both intake and tool paths.
4. **`jobs/tasks.py` → `ai.controller` → back into domain models.** The Celery task depends on the AI controller, which depends on domain models; the chained `contract_brain_ingestion` adds `contract_brain.ingestion`'s dependency on six domains' models. Lazy import inside `_queue_contract_brain_ingestion` (`from app.jobs.service import ...`, `tasks.py:227`) papers over a circular edge between `jobs.tasks` and `jobs.service`.
5. **`contracts.lifecycle` as a shared write-target.** Imported by `contracts.routes`, `approvals.service`, `signatures.service`, `renewals.routes`, `tool_runtime` — five callers mutate contract state through it, but the commit lives in each caller.

### 4.3 EXHAUSTIVE `db.commit()` outside any repository layer

There is **no repository layer**, so *every* commit is "outside a repository." **Total: 135 `db.commit()` / `durable_db.commit()` calls** across the app (excluding the `request_log_queue` infra writer). By file (commit count; flushes noted separately where relevant):

| File | `db.commit()` | Layer |
|---|---|---|
| `app/auth/service.py` | 19 | service |
| `app/ai/controller.py` | 16 | AI engine |
| `app/projects/routes.py` | 13 | route |
| `app/playbooks/routes.py` | 11 | route |
| `app/jobs/tasks.py` | 11 | Celery task |
| `app/assistant/routes.py` | 10 | route |
| `app/contract_files/routes.py` | 9 | route |
| `app/tabular_review/routes.py` | 8 | route |
| `app/contract_files/service.py` | 6 | service |
| `app/obligations/routes.py` | 5 | route |
| `app/approvals/routes.py` | 4 | route |
| `app/signatures/routes.py` | 3 | route |
| `app/jobs/routes.py` | 3 | route |
| `app/contract_brain/routes.py` | 3 | route |
| `app/tabular_review/service.py` | 2 | service |
| `app/renewals/routes.py` | 2 | route |
| `app/ai/tool_runtime.py` | 2 | AI engine |
| `app/ai/routes.py` | 2 | route |
| `app/workflows/routes.py` | 1 | route |
| `app/organizations/routes.py` | 1 | route |
| `app/contracts/service.py` | 1 | service |
| `app/contracts/routes.py` | 1 | route |
| `app/admin/routes.py` | 1 | route |
| `app/core/audit.py` | 1 | cross-cutting (autonomous session) |

Exact line numbers for every commit (grep `db\.commit()` / `durable_db\.commit()`):

- **AI engine** — `ai/controller.py`: 148, 206, 238, 259, 291, 325, 399, 486, 516, 528, 559, 592, 736, 1053, 1076, 1098. `ai/tool_runtime.py`: 690, 717. `ai/routes.py`: 111, 175.
- **Celery task** — `jobs/tasks.py`: 37, 97, 118, 127, 156, 161, 167, 176, 205, 246, 251.
- **Services** — `auth/service.py`: 219, 256, 278, 304, 319, 333, 367, 456, 546, 584, 622, 696, 751, 783, 849, 873, 909, 924, 960. `contract_files/service.py`: 273, 372, 467, 475, 514, 522. `contracts/service.py`: 107. `tabular_review/service.py`: 47, 55.
- **Routes** — `projects/routes.py`: 142, 177, 202, 270, 314, 356, 425, 460, 526, 568, 634, 675, 708. `playbooks/routes.py`: 77, 99, 152, 189, 212, 259, 297, 351, 384, 411, 468. `assistant/routes.py`: 141, 171, 192, 289, 373, 381, 455, 460, 475, 498. `contract_files/routes.py`: 198, 247, 349, 415, 499, 531, 558, 601, 633. `tabular_review/routes.py`: 155, 223, 298, 355, 370, 413, 457, 484. `obligations/routes.py`: 104, 127, 164, 167, 230. `approvals/routes.py`: 90, 112, 139, 163. `signatures/routes.py`: 136, 209, 286. `jobs/routes.py`: 38, 80, 98. `contract_brain/routes.py`: 124, 197, 200. `renewals/routes.py`: 101, 159. `workflows/routes.py`: 50. `organizations/routes.py`: 55. `contracts/routes.py`: 112. `admin/routes.py`: 66.
- **Cross-cutting** — `core/audit.py`: 82 (`durable_db.commit()`, autonomous `SessionLocal`).
- **Infra (not application data; listed for completeness, excluded from the 135)** — `core/request_log_queue.py`: 52, 73 (batched RequestLog writer thread).

`db.flush()` sites are far more numerous (≈85; concentrated in `ai/tool_runtime.py` ≈40, `playbooks/service.py` ≈11, `ai/controller.py`, `contract_files/service.py`). Services predominantly `flush()` and defer the `commit()` to their route caller — except `auth/service.py` and `contract_files/service.py`, which commit themselves.

### 4.4 Where transactional boundaries live today

- **Opener:** `app/core/deps.py:17 get_db()` — `db = SessionLocal()`, `yield db`, `except: db.rollback(); raise`, `finally: db.close()`. It does **not** commit.
- **Committer (HTTP):** the route function, after calling its service(s). The dominant pattern is `service_fn(db, ...); db.commit(); db.refresh(obj); return obj`.
- **Committer (services that self-commit):** `auth/service.py` (every mutating fn), `contract_files/service.py` (e.g. `create_contract_from_upload` at 273, with rollback+`storage_service.delete_bytes_permanently` on failure), `contracts/service.py`, `tabular_review/service.py`.
- **Committer (AI engine):** `AIController` commits inside `run_structured_skill` (guarded by `commit=True` param), and inside `stream_assistant_run`/`resume_assistant_run` it commits **per tool-loop iteration** and after each Claude call log (`controller.py:206/238/259/291/325` and `486/516/528/559/592`). `ToolRuntime` commits at `690/717`.
- **Committer (background):** `jobs/tasks.py:_run_ai_job` owns its session and commits at each phase boundary (11 sites); `finally: db.close()`.
- **Rollback owners:** `get_db()` (HTTP exceptions); `_run_ai_job` outer `except` (marks job FAILED then commits, does not rollback the failed work — it sets failure fields and commits those); `create_contract_from_upload` (explicit rollback + storage cleanup); `core/audit.py` (rolls back + re-raises in its autonomous session).
- **Independent transaction:** `core/audit.py:write_audit_log` always runs in a fresh `SessionLocal()` and commits regardless of the caller's transaction outcome — so an audit row can persist even when the operation it describes is later rolled back, and vice-versa.

---

## 5. External integrations & assumptions

All live under `app/integrations/`; each has a `settings.mock_<name>` switch and `validate_runtime_settings` (`core/config.py`) refuses to boot non-local with any mock on.

### Anthropic / Claude — `app/integrations/claude.py`
- **Instantiation:** module-level singleton `claude_client = ClaudeClient()` (`claude.py:346`). HTTP transport is a **long-lived shared `httpx.AsyncClient`** created lazily by `_client()` (`claude.py:41-45`, `_CLAUDE_TIMEOUT = httpx.Timeout(600.0, connect=10.0)`); closed at app shutdown via `aclose_claude_client()` (registered in `main.py` lifespan).
- **Methods:** `complete_structured` (`tool_choice={type:tool, name:return_<skill>}`, `claude.py:66`), `complete_with_tools` (`tool_choice:auto`, assistant loop), `complete_text` (fallback).
- **Retry/timeout:** `tenacity.AsyncRetrying`, `stop_after_attempt(claude_max_retries=3)`, `wait_exponential(multiplier=1.0, max=30.0)`, predicate `_should_retry` → retry on HTTP 429, 5xx, and `httpx.TransportError`/`TimeoutException` (`claude.py:56-60`). Request timeout 600 s.
- **Assumptions:** `POST https://api.anthropic.com/v1/messages`, header `anthropic-version: 2023-06-01`, model `settings.claude_model`. Response shape assumed to contain `content` blocks of type `text`/`tool_use`, a `stop_reason`, and `usage` tokens — destructured into `ClaudeProviderResponse` (`claude.py:22-31`). Mock path (`mock_claude=True`, default) returns canned `tool_use` payloads from `_claude_mock.structured_payload_by_tool()` / keyword routing via `select_mock_tool()` (`claude.py:16`).

### DocuSign — `app/integrations/docusign.py`
- **Instantiation:** singleton `docusign_client = DocuSignClient()` (`docusign.py:186`); long-lived shared `httpx.AsyncClient` lazily created (`docusign.py:33`).
- **Auth:** JWT (RS256) assertion signed with the PEM at `settings.docusign_private_key_path` (cached in-process), POSTed to `<oauth_base>/oauth/token`.
- **Assumptions:** envelope create POSTs base64 doc to `<rest_base>/v2.1/accounts/<account_id>/envelopes`; webhook verified by `verify_connect_signature` (HMAC-SHA256, `hmac.compare_digest` vs `X-DocuSign-Signature-1`) and **rejects when no `docusign_connect_hmac_key` configured**. Requires `DOCUSIGN_{INTEGRATION_KEY,USER_ID,ACCOUNT_ID,PRIVATE_KEY_PATH}`. `void_envelope` is best-effort on rollback.

### Reducto (OCR) — `app/integrations/reducto.py`
- **Instantiation:** singleton `reducto_client = ReductoClient()` (`reducto.py:67`); uses the `reducto.AsyncReducto` SDK (constructed per the SDK's own client mgmt). Writes upload to a tmpfile (`tmp.flush()` at `reducto.py:42`).
- **Assumptions:** `client.upload(file=...)` then `client.parse.run(input=...)`; concatenates chunk text; sets `quality_score = 0.9 if text else 0.0`. Mock returns `""`. Invoked only when native extraction yields `needs_ocr` or `quality_score < 0.55` (`contract_files/text_extraction.py`).

### Resend (email) — `app/integrations/resend.py`
- **Instantiation:** singleton `resend_client = ResendClient()` (`resend.py:37`), but **opens a fresh `httpx.AsyncClient(timeout=30)` per call** (`resend.py:21`) — not long-lived (diverges from Claude/DocuSign posture).
- **Assumptions:** `POST https://api.resend.com/emails` with `{from, to, subject, html}`. Called from `approvals.service`, `signatures.service`, `obligations/routes.py:run-reminders`, `renewals/routes.py`, and auth flows. No tenacity retry visible.

### Postgres / pgvector — `app/core/database.py`
- **Instantiation:** one module-level `engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size, max_overflow, pool_recycle, pool_timeout)` (`database.py:72-79`) and one `SessionLocal = sessionmaker(... expire_on_commit=False)` (`database.py:84-86`). DSN `postgresql+psycopg://...` (psycopg 3).
- **Assumptions:** pgvector extension present (`CREATE EXTENSION IF NOT EXISTS vector` in migrations 0001/0002); `ContractEmbedding.embedding` is `pgvector.sqlalchemy.Vector(384)`; cosine search in `contract_brain/retrieval.py`. Audit chain assumes `pg_advisory_xact_lock` (guarded `try/except` so SQLite test runs fall through, `audit.py:54-60`). Explicit `NAMING_CONVENTION` so Alembic constraint names are deterministic.

### Redis / Celery — `app/jobs/celery_app.py`, `app/jobs/tasks.py`
- **Instantiation:** `celery_app` configured with `broker=settings.redis_url` and Redis result backend (single Redis instance for both). One worker container. Single task `run_ai_job` autoloaded from `app.jobs.tasks`.
- **Assumptions:** Redis available as both broker and backend; the task body opens its own `SessionLocal` (DB reachable from the worker process); `run_ai_job.delay(job_id)` is the only enqueue entrypoint, called from `jobs.service.dispatch_job`. `autoretry_for=(Exception,)`, `retry_backoff=True`, `max_retries=3`. Stuck jobs reaped lazily by `jobs/routes.py:_reap_stuck_jobs` (20 min TTL) on each list call — there is no Celery beat / cron.

---

*End of Architecture Reality Document. Descriptive only.*
