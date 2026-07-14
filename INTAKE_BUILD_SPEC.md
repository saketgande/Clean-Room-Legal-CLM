# AEGIS Legal Intake — Engineering Build Spec

One legal front door inside the Clean-Room CLM: **capture → triage → route → SLA → audit**, with AI agents that draft and a named human who decides. All 12 surfaces of the reference implementation (My Work, Inbox, Triage Cockpit, Kanban, New Request, My Requests, Self-Service, SLA Dashboard, Pool Ops, Smart Routing, Teams, Request Types), rebuilt natively on the CLM stack — FastAPI/SQLAlchemy/Alembic + Next.js 15 App Router — reusing the CLM's audit chain, AI skill pipeline, confirmations, authority (DoA), RBAC, projects, and Celery infrastructure.

**How this document was produced.** Three research passes (the CLM's own conventions extracted from its code; field-level shapes extracted from the reference intake implementation; web engineering research on ticketing state machines, SLA engines, custom fields, race-safe assignment, and keyboard triage UIs) fed two spec authors (Parts 1–2) and a planner (Part 3). An adversarial review then hunted defects in the drafts and found 28 (5 blockers). **Part 0 resolves all 28** — where Parts 1–3 conflict with Part 0, **Part 0 wins**.

**How to read it.** Implementers: read Part 0 fully, then implement Part 3 task by task, using Parts 1–2 as the detailed reference (as amended by Part 0). The appendix records the review findings for traceability.

---

## PART 0 — Binding corrections (supersede Parts 1–3)

### API contract (resolves the blocker API mismatch)
0.1 **One noun: `/intake/requests/*`.** The frontend `intakeApi` is rewritten verbatim against the backend §6 route table. No `/intake/tickets/*` anywhere.
0.2 **Added routes:** `GET /intake/requests/{id}/handoffs` (custody list; intake:read or requester-owner); `POST /intake/sla-scan` (`admin_panel:access`; synchronously runs the *same* sweep function as the Celery beat task and returns its counter dict — never fork the logic); `GET /intake/agents` (intake:triage; read-only agent-registry metadata — the frontend "Agent settings" modal becomes this read-only list); `POST /intake/extract-text` (intake:create; multipart file → extracted text via the existing contract-files extraction service; the New Request form folds the text into the description client-side — **no attachment storage in v1**).
0.3 `moveStage`/`advanceStage` fold into `PATCH /intake/requests/{id}` with `{stage}` (transition T10 governs). `spawned_matter_id` is **deleted everywhere** — promotion to matter/contract happens only via the explicit promote endpoint (T11); triage never spawns matters. Bulk verdicts: `POST /intake/requests/bulk-triage` `{ids, action}` → `{results: [{id, ok, error?}]}`.

### Approve seam (resolves 2 blockers)
0.4 **Do not call** `confirm_confirmation`/`reject_confirmation` from `app/ai/confirmations.py` (its permission check 403s non-admin reviewers, and it dereferences a NULL `tool_call_id`). New `service._decide_intake_confirmation(db, confirmation, actor, approve)` in `app/intake/service.py`: `SELECT … FOR UPDATE`, 409 if not `PENDING`, flip status, write its own audit row, skip the AssistantToolCall mirror. The route has already enforced `intake:triage` + the approval gate + authority before this is called.
0.5 **Confirmation `expires_at` = NULL for intake.** The structural gate is "PENDING has no exit except `record_triage_action`". SLA-linked expiry created a terminal trap (overdue requests could never be approved) and broke the backdated seed.
0.6 **Authority shim** passed to `enforce_authority` is a dataclass with **all six** attributes `_grant_covers` reads: `value_amount=None, currency=None, contract_type=(request_type.key if typed else type_label), jurisdiction=None, risk_band=None, risk_level=None`. Convention: intake approve grants use `max_value=None` and `allowed_contract_types` matched against intake type keys.
0.7 Approve with no pending recommendation → **422 "No draft to approve — use manual close."** (The Cockpit `a` shortcut already guards on recommendation presence; bulk-triage reports it per-id in `results`.)

### Status model (resolves the terminal-trap majors)
0.8 Request `status` enum = `awaiting_triage | in_review | escalated | approved | closed` — **`rejected` is not a request status.** A rejected recommendation returns the request to `status=in_review`, `stage='triage'`, SLA still running, `triage_action='rejected'` recorded. A rejected-then-forgotten request is the failure mode intake exists to prevent.
0.9 `OPEN_STATUSES = {awaiting_triage, in_review, escalated}`; `TERMINAL_STATUSES = {approved, closed}` — **disjoint**. Pool capacity, my-work, and the SLA sweep key on OPEN only.
0.10 New column `intake_request.closed_at TIMESTAMPTZ NULL`, stamped **exactly once** inside `_transition` when status enters TERMINAL. `build_sla_legs` and all aggregates use `closed_at` — never `updated_at` (which moves on every touch and would retroactively rewrite SLA evidence).
0.11 Escalated-at-birth hole closed: T2/T3 from-state = *any non-terminal, untriaged status*; agent processing **never downgrades** status (an escalated request stays escalated; recommendation + batons still written). The SLA sweep also scans `escalated` rows and fires the `intake.sla_breached` audit/timeline row once on the posture edge.

### Kanban + permissions (resolves the privilege blockers)
0.12 Drag-to-Complete requires `intake:triage` and executes **manual-close semantics through the triage endpoint with a confirm modal** (non-optimistic). The Complete drop target is hidden for users without `intake:triage`. Custom mid-stages bucket **by position** into the In Review column; cross-column drag is disabled for custom-stage requests (their stage advances live on the detail chip rail); optimistic status derives from the T10 rule (mid-stage → `in_review`), not a name-keyed map.
0.13 **Permission split (privacy):** `intake:create` (members/all employees — submit, `/requests/mine`, copilot, `GET /intake/kb`, extract-text); `intake:read` (staff-wide list/detail/timeline/SLA-legs — legal_reviewer + approver + admin, **not** member); `intake:triage` (verdicts, bulk, cockpit, sla-ops, routing-rules read, agents read); `intake:update` (stage/handoff/tasks/work-status, assignees read); admin mutations stay on `admin_panel:access`. `get_request` additionally allows `requester_user_id == actor.id` (own-ticket read-only detail). Employees must never be able to enumerate org-wide requests (harassment/litigation content).
0.14 `GET /intake/routing-rules` → `intake:triage` (read-only for staff); POST/PATCH/DELETE stay admin. `GET /intake/assignees` → `intake:update` (the handoff dialog needs it).

### Engine details
0.15 **Pool balancer:** reject overflow **cycles** (walk the whole chain) at team save time, *and* acquire member-row locks in deterministic global order — resolve the overflow chain's team ids, sort, lock all member rows in one query ordered by `(team_id, id)`. Both; the cycle check is UX, the lock order is correctness.
0.16 **SLA posture:** the sweep recomputes posture in **both directions** and persists on any edge (`overdue→at_risk→on_track` included); escalation notifications fire only on the upward edge (dedup via the stored `sla_status` edge). **Pause** is idempotent: pause-while-paused = no-op 200; resume-while-not-paused = no-op 200; pause on a terminal request = 409 (same closed-is-immutable check as `_transition`).
0.17 **Registration:** `app/models.py` imports **all 10** intake model classes and appends all 10 to `__all__` (the spec's "8" is wrong). Migration also adds `CREATE INDEX IF NOT EXISTS ix_intake_request_org_requester ON intake_request(org_id, requester_user_id)` (+ DROP in downgrade) — `/requests/mine` must not seq-scan.
0.18 Copilot skill is registered with a **truthful synchronous execution mode** (it runs via `run_structured_skill`, no JobRun) — the registry must not claim `job`.

### Frontend contract
0.19 `lib/types.ts` mirrors the backend serializers **exactly**: snake_case enum literals (`on_track/at_risk/overdue`; the 0.8 status enum; `manual_close/edited_approved`; `no_match`; recommendation statuses `pending/approved/edited/rejected`; `actor_type: user|agent|system`; fields `citations`, `short_form_reply`, `degraded`, `contract_id`, `project_id`). Presentation labels ("At Risk", "Completed") live in a label-map module (`shared.ts`), never in wire types. "Snoozed" is `triage_action='snoozed'` + `snoozed_until`, not a status.
0.20 `humanizeEvent` keys = backend audit action strings **verbatim**: `intake.created, intake.assigned, intake.handoff, intake.stage_advanced, intake.sla_breached, intake.auto_escalated, intake.closed, intake.agent_no_match, intake.approval_blocked, intake.promoted, intake.paused, intake.resumed, intake.routing_rule.fired, intake.approved, intake.rejected`.
0.21 `get_sla_ops_summary` returns the **union** shape and the frontend interface matches field-for-field: `{generated_at, open_total, awaiting_triage, escalated, on_track, at_risk, overdue, paused, avg_elapsed_pct, breaches_7d, by_holder, oldest_open, workload:[{user_id,name,open,overdue}], rule_effectiveness:[{id,name,times_fired,last_fired_at}] (top 5)}`.
0.22 Self-Service has **no static `kb.ts`** — it fetches `GET /intake/kb` (category chips derived from tags; the article-count stat from the same query). Admin KB edits and the FAQ agent cite the same rows: one source of truth.
0.23 The Timeline panel feeds from **audit rows only** (never pruned, carry `row_hash`): `hash_prefix = row_hash[:8]`, `chain_position` = per-resource ordinal explicitly labeled as such. Mixed feeds with prunable rows must not sit under a "CHAIN-SEALED · TAMPER-EVIDENT" banner.
0.24 Permission helper supports the RBAC wildcard: `const can = (p) => !!user?.permissions?.some(x => x === p || x === "*")`.
0.25 Seed: the backdated −30h request is the **no-match, queue-held** one (no recommendation), so nothing seeded is born expired or gated.

---



<!-- ================================================== -->
# PART 1 — Backend spec
> Amended by Part 0 — where they conflict, Part 0 wins.

# Backend Engineering Spec — Native Legal Intake Module (`app/intake/`)

Target repo: `/Users/saketgande/Desktop/EY_applications/aegis-for-production-new/Clean-Room-Legal-CLM-prod`
Module: `backend/app/intake/` — `__init__.py` (empty), `models.py`, `schemas.py`, `service.py`, `routes.py` — exactly the `app/authority/` pattern. AI additions live in the existing `app/ai/` files plus one new `app/intake/agents.py` (agent registry is code-config, not a table). Migration continues from head `0021_authority_grants`.

---

## 1. Tables

All models compose `(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base)` in that order — so every table implicitly has `id VARCHAR(36) PK`, `org_id VARCHAR(36) NOT NULL ix`, `created_by_user_id/updated_by_user_id VARCHAR(36) NULL`, `created_at/updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`. Only the additional columns are listed. Enum-ish fields are `VARCHAR` with inline value comments, validated in `service.py` (never DB enums). List/dict payloads are `Column(JSON)` → `JSONB` in the migration. No `SoftDeleteMixin` anywhere in this module (closed is terminal, never deleted).

### 1.1 `intake_request` (class `IntakeRequest`)

| column | type | null | default | notes |
|---|---|---|---|---|
| ref | VARCHAR(20) | NO | — | human id `REQ-4123`; `uq_intake_request_org_ref (org_id, ref)`; from Postgres sequence `intake_ref_seq` |
| source | VARCHAR(20) | NO | `'form'` | form\|copilot\|email\|api\|seed |
| requester_user_id | VARCHAR(36) | NO | — | FK `"user"(id)`; ix. Server-authoritative = actor on create |
| requester_name | VARCHAR(200) | YES | — | display fallback for email-sourced externals |
| department | VARCHAR(60) | YES | — | picklist validated in service |
| request_type_id | VARCHAR(36) | YES | — | FK `intake_request_type(id)` ON DELETE SET NULL |
| type_label | VARCHAR(120) | NO | — | free-text type ("NDA Request") — coexists with typed FK |
| description | TEXT | NO | `''` | typed desc + `[Request details]` label:value lines + attached doc text |
| field_values | JSON | YES | — | `{field.key: value}` answers; validated against `intake_request_field` rows at submit |
| priority | VARCHAR(20) | NO | `'Medium'` | Critical\|High\|Medium\|Low |
| status | VARCHAR(40) | NO | `'awaiting_triage'` | awaiting_triage\|in_review\|approved\|rejected\|escalated\|closed; ix `(org_id, status)` |
| stage | VARCHAR(60) | NO | `'new'` | spine + custom stage names; ix `(org_id, stage)` |
| work_status | VARCHAR(40) | YES | — | not_started\|in_progress\|blocked\|delivered (delivery, distinct from status) |
| assigned_to_user_id | VARCHAR(36) | YES | — | FK `"user"(id)`; ix `(org_id, assigned_to_user_id)` |
| approval_gate_user_id | VARCHAR(36) | YES | — | FK `"user"(id)`; server-owned, only rules write it |
| sla_hours | INTEGER | NO | `24` | |
| sla_status | VARCHAR(20) | NO | `'on_track'` | on_track\|at_risk\|overdue — persisted by sweep; UI recomputes live pct |
| paused_at | TIMESTAMPTZ | YES | — | waiting-on-requester clock stop (set = paused now) |
| paused_ms_total | BIGINT | NO | `0` | accumulated pause; added to breach instant |
| submitted_at | TIMESTAMPTZ | NO | `now()` | ix `(org_id, submitted_at)` |
| triaged_by_user_id | VARCHAR(36) | YES | — | server ALWAYS stamps actor on newly-firing triage action (anti-spoof) |
| triaged_at | TIMESTAMPTZ | YES | — | |
| triage_action | VARCHAR(30) | YES | — | approved\|edited_approved\|rejected\|reassigned\|manual_close\|snoozed |
| agent_processed_at | TIMESTAMPTZ | YES | — | null→set transition gates auto-baton + no-match audit (dedupe) |
| agent_outcome | VARCHAR(20) | YES | — | matched\|no_match |
| ai_triage | JSON | YES | — | classifier stamp {category, risk_flag, confidence, complexity, routing_rule, source} |
| fired_rules | JSON | YES | — | server-computed {rule_ids:[], fired_at, summaries:[{id,name,actions[]}]} |
| stage_timestamps | JSON | YES | — | append-only `[{stage, at}]`, server-owned |
| conversation | JSON | YES | — | copilot transcript `[{role, content, ts, fields_extracted?}]` — JSON column, no child table |
| handoff_holder | VARCHAR(10) | YES | — | agent\|human\|queue — denormalized; `intake_handoff` is truth |
| handoff_user_id | VARCHAR(36) | YES | — | set only when holder=human |
| handoff_updated_at | TIMESTAMPTZ | YES | — | |
| external_message_id | VARCHAR(200) | YES | — | email/webhook dedupe; partial unique ix `(org_id, external_message_id) WHERE external_message_id IS NOT NULL` |
| project_id | VARCHAR(36) | YES | — | selective promotion target (matter); plain column, no FK |
| contract_id | VARCHAR(36) | YES | — | FK `contract(id)` ON DELETE SET NULL; set on promotion only |

Skipped vs source: `workflow` JSON (derived in `serialize_request` from spine + type stages + current stage — never stored), `IntakeTicketAssignment`/`IntakeTicketParty` tables (v1 has one primary assignee + tasks; add tables when multi-role delivery is asked for), `assigned` free-text (serializer resolves name from FK).

### 1.2 `intake_request_type` (class `IntakeRequestType`)

| column | type | null | default | notes |
|---|---|---|---|---|
| key | VARCHAR(60) | NO | — | `^[a-z0-9][a-z0-9_-]*$`; `uq (org_id, key)` |
| name | VARCHAR(120) | NO | — | |
| workstream | VARCHAR(120) | YES | — | grouping label |
| description | TEXT | YES | — | |
| active | BOOLEAN | NO | `true` | list defaults to active-only |
| stages | JSON | YES | — | ordered custom mid-stage names; NULL = default spine |
| sort_order | INTEGER | NO | `100` | |

### 1.3 `intake_request_field` (class `IntakeRequestField`)

| column | type | null | default | notes |
|---|---|---|---|---|
| request_type_id | VARCHAR(36) | NO | — | FK `intake_request_type(id)` ON DELETE CASCADE; `relationship(..., cascade="all, delete-orphan", lazy="selectin")` on the type (walls child pattern) |
| key | VARCHAR(60) | NO | — | same regex; `uq (request_type_id, key)` |
| label | VARCHAR(120) | NO | — | |
| kind | VARCHAR(20) | NO | `'text'` | text\|textarea\|select\|date\|number\|boolean |
| required | BOOLEAN | NO | `false` | boolean kind requires explicit true/false when required |
| sort_order | INTEGER | NO | `100` | |
| options | JSON | YES | — | `[{value,label}]`, select only |

Update semantics: fields replaced wholesale when provided (delete-orphan + recreate), per source.

### 1.4 `intake_routing_rule` (class `IntakeRoutingRule`)

| column | type | null | default | notes |
|---|---|---|---|---|
| name | VARCHAR(120) | NO | — | |
| description | TEXT | YES | — | |
| enabled | BOOLEAN | NO | `true` | |
| eval_order | INTEGER | NO | `100` | asc; ix `(org_id, enabled, eval_order)` |
| match_type | VARCHAR(120) | YES | — | CONDITIONS: all non-null AND together |
| match_priority | VARCHAR(20) | YES | — | exact eq |
| match_department | VARCHAR(60) | YES | — | exact eq |
| match_keyword | VARCHAR(200) | YES | — | case-insensitive substring of description |
| match_complexity | VARCHAR(20) | YES | — | simple\|standard\|complex |
| set_assignee_user_id | VARCHAR(36) | YES | — | ACTIONS; FK `"user"(id)` |
| set_priority | VARCHAR(20) | YES | — | |
| set_sla_hours | INTEGER | YES | — | |
| set_team_id | VARCHAR(36) | YES | — | FK `intake_team(id)` ON DELETE SET NULL — route-to-pool |
| escalate_to_user_id | VARCHAR(36) | YES | — | assign + raise Critical + status escalated |
| require_approval_from_user_id | VARCHAR(36) | YES | — | stamps `approval_gate_user_id` |
| times_fired | INTEGER | NO | `0` | honest counter (no-op match ≠ firing) |
| last_fired_at | TIMESTAMPTZ | YES | — | |

Validation (`assertRuleSemantics` port): ≥1 condition AND ≥1 action; name required; targets must exist in org; `None`-in-payload clears, absent preserves. Name mirrors (assignee_name etc.) are NOT stored — `serialize_rule(db, row)` resolves labels (authority pattern).

### 1.5 `intake_team` (class `IntakeTeam`)

| column | type | null | default | notes |
|---|---|---|---|---|
| key | VARCHAR(60) | NO | — | normalized slug; `uq (org_id, key)` |
| name | VARCHAR(120) | NO | — | |
| description | TEXT | YES | — | |
| active | BOOLEAN | NO | `true` | |
| strategy | VARCHAR(20) | NO | `'least_loaded'` | least_loaded\|round_robin |
| overflow_team_id | VARCHAR(36) | YES | — | FK `intake_team(id)` ON DELETE SET NULL; self-overflow rejected in service; cycles broken at resolve |
| sort_order | INTEGER | NO | `100` | |

Delete: service NULLs routing-rule `set_team_id` and other teams' `overflow_team_id` pointing at it, then deletes (members cascade).

### 1.6 `intake_team_member` (class `IntakeTeamMember`)

| column | type | null | default | notes |
|---|---|---|---|---|
| team_id | VARCHAR(36) | NO | — | FK `intake_team(id)` ON DELETE CASCADE; `uq (team_id, user_id)` |
| user_id | VARCHAR(36) | NO | — | FK `"user"(id)` |
| capacity | INTEGER | NO | `0` | ≥0; 0 = unbounded (never at capacity, excluded from capacity_total) |
| active | BOOLEAN | NO | `true` | |
| last_assigned_at | TIMESTAMPTZ | YES | — | round-robin cursor; NULLS FIRST = never-picked wins |

### 1.7 `intake_handoff` (class `IntakeHandoff`) — append-only custody ledger

| column | type | null | default | notes |
|---|---|---|---|---|
| request_id | VARCHAR(36) | NO | — | FK `intake_request(id)` ON DELETE CASCADE; ix `(request_id, created_at)` |
| from_holder | VARCHAR(10) | YES | — | agent\|human\|queue; NULL on first pass |
| to_holder | VARCHAR(10) | NO | — | agent\|human\|queue |
| to_user_id | VARCHAR(36) | YES | — | required when to_holder=human |
| reason | VARCHAR(300) | YES | — | |
| actor_type | VARCHAR(10) | NO | `'user'` | user\|agent\|system (actor user id = mixin created_by_user_id, NULL for agent/system) |
| recommendation_id | VARCHAR(36) | YES | — | FK `intake_agent_recommendation(id)` ON DELETE SET NULL — governing rec on the review-gate pass |

Never updated, never deleted (except request cascade). `created_at` is the event timestamp.

### 1.8 `intake_agent_recommendation` (class `IntakeAgentRecommendation`)

| column | type | null | default | notes |
|---|---|---|---|---|
| request_id | VARCHAR(36) | NO | — | FK `intake_request(id)` ON DELETE CASCADE; ix |
| agent_id | VARCHAR(60) | NO | — | nda_agent, faq_agent, … |
| confidence | FLOAT | NO | `0` | 0–1 |
| suggested_action | VARCHAR(40) | NO | `'flag_for_review'` | approve_and_send\|flag_for_review\|escalate |
| drafted_response | TEXT | NO | `''` | |
| reasoning | TEXT | NO | `''` | |
| concerns | JSON | YES | — | string[] |
| citations | JSON | YES | — | `[{id,title}]` (KB article ids / playbook refs) |
| short_form_reply | TEXT | YES | — | alternativeTone port |
| degraded | BOOLEAN | NO | `false` | Claude-down template fallback |
| status | VARCHAR(20) | NO | `'pending'` | pending\|approved\|edited\|rejected; ix `(org_id, status)` |
| reviewed_by_user_id | VARCHAR(36) | YES | — | |
| reviewed_at | TIMESTAMPTZ | YES | — | |
| override_reason | VARCHAR(300) | YES | — | "Attorney edited the drafted response before approval." on edited_approved |
| edited_at | TIMESTAMPTZ | YES | — | |
| skill_run_id | VARCHAR(36) | YES | — | links AISkillRun — model id/prompt hash/tokens live THERE |
| confirmation_id | VARCHAR(36) | YES | — | links the AIConfirmation gate row |

The source's separate `AgentDecision` governance table is **collapsed**: verdict = `status` + the linked `AIConfirmation`; model/prompt/telemetry evidence = the linked `AISkillRun`/`AICallLog` (already immutable-ish and pruned per policy); the executed-action audit link = the confirmation's audit rows. One table instead of two.

### 1.9 `intake_task` (class `IntakeTask`)

| column | type | null | default | notes |
|---|---|---|---|---|
| request_id | VARCHAR(36) | NO | — | FK `intake_request(id)` ON DELETE CASCADE; ix |
| title | VARCHAR(200) | NO | — | |
| description | TEXT | YES | — | |
| assignee_user_id | VARCHAR(36) | YES | — | FK `"user"(id)` |
| status | VARCHAR(20) | NO | `'open'` | open\|in_progress\|blocked\|done |
| sort_order | INTEGER | NO | `100` | |
| effort_minutes | INTEGER | NO | `0` | fast-read total; each increment audited `intake.task.effort_logged` |

### 1.10 `intake_kb_article` (class `IntakeKbArticle`) — warranted

Warranted because the FAQ/Policy-QA agents need citable, admin-editable grounding, and citations must reference real ids (requires_citations discipline). Source kept these in JS code; a table makes them tenant-editable and gives the triage prompt real snippets.

| column | type | null | default | notes |
|---|---|---|---|---|
| source_ref | VARCHAR(120) | NO | — | citation label e.g. `POLICY-REMOTE-WORK`, `NDA-TEMPLATE-v4.2`; `uq (org_id, source_ref)` |
| title | VARCHAR(200) | NO | — | |
| body | TEXT | NO | — | |
| tags | JSON | YES | — | match keywords (string[]) for retrieval |
| active | BOOLEAN | NO | `true` | |

Retrieval is code: lowercase keyword-overlap score of request description vs tags/title, top 3 injected into the triage prompt as `kb_snippets`. No embeddings, no GIN index. `# ponytail: keyword match, swap for pgvector if KB grows past ~200 articles`.

### 1.11 Migration

One file: `backend/alembic/versions/0022_legal_intake.py`

```
revision = "0022_legal_intake"
down_revision = "0021_authority_grants"
branch_labels = None
depends_on = None
```

Docstring: `"""Legal intake module (Phase 5 — native intake).\n\n<intent paragraph>\n\nRevision ID: 0022_legal_intake\nRevises: 0021_authority_grants\nCreate Date: 2026-07-10\n"""`. Imports: `from alembic import op` only. All raw idempotent SQL via `op.execute`, in this order:

1. `CREATE SEQUENCE IF NOT EXISTS intake_ref_seq START WITH 4001` (ref = `'REQ-' || nextval`)
2. `CREATE TABLE IF NOT EXISTS intake_request_type (...)` — id VARCHAR(36) PRIMARY KEY, org_id VARCHAR(36) NOT NULL, VARCHAR sizes matching models, JSONB for JSON, TIMESTAMPTZ, created_at/updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
3. `intake_request_field` (`REFERENCES intake_request_type(id) ON DELETE CASCADE`)
4. `intake_team` (self-ref overflow: add the FK inline `REFERENCES intake_team(id) ON DELETE SET NULL`)
5. `intake_team_member` (CASCADE to team, `REFERENCES "user"(id)`)
6. `intake_routing_rule` (`REFERENCES intake_team(id) ON DELETE SET NULL`, `REFERENCES "user"(id)` ×3)
7. `intake_request` (`REFERENCES "user"(id)` for requester/assignee/gate, `REFERENCES intake_request_type(id) ON DELETE SET NULL`, `REFERENCES contract(id) ON DELETE SET NULL`)
8. `intake_agent_recommendation` (CASCADE to request)
9. `intake_handoff` (CASCADE to request, `REFERENCES intake_agent_recommendation(id) ON DELETE SET NULL`)
10. `intake_task` (CASCADE to request)
11. `intake_kb_article`
12. Indexes, each a separate `CREATE INDEX IF NOT EXISTS ix_<table>_<col/concept> ON ...`: `ix_intake_request_org_status`, `ix_intake_request_org_stage`, `ix_intake_request_org_assignee`, `ix_intake_request_org_submitted`, `ix_intake_handoff_request_created`, `ix_intake_routing_rule_org_eval`, `ix_intake_agent_recommendation_request`, `ix_intake_agent_recommendation_org_status`, `ix_intake_task_request`; uniques: `CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_request_org_ref ...`, `uq_intake_request_type_org_key`, `uq_intake_request_field_type_key`, `uq_intake_team_org_key`, `uq_intake_team_member_team_user`, `uq_intake_kb_article_org_ref`, and partial `uq_intake_request_org_extmsg ON intake_request(org_id, external_message_id) WHERE external_message_id IS NOT NULL`

`downgrade()`: DROP INDEX/TABLE IF EXISTS in exact reverse order + `DROP SEQUENCE IF EXISTS intake_ref_seq`. Run via `make migrate`.

Registration: import all 8 model classes in `app/models.py` and append to `__all__`.

---

## 2. State machine

### 2.1 Status enum (code constants in `app/intake/service.py`, strings in DB)

```python
STATUSES = {"awaiting_triage", "in_review", "approved", "rejected", "escalated", "closed"}
TERMINAL_STATUSES = {"approved", "rejected", "closed"}      # SLA clock stops (legs closed_ts = triaged_at or updated_at)
OPEN_STATUSES = {"awaiting_triage", "in_review", "approved", "escalated"}  # counts toward capacity/my-work/pool-ops (port of OPEN_TICKET_STATUSES)
IMMUTABLE_STATUSES = {"closed"}                             # closed is terminal-immutable; follow-up = new request
```

### 2.2 Stage model — fixed spine book-ending per-type custom stages

```
stages(request) = ["new", "triage"] + (request_type.stages or ["assigned", "review"]) + ["complete"]
```

`new`/`triage` and `complete` are fixed book-ends; the middle comes from `intake_request_type.stages` (custom workstreams) or the default. Kanban columns = the default spine (New / AI Triage / Assigned / In Review / Complete); typed requests with custom stages render their own chip rail on the detail page. Every stage write appends `{stage, at}` to `stage_timestamps` (server-owned, append-only) and derives `workflow` steps in the serializer: step i `done = i < idx`, `active = i == idx and stage != "complete"`.

### 2.3 Transition table

All mutations route through ONE choke-point: `service._transition(db, *, request, to_status=None, to_stage=None, actor, actor_type="user", audit_action, before, after, ...)` which: rejects edits to `closed` (409), stamps stage_timestamps, writes `write_audit_log` + `write_timeline_event`, and leaves commit to the calling service function (one commit per API call).

| # | From → To (status / stage) | Actor | Guard (permission + checks) | Side effects |
|---|---|---|---|---|
| T1 | — → awaiting_triage / new | requester | `intake:create`; field_values validated against field defs; external_message_id dedupe (idempotent return of existing) | ref from sequence; routing rules evaluate (§3); audit `intake.created`; timeline; JobRun(`intake_triage`) enqueued via `run_ai_job.delay` |
| T2 | awaiting_triage/new → in_review / assigned (agent matched) | system (celery) | none — gated by `agent_processed_at IS NULL` (becameProcessed dedupe) | recommendation row (pending) + AIConfirmation (pending, `expires_at = submitted_at + sla_hours`) ; 2 auto-baton handoff rows (§2.4); `agent_processed_at`, `agent_outcome='matched'`; assignee mirrored if pool/rule assigned; audit `intake.recommendation.generated` (actor_user_id NULL) |
| T3 | awaiting_triage/new → (unchanged) (no agent match) | system | same dedupe | baton rows →agent→queue; `agent_outcome='no_match'`; audit `intake.agent_no_match` |
| T4 | open → approved / complete | attorney | `intake:triage`; approval-gate check (§2.5); `enforce_authority(action="intake:approve")` (§5.3); not closed | action=`approved`/`edited_approved`: recommendation → approved/edited (+`edited_at`, `override_reason`); `confirm_confirmation` on the linked AIConfirmation (same txn); `triaged_by_user_id = actor.id` (server-authoritative, ignore client), `triaged_at`, `triage_action`; audit `intake.recommendation.approved` / `.edited_approved`; SLA clock stops |
| T5 | open → rejected / triage | attorney | `intake:triage` | recommendation → rejected; `reject_confirmation`; audit `intake.recommendation.rejected`; request stays open for manual handling (rejected ∉ OPEN but stage=triage keeps it in the manual queue view) |
| T6 | open → closed / complete | attorney | `intake:triage` | recommendation stays pending (close ≠ verdict); audit `intake.recommendation.manual_close` + `intake.closed`; confirmation expires naturally |
| T7 | open → (status unchanged) / new, snoozed | attorney | `intake:triage` | `triage_action='snoozed'`; hidden from queue (`is_awaiting_triage` excludes it); audit `intake.recommendation.snoozed`; does not count as triaged in stats |
| T8 | open → in_review (reassigned) | attorney | `intake:triage`; target user in org | `assigned_to_user_id` set; handoff row (human→human, validated §2.4); audit `intake.assigned` (fires on FK transition only) + `intake.recommendation.reassigned`; recommendation stays pending |
| T9 | open → escalated | system (SLA sweep) or routing rule | none | `sla_status='overdue'`; audit `intake.sla_breached` {sla_hours, elapsed_hours} + `intake.auto_escalated`; timeline; notify (§4.4) |
| T10 | stage x → stage y (kanban drag / advance) | any holder | `intake:update`; y must be in `stages(request)`; not closed | status mapped: `new/triage→awaiting_triage`, mid-stages→`in_review`, `complete→closed`; stage_timestamps append; audit `intake.stage_advanced` |
| T11 | approved → approved (+promotion) | attorney | `intake:triage`; recommendation approved OR no recommendation exists (ungated) | sets `contract_id` or `project_id`; audit `intake.promoted` {target, id} |
| T12 | closed → anything | — | **blocked**, 409 "Request is closed — file a follow-up" | none |

Manual handoff (not a status transition): `POST .../handoff` — `intake:update`; validates state machine, writes ledger row + denormalized fields + audit `intake.handoff` (before/after holder); `to_holder='human'` mirrors `assigned_to_user_id` unless `sync_assignee=false`.

### 2.4 Holder / custody model (port of `handoff/state.ts` + `auto.ts`)

Holders: `agent | human | queue`. Rules in `service._validate_handoff`:
- `to_holder='human'` requires `to_user_id`
- first pass (`from_holder` NULL) → any holder OK
- human→human legal only if `to_user_id != from_user_id` (reassignment)
- any other same-holder pass → 422 "already held by X" (no-op ≠ handoff)
- all cross-holder passes legal

Auto-baton (in the triage job, gated on `agent_processed_at` null→set, so re-runs never double-write): exactly two rows, `actor_type='agent'`, `created_by_user_id` NULL:
1. `{from: current_holder(default queue), to: 'agent', reason: matched ? "Agent triage started" : "Router evaluated the ticket"}`
2. matched && assignee → `{from:'agent', to:'human', to_user_id: assignee, reason: "Agent draft ready — passed to the assignee for review", recommendation_id}`; matched && no assignee → `{to:'queue', reason:"Agent draft ready — queued for attorney review", recommendation_id}`; no match → `{to:'queue', reason:"No agent matched — queued for manual triage"}`

Plus one summarizing audit row `intake.handoff` metadata `{auto:"agent-pipeline", outcome, passes:2}`. Zero-length passes advance the holder without emitting an SLA leg (§4.2).

### 2.5 Approval-gate refusal (port of gate refusal)

In `record_triage_action`, before applying `approved`/`edited_approved`: if `approval_gate_user_id` set and `actor.id != approval_gate_user_id` and actor is not org admin → write audit `intake.approval_blocked` (before `{triage_action}`, after `{attempted_action, required_approver_id}`, metadata `{source:"approval-gate"}`) via `record_decision`-style isolated semantics (use `write_audit_log` then commit BEFORE raising, or reuse `app.core.authz.record_decision` isolated-session so the 403 rollback can't erase it — reuse `record_decision(user=..., action="intake:approve", outcome="denied", reason="approval_gate")` and additionally the intake audit row), then 403. Reject/reassign/snooze/close are NOT gated.

---

## 3. Routing engine

Conditions/actions are typed COLUMNS (§1.4), not JSON — faithful port. Evaluation is cumulative-ordered (later rules see earlier effects), runs inside the two write choke-points: `create_request` and `update_request` (PATCH). Both call `service._apply_routing(db, request)`.

### 3.1 Never-override-human guard

```python
if request.triaged_by_user_id or request.triage_action:
    return  # attorney decisions always win; rules never re-fire after human triage
```

### 3.2 Evaluation pseudocode

```python
def _apply_routing(db, request):
    rules = db.scalars(select(IntakeRoutingRule)
        .where(org_id == request.org_id, enabled == True)
        .order_by(IntakeRoutingRule.eval_order, IntakeRoutingRule.id)).all()
    if not rules: return
    w = working_state(request)   # type_label, priority, department, description,
                                 # sla_hours, assignee, complexity (ai_triage or "standard"), gate
    fired = []
    for r in rules:
        if not _matches(r, w): continue          # all non-null conditions AND;
                                                 # keyword = ci substring of description
        actions = []
        if r.set_priority and r.set_priority != w.priority: w.priority = r.set_priority; actions += [f"priority → {r.set_priority}"]
        if r.set_sla_hours and r.set_sla_hours != w.sla_hours: w.sla_hours = r.set_sla_hours; actions += [f"SLA → {r.set_sla_hours}h"]
        if r.set_assignee_user_id and r.set_assignee_user_id != w.assignee:
            w.assignee = r.set_assignee_user_id; actions += [f"assignee → {name}"]
        elif r.set_team_id:                       # pool only when no direct assignee on same rule
            pick = _pick_from_pool(db, r.set_team_id)          # §3.3, row-locked
            if pick and pick.user_id != w.assignee:
                w.assignee = pick.user_id
                actions += [f"pool {pick.team_name} → {pick.user_name}" + (" (overflow)" if pick.overflow else "")]
        if r.escalate_to_user_id:
            changed = False
            if not (r.set_assignee_user_id or r.set_team_id) and w.assignee != r.escalate_to_user_id:
                w.assignee = r.escalate_to_user_id; changed = True
            if not r.set_priority and w.priority != "Critical": w.priority = "Critical"; changed = True
            if changed: w.escalated = True; actions += [f"escalate → {name}"]   # idempotent: stops firing once target holds at Critical
        if r.require_approval_from_user_id and r.require_approval_from_user_id != w.gate:
            w.gate = r.require_approval_from_user_id; actions += [f"approval gate → {name}"]
        if actions: fired.append((r, actions))    # zero changes = no-op match, counter stays honest
    apply w back onto request; w.escalated → request.status = "escalated"
    prior = set((request.fired_rules or {}).get("rule_ids", []))
    request.fired_rules = {"rule_ids": [...], "fired_at": iso_now, "summaries": [...]}  # full rewrite
    for r, actions in fired:
        if r.id not in prior:                     # newly-fired only
            write_audit_log(db, action="intake.routing_rule.fired", resource_type="intake_request",
                            resource_id=request.id, actor_user_id=None, after={"rule": r.name, "actions": actions})
            r.times_fired += 1; r.last_fired_at = utcnow()
```

Deterministic conditions re-applied over post-client values = idempotent reconvergence on stale optimistic writes.

### 3.3 Pool balancer (race-safe)

```python
def _pick_from_pool(db, team_id, visited=None):
    visited = visited or set(); visited.add(team_id)
    team = _get_team(db, team_id)                # None → reason "unknown-team"
    members = db.scalars(select(IntakeTeamMember)
        .where(team_id == team.id, active == True)
        .order_by(IntakeTeamMember.id)
        .with_for_update()).all()                # LOCK: serializes assignment per team
    open_counts = one groupBy: count(intake_request) where assigned_to IN member_ids
                  AND status IN OPEN_STATUSES    # inside same txn, after lock
    eligible = [m for m in members if m.capacity <= 0 or open_counts.get(m.user_id, 0) < m.capacity]
    if eligible:
        key = ((open_count, last_assigned_at NULLS FIRST, user_id) if team.strategy == "least_loaded"
               else (last_assigned_at NULLS FIRST, user_id))       # deterministic tie-breaks
        pick = min(eligible, key=key)
        pick.last_assigned_at = utcnow()         # cursor bumped under the lock — two rules in
                                                 # one save can't stack one member
        return PoolPick(team, pick.user_id, overflow=bool(len(visited) > 1))
    if team.overflow_team_id and team.overflow_team_id not in visited:
        return _pick_from_pool(db, team.overflow_team_id, visited)   # chain; visited set breaks cycles
    return None                                  # all-at-capacity-no-overflow → stays unassigned (manual pickup)
```

`# ponytail: FOR UPDATE on member rows serializes per-team; fine at legal-team throughput — SKIP LOCKED sharding if this ever queues.` The lock is held only for the remainder of the request's single transaction (routes are one-commit).

---

## 4. SLA engine

### 4.1 Stored vs computed

**Stored** on `intake_request`: `sla_hours`, `sla_status` (coarse, sweep-written), `submitted_at`, `paused_at`, `paused_ms_total`. **Computed on read** (never stored): legs, pct, breach instant, age. No SLA-clock table, no metric-instance rows — one window per request, partitioned by custody (faithful port; upgrade path is per-metric instances if reply-time SLAs are ever asked for).

### 4.2 Legs computation — `service.build_sla_legs` (pure function, port of `legs.ts`)

```python
def build_sla_legs(request, handoffs, now_ms):
    closed_ts = ms(request.triaged_at or request.updated_at) if request.status in TERMINAL_STATUSES else None
    end = max(closed_ts or now_ms, submitted)
    sla_ms = max(sla_hours, 0) * 3_600_000
    pause = paused_ms_total + (now_ms - ms(paused_at) if paused_at and not closed_ts else 0)
    breach_ts = submitted + sla_ms + pause        # pauses SHIFT the breach instant
    passes = sorted(handoffs by created_at), atTs clamped into [submitted, end]   # clock-skew guard
    cursor = (holder="queue", user=None, label="Intake queue", start=submitted)
    legs = []
    for p in passes:
        if p.at > cursor.start: legs.append(segment(cursor → p.at))   # zero-length passes advance
        cursor = (p.to_holder, p.to_user_id if human else None, label_for(p), p.at)   # holder w/o emitting empty leg
    legs.append(segment(cursor → end))
    each leg: {holder, holder_user_id, holder_label ("AI agent"|user name|"Intake queue"),
               start_ts, end_ts (exclusive), elapsed_ms,
               pct_of_sla = round(elapsed/sla_ms*100) if sla_ms else 0,
               active = not closed and is_last,
               breached_during_leg = sla_ms > 0 and start_ts <= breach_ts < end_ts}   # expiry lands in exactly ONE leg
    return {legs, sla_ms, breach_ts, total_elapsed_ms: end - submitted,
            breached: sla_ms > 0 and end >= breach_ts, closed: bool(closed_ts), paused: bool(paused_at)}
```

Evidence-not-policy: legs carry wall-clock custody elapsed + window share; the effective clock (pause-shifted `breach_ts`) decides breach. Breach attribution = the leg holding at the shifted breach instant.

### 4.3 Pause semantics (waiting on requester)

`POST /intake/requests/{id}/pause {paused: true}` → `paused_at = utcnow()`, audit `intake.paused` (reason "waiting on requester"). `{paused: false}` → `paused_ms_total += now - paused_at`, `paused_at = NULL`, audit `intake.resumed`. Sweep skips paused requests entirely. Terminal transitions auto-resume (fold live pause into total) so math never leaks.

### 4.4 Celery-beat sweep — `check_intake_slas` (copy the `check_stage_slas` shape exactly)

`app/jobs/celery_app.py` beat entry:
```python
"check-intake-slas": {  # intake SLA at-risk / breach sweep, every 15 minutes
    "task": "app.jobs.tasks.check_intake_slas",
    "schedule": crontab(minute="*/15"),
},
```

`app/jobs/tasks.py`:
```python
@celery_app.task
def check_intake_slas() -> dict:
    """Import-safe, no-op on empty tables. State-transition triggers (sla_status changes)
    keep it naturally idempotent — no dedupe table."""
    from app.intake.models import IntakeRequest   # local imports
    db = SessionLocal()
    try:
        counters = {"scanned": 0, "at_risk": 0, "breached": 0}
        rows = open, non-terminal, non-escalated, unpaused requests
               (status IN ("awaiting_triage", "in_review"), paused_at IS NULL, sla_hours > 0)
        for r in rows:
            pct = effective_elapsed_ms(r) / sla_ms(r)     # pause-adjusted
            if pct >= 1.0 and r.sla_status != "overdue":                     # TIER 2: breach
                r.sla_status = "overdue"; r.status = "escalated"
                write_audit_log(action="intake.sla_breached", after={"sla_hours", "elapsed_hours": round(h,1)})
                write_audit_log(action="intake.auto_escalated", ...)
                write_timeline_event(event_type="intake.sla_breached", title=f"SLA breached — {r.ref}")
                notify org admins (existing notification service);  counters["breached"] += 1
            elif 0.7 <= pct < 1.0 and r.sla_status == "on_track":            # TIER 1: at-risk
                r.sla_status = "at_risk"
                write_timeline_event(...); notify assignee (owner)
                counters["at_risk"] += 1
        db.commit()
        return counters
    finally:
        db.close()
```

Idempotency: escalated/terminal excluded from scan; each tier fires only on the `sla_status` transition edge. Late-detected breaches get `breached` semantics at detection time; the legs math still reports the true `breach_ts` (documented choice: display truth = computed, event time = detection).

### 4.5 SLA-ops aggregate — `GET /intake/sla-ops` → `service.get_sla_ops_summary`

Pure read over open requests: `{generated_at, open_total, on_track, at_risk (0.7 ≤ pct < 1), overdue (pct ≥ 1), paused, avg_elapsed_pct, breaches_7d (audit rows action='intake.sla_breached' in window), by_holder: {queue, agent, human} open counts from denormalized handoff_holder, oldest_open: [{ref, pct, holder_label}] top 5}` — pct computed server-side from `submitted_at + sla_hours + pause`, same formula as the sweep.

---

## 5. AI layer

### 5.1 Classifier + agent selection = deterministic code (no Claude)

Port `classifyIntakeRegex` as `app/intake/agents.py::classify(description, department) -> dict | None` — 10 ordered regex rules, first match wins, each returning `{category, priority, sla_hours, rule_id, confidence, risk, source: "regex"}` (order: employment-sensitive > litigation > NDA > finance > IP/trademark > privacy > sanctions > regulatory > vendor-contract > vendor-DD; text ≥ 10 chars; None → fallback `{category: "General Inquiry", priority: "Medium", sla_hours: 24, source: "fallback"}`). Result stamped to `intake_request.ai_triage`. Claude is never asked to classify.

Agent registry — a code list `AGENTS` in `app/intake/agents.py`, order = routing order, first `can_handle(request)` wins:

| agent_id | can_handle (regex on category/type/desc) | adds to prompt | hard floors (code post-processing, never trust model) |
|---|---|---|---|
| `nda_agent` | nda/non-disclosure, not breach/violat | MNDA-v4.2 playbook block (2-yr term, mutual no-solicit 12mo, DE law); counterparty extraction | base confidence 0.92; action approve_and_send allowed |
| `vendor_intake_agent` | vendor dd/due diligence/onboarding | DPA v3.1 block; **sanctions regex screen runs in code first** | screen HIT → force `escalate`, conf 0.92; screen unavailable → force `flag_for_review`, conf 0.4 (never auto-clear) |
| `trademark_agent` | trademark | distinctiveness-spectrum + NICE-class memo instructions | force-append concern "formal registry search + counsel sign-off required"; approve_and_send only if conf ≥ 0.8 |
| `litigation_agent` | lawsuit/subpoena/demand letter/cease and desist… | adverse-party/deadline/tier extraction instructions | **always** force `flag_for_review`; force-append no-legal-hold + conflicts-check concerns |
| `contract_review_agent` | contract review/msa/sow/redline, no nda | embedded CONTRACT_PLAYBOOK (cap=12mo fees, mutual indemnity, DE, Net 45…), severity-tagged deviations | force-append "attorney sign-off required before execution"; approve_and_send only if conf ≥ 0.85 |
| `policy_qa_agent` | harassment/discriminat/retaliation OR KB policy match | matched KB policy snippet | sensitive branch → force `escalate`, conf 0.55, "do not auto-send" |
| `faq_agent` (last) | KB match AND not a drafting request | top-3 KB snippets (keyword retrieval §1.10) | no KB match → flag_for_review conf 0.30 |

No match → `agent_outcome='no_match'`, T3. Confidence: model returns ordinal `high|medium|low`; code maps `{high: agent_base, medium: agent_base - 0.15, low: 0.4}` — treated as ordinal signal, thresholds are code, never the model's number.

### 5.2 SkillSpecs (register in `app/ai/registry.py`, output models in `app/ai/schemas.py`, prompts in `DEFAULT_SKILL_PROMPTS` in `app/ai/prompt_versions.py`)

```python
SkillSpec(name="intake_triage", version="v1", description="Draft a specialist legal-intake response for a routed request",
    execution_mode="job", prompt_key="intake_triage", prompt_version="v1",
    input_model=None, output_model=IntakeTriageOutput,
    required_permission="intake:read", resource_type="intake_request",
    requires_citations=True, allows_mutation=False,
    feature_flag="feature.ai.intake_triage", enabled_by_default=True, max_tokens=2048)

SkillSpec(name="intake_copilot_turn", version="v1", description="One conversational intake turn: extract fields, ask follow-ups",
    execution_mode="job", prompt_key="intake_copilot", prompt_version="v1",
    output_model=IntakeCopilotTurnOutput, required_permission="intake:create",
    resource_type="intake_request", requires_citations=False, allows_mutation=False,
    feature_flag="feature.ai.intake_copilot", max_tokens=1024)
```

```python
class IntakeTriageOutput(BaseModel):
    drafted_response: str                 # 130-240w reply draft
    short_form_reply: str | None = None
    reasoning: str
    concerns: list[str] = Field(default_factory=list)
    citation_refs: list[str] = Field(default_factory=list)   # must be kb source_refs / playbook ids passed in prompt
    suggested_action: str = Field(pattern="^(approve_and_send|flag_for_review|escalate)$")
    confidence: str = Field(pattern="^(high|medium|low)$")    # ordinal, calibrated in code
    extracted: dict = Field(default_factory=dict)             # counterparty, jurisdiction, value, deadline…

class IntakeCopilotTurnOutput(BaseModel):
    message: str                                              # <70 words, shown in chat
    fields_extracted: dict = Field(default_factory=dict)      # only new/updated keys
    ready: bool = False
    ready_reason: str = ""
    topic_switch_detected: bool = False
    topic_switch_to: str | None = None
```

Prompt assembly: `SHARED_LEGAL_SYSTEM_PROMPT` (untrusted-content + citation rules) + skill prompt; agent-specific instructions/playbook/KB snippets are server-controlled and injected via `input_payload` (rendered above the "Untrusted contract text:"-style framing of the request description; PromptBuilder frames the description as untrusted). Copilot rules baked into the `intake_copilot` prompt: simple requests need only type/counterparty/urgency; sensitive topics → intake only, no drafting; topic switch → flag + ask; ≤2 follow-ups per turn.

Execution wiring: `create_request` → `app/jobs/service.py` create JobRun(`job_type="intake_triage"`) → `run_ai_job.delay` → new branch in `_run_ai_job` (tasks.py): load request → `classify` + `select_agent` (code) → if agent: `await ai_controller.run_job_skill(db, job=job, skill_name="intake_triage", input_payload={...})` → post-process in `app.intake.service.apply_triage_result(db, request_id, output, skill_run_id)`: apply floors, write recommendation (+`skill_run_id`), create confirmation, auto-baton, T2 transition, audits. If no agent or `AIUnavailable`/skill failure: degraded path — template recommendation `{confidence: 0.4, suggested_action: "flag_for_review", degraded: True, concerns: ["AI review unavailable — this is a template draft…", ...]}` or T3 no-match. Copilot: `POST /intake/copilot/turn` runs `run_structured_skill` synchronously (`commit=True`, no JobRun) — cost guard + AICallLog apply automatically.

### 5.3 Recommendation lifecycle — confirmations + authority

- **PENDING** recommendation is created with a companion `AIConfirmation` row (reuse the model + `AIConfirmationStatus`): `resource_type="intake_request"`, `resource_id=request.id`, `requested_payload={"tool_name": "intake_send_response", "arguments": {"request_id", "agent_id", "suggested_action"}}`. Created by a small `service._create_intake_confirmation` (mirrors `create_confirmation` minus the AssistantToolCall flip), then `expires_at` overridden to the SLA due instant (30-min default is wrong for a triage queue).
- **Approve seam** (`record_triage_action`, actions approved/edited_approved), in ONE transaction: (1) permission `intake:triage` (route dep), (2) approval-gate check §2.5, (3) `enforce_authority(db, user=actor, action="intake:approve", contract=<promoted contract if request.contract_id else authority-shim(value=None, contract_type=request_type.key, jurisdiction=None)>, resource_type="intake_request", resource_id=request.id)` — requires extending `authority/service.py` `ACTIONS` set with `"intake:approve"` + the schemas' `Field(pattern=...)`; progressive enforcement means the gate is dormant until an org defines its first `intake:approve` grant, (4) `confirm_confirmation(db, confirmation_id=..., user=actor)` — writes `assistant.confirmation_confirmed` audit, 409s if expired/decided, (5) recommendation status flip + stamps, (6) intake audit row, (7) commit. Reject → `reject_confirmation` + rec rejected. Manual-close/snooze/reassign leave both PENDING (close ≠ verdict).
- **Immutability**: an already-resolved recommendation is a no-op 409; new triage runs append a NEW recommendation row (newest = current), never overwrite resolved ones.

### 5.4 Confidence gate thresholds (code, in `apply_triage_result`)

```python
if suggested_action == "approve_and_send" and confidence < AGENT_APPROVE_FLOOR[agent_id]:  # nda .8, contract .85, trademark .8, faq .9
    suggested_action = "flag_for_review"
# degraded is always (0.4, flag_for_review); sanctions HIT is always (0.92, escalate); litigation always flag_for_review
```

Nothing auto-sends and nothing auto-approves at any confidence — the gate is structural (PENDING has no exit except a human `record_triage_action`). Thresholds are constants; recalibrate against the audit trail (every human reject of a high-confidence rec is a labeled correction, queryable from `intake.recommendation.rejected` rows joined to recommendations).

### 5.5 What gets audited (immutable `write_audit_log`, `resource_type="intake_request"` unless noted)

`intake.created`, `intake.updated`, `intake.stage_advanced`, `intake.assigned`, `intake.handoff`, `intake.recommendation.generated` (actor NULL), `intake.agent_no_match` (actor NULL), `intake.routing_rule.fired` (actor NULL), `intake.recommendation.approved` / `.edited_approved` / `.rejected` / `.manual_close` / `.snoozed` / `.reassigned`, `intake.approval_blocked`, `intake.closed`, `intake.paused` / `intake.resumed`, `intake.sla_breached`, `intake.auto_escalated`, `intake.promoted`, `intake.task.effort_logged`, config CRUD: `intake.request_type.created/updated/deleted`, `intake.team.created/updated/deleted`, `intake.team_member.added/updated/removed`, `intake.routing_rule.created/updated/deleted`, `intake.kb_article.created/updated/deleted` (resource_type matching each config noun). Plus automatic: `assistant.confirmation_confirmed/_rejected` (resource_type `ai_confirmation`), `access.denied` on permission/authority denials, AISkillRun + AICallLog telemetry. Human-facing feed: `write_timeline_event` on create, recommendation, triage verdicts, handoffs, breach, promotion.

---

## 6. API

`routes.py`: `router = APIRouter(prefix="/intake", tags=["intake"])`; `_MANAGE = require_permission("admin_panel:access")` module-level. Register in `app/main.py::create_app`: `from app.intake.routes import router as intake_router` + `app.include_router(intake_router, prefix=prefix)`. Routes are thin: parse → service → return; POST → 201 + `response_model`; DELETE → `Response(status_code=204)`.

**RBAC** (`app/core/rbac.py`): `INTAKE_PERMISSIONS = {"intake:read", "intake:create", "intake:update", "intake:triage"}`, OR'd into `ALL_PERMISSIONS`. `DEFAULT_ROLE_PERMISSIONS`: **member** += `intake:read, intake:create` (submitters); **legal_reviewer** += all four; **approver** += `intake:read`; **admin** inherits via `ALL_PERMISSIONS`. Optional descriptions in `roles/service.py::_PERMISSION_DESCRIPTIONS`. `bootstrap_roles` propagates automatically.

| # | Method + path | Permission | Request → Response | Service fn |
|---|---|---|---|---|
| 1 | POST `/intake/requests` | `intake:create` | `IntakeRequestCreate {type_label, description, department?, priority?, request_type_id?, field_values?, source?, conversation?, external_message_id?}` → 201 `IntakeRequestResponse` | `create_request` (routing + audit + triage job) |
| 2 | GET `/intake/requests` | `intake:read` | query `status, stage, assigned_to, priority, q, awaiting?, snoozed?, limit, offset` → `list[IntakeRequestResponse]` | `list_requests` |
| 3 | GET `/intake/requests/mine` | `intake:read` | → requester portal list (own requests, friendly status labels, newest first, cap 100) | `list_my_requests` |
| 4 | GET `/intake/requests/{id}` | `intake:read` | → `IntakeRequestDetailResponse` (request + current recommendation + handoffs + tasks + sla legs) | `get_request` |
| 5 | PATCH `/intake/requests/{id}` | `intake:update` | `IntakeRequestUpdate {description?, priority?, department?, stage?, work_status?, field_values?, assigned_to_user_id?}` → response | `update_request` (T10/T12 guards, routing re-eval, `intake.assigned` on FK transition) |
| 6 | POST `/intake/requests/{id}/triage` | `intake:triage` | `TriageActionRequest {action: Field(pattern="^(approved\|edited_approved\|rejected\|reassigned\|manual_close\|snoozed)$"), edited_draft?, reassign_to_user_id?, reason?}` → detail response | `record_triage_action` (T4–T8; gate + authority + confirmation) |
| 7 | POST `/intake/requests/bulk-triage` | `intake:triage` | `{ids: list[str], action: "approved"}` → `{results: [{id, ok, error?}]}` (per-id txn; gate refusals reported, not raised) | `bulk_triage` |
| 8 | POST `/intake/requests/{id}/handoff` | `intake:update` | `HandoffCreate {to_holder, to_user_id?, reason?, sync_assignee?=True}` → `HandoffStateResponse` | `hand_off` |
| 9 | GET `/intake/requests/{id}/sla` | `intake:read` | → `SlaLegsResponse {legs[], sla_ms, breach_ts, total_elapsed_ms, breached, closed, paused}` | `get_sla_legs` |
| 10 | POST `/intake/requests/{id}/pause` | `intake:update` | `{paused: bool}` → response | `set_paused` |
| 11 | GET `/intake/requests/{id}/timeline` | `intake:read` | → timeline events + audit rows for the request, newest first | `get_timeline` |
| 12 | POST `/intake/requests/{id}/promote` | `intake:triage` | `{target: Field(pattern="^(contract\|project)$"), target_id: str}` → response | `promote_request` (T11) |
| 13 | POST `/intake/requests/{id}/tasks` | `intake:update` | `IntakeTaskCreate {title, description?, assignee_user_id?}` → 201 `IntakeTaskResponse` | `create_task` |
| 14 | PATCH `/intake/tasks/{task_id}` | `intake:update` | `{title?, status?, assignee_user_id?, sort_order?}` → task | `update_task` |
| 15 | DELETE `/intake/tasks/{task_id}` | `intake:update` | → 204 | `delete_task` |
| 16 | POST `/intake/tasks/{task_id}/effort` | `intake:update` | `{minutes: int > 0}` → task (audits `intake.task.effort_logged` {minutes}) | `log_task_effort` |
| 17 | GET `/intake/my-work` | `intake:read` | → `{tickets[], tasks[], reviews[], counts}` — 3 self-scoped queries over OPEN_STATUSES, ranked overdue → priority → oldest (my-work port) | `get_my_work` |
| 18 | GET `/intake/assignees` | `intake:triage` | → `[{id, name, email}]` org users for the reassign picker | `list_assignees` |
| 19 | POST `/intake/copilot/turn` | `intake:create` | `{history: [{role, content}], state: dict, ticket_type?, request_id?}` → `IntakeCopilotTurnOutput` + merged state (sync skill run) | `copilot_turn` |
| 20 | GET `/intake/request-types` | `intake:read` | query `all?` (inactive included for admins) → `list[RequestTypeResponse]` (with fields) | `list_request_types` |
| 21 | POST `/intake/request-types` | `_MANAGE` | `RequestTypeCreate {key, name, workstream?, description?, stages?, sort_order?, fields?: [RequestFieldCreate]}` → 201 | `create_request_type` |
| 22 | PATCH `/intake/request-types/{id}` | `_MANAGE` | update (fields wholesale-replaced when provided) → response | `update_request_type` |
| 23 | DELETE `/intake/request-types/{id}` | `_MANAGE` | → 204 (requests keep `type_label`, FK SET NULL) | `delete_request_type` |
| 24 | GET `/intake/teams` | `intake:triage` | → `list[TeamResponse]` (members inline) | `list_teams` |
| 25 | POST `/intake/teams` | `_MANAGE` | `TeamCreate {key, name, strategy?, overflow_team_id?, …}` → 201 | `create_team` |
| 26 | PATCH `/intake/teams/{id}` | `_MANAGE` | → response | `update_team` |
| 27 | DELETE `/intake/teams/{id}` | `_MANAGE` | → 204 (rule/overflow FKs nulled) | `delete_team` |
| 28 | POST `/intake/teams/{id}/members` | `_MANAGE` | `{user_id, capacity?=0}` → 201 member | `add_team_member` |
| 29 | PATCH `/intake/team-members/{id}` | `_MANAGE` | `{capacity?, active?}` → member | `update_team_member` |
| 30 | DELETE `/intake/team-members/{id}` | `_MANAGE` | → 204 | `remove_team_member` |
| 31 | GET `/intake/routing-rules` | `_MANAGE` | → `list[RoutingRuleResponse]` (resolved names + counters) | `list_routing_rules` |
| 32 | POST `/intake/routing-rules` | `_MANAGE` | `RoutingRuleCreate` (≥1 condition + ≥1 action validated) → 201 | `create_routing_rule` |
| 33 | PATCH `/intake/routing-rules/{id}` | `_MANAGE` | null-clears / absent-preserves → response | `update_routing_rule` |
| 34 | DELETE `/intake/routing-rules/{id}` | `_MANAGE` | → 204 | `delete_routing_rule` |
| 35 | GET `/intake/kb` | `intake:read` | → `list[KbArticleResponse]` | `list_kb_articles` |
| 36 | POST `/intake/kb` | `_MANAGE` | `{source_ref, title, body, tags?}` → 201 | `create_kb_article` |
| 37 | PATCH `/intake/kb/{id}` | `_MANAGE` | → response | `update_kb_article` |
| 38 | DELETE `/intake/kb/{id}` | `_MANAGE` | → 204 | `delete_kb_article` |
| 39 | GET `/intake/pool-ops` | `intake:triage` | query `days?=30` → pool-ops summary (per-team utilization 70/100 thresholds, complexity mix, overflow-in, routed, closed 7d/30d, effort minutes, unassigned_open) — read-time attribution over current assignee + audit rows | `get_pool_ops_summary` |
| 40 | GET `/intake/sla-ops` | `intake:triage` | → SLA ops summary (§4.5) | `get_sla_ops_summary` |

Schemas: plain pydantic `<X>Create/<X>Update/<X>Response` in `schemas.py`, `Field(pattern=...)` for pseudo-enums, `Field(default_factory=list)` for lists, ISO datetimes as `str | None` in responses. Service functions: `def f(db: Session, *, actor: User, payload)`, validate → construct with `org_id=actor.org_id` → `db.add/flush` → `write_audit_log` → `db.commit` → `db.refresh` → `serialize_<x>(db, row)` dict; `_get(db, org_id, id)` 404 helper; every query filters `org_id`.

---

## 7. Seed (`app/devtools.py::seed`, local/development/test only, idempotent match-by-key/ref, runs after demo users exist)

1. **KB articles** (5): `NDA-TEMPLATE-v4.2`, `DPA-v3.1`, `POLICY-REMOTE-WORK`, `POLICY-GIFTS-ENTERTAINMENT`, `TM-CLEARANCE-PLAYBOOK` — title/body/tags for FAQ + agent citations.
2. **Request types** (3):
   - `nda` "NDA Request" — fields: `counterparty` (text, required), `nda_direction` (select mutual/one-way, required), `effective_date` (date), `notes` (textarea); default stages.
   - `contract-review` "Contract Review" — fields: `contract_value` (number), `counterparty` (text, required), `deadline` (date); stages `["review", "redline", "negotiation"]`.
   - `litigation-noncourt` "Litigation / Dispute" — fields: `adverse_party` (text, required), `response_deadline` (date, required), `served` (boolean); stages `["conflicts-check", "assessment", "response"]`.
3. **Teams** (2): `contracts-pool` (least_loaded; members legal1@ cap 5, legal2@ cap 5) and `escalations` (round_robin; members admin1@ cap 0 unbounded; overflow target of contracts-pool).
4. **Routing rules** (3, eval_order 10/20/30): (a) `match_type="NDA Request"` → `set_team_id=contracts-pool`, `set_sla_hours=24`; (b) `match_priority="Critical"` → `escalate_to_user_id=admin1@`; (c) `match_keyword="vendor"` → `require_approval_from_user_id=legal1@`, `set_sla_hours=48`.
5. **Sample requests** (6, created through `service.create_request` as user1@/user2@ so refs, routing firings, handoffs and audit rows are real): 2 NDA (one triage job pre-completed with a seeded PENDING recommendation `degraded=false` fake-draft — seed calls `apply_triage_result` with a canned `IntakeTriageOutput`, no Claude call), 1 contract review (assigned, stage review), 1 litigation (flag_for_review rec), 1 general question (no-match, queue), 1 approved+closed NDA (full lifecycle so the timeline/legs panels are populated). One request back-dated `submitted_at` −30h so the SLA panel shows an overdue leg.
6. Seed is skipped entirely outside local/development/test (same guard as demo users). No cleanup needed — seeded data is the demo dataset by design; audit rows it writes are intentional demo history. Ad-hoc TESTS (not seed) must create throwaway requests and delete them (cascade removes children; orphaned audit rows are invisible) and must never triage seeded/shared requests.

---

## Integration checklist (every file touched outside `app/intake/`)

| File | Change |
|---|---|
| `backend/alembic/versions/0022_legal_intake.py` | new migration (§1.11) |
| `app/models.py` | import 8 intake models + add to `__all__` |
| `app/main.py` | import + `include_router(intake_router, prefix=prefix)` |
| `app/core/rbac.py` | `INTAKE_PERMISSIONS` + union + role defaults (§6) |
| `app/roles/service.py` | optional `_PERMISSION_DESCRIPTIONS` entries |
| `app/authority/service.py` + `app/authority/schemas.py` | add `"intake:approve"` to `ACTIONS` + schema pattern |
| `app/ai/schemas.py` | `IntakeTriageOutput`, `IntakeCopilotTurnOutput` |
| `app/ai/registry.py` | 2 SkillSpecs (§5.2) |
| `app/ai/prompt_versions.py` | `DEFAULT_SKILL_PROMPTS["intake_triage"]`, `["intake_copilot"]` (+ repo-doc `intake_triage.v1.md`) |
| `app/jobs/tasks.py` | `job_type == "intake_triage"` branch in `_run_ai_job`; `check_intake_slas` task |
| `app/jobs/celery_app.py` | `check-intake-slas` beat entry (`crontab(minute="*/15")`) |
| `app/devtools.py` | intake seed block (§7) |
| `app/intake/agents.py` | classifier rules + `AGENTS` registry + floors (new file, code-config) |

Deliberate cuts (add when a real need appears): no email/Teams mailbox polling (channel = `source` field + `external_message_id` dedupe, the API endpoint IS the email-ingest seam); no multi-role assignment/party tables; no per-metric SLA instances or business-hours calendars (single pause-aware window); no stored workflow JSON (derived); no separate AgentDecision table (AIConfirmation + AISkillRun cover governance); no conversation child table (JSON column).


<!-- ================================================== -->
# PART 2 — Frontend spec
> Amended by Part 0 — where they conflict, Part 0 wins.

# Frontend Engineering Spec — Legal Intake surfaces (Clean-Room CLM)

Target: `frontend/src` (Next.js 15 App Router). All pages `"use client"`, slate-* tokens only (never `bg-white`), adaptive empty states everywhere, dark mode verified via ThemeToggle. All new API paths assume backend module `app/intake/` mounted at `/api/v1/intake` (+ config routes under the same prefix, admin-gated server-side).

---

## 1. Route + navigation map

### Routes
| Route | File | Purpose |
|---|---|---|
| `/intake` | `src/app/(app)/intake/page.tsx` | Tabbed shell (all 12 surfaces) |
| `/intake/[id]` | `src/app/(app)/intake/[id]/page.tsx` | Ticket Detail (deep-linkable; Breadcrumbs back to `/intake?tab=inbox`) |

No other subroutes. Tab state: `const [tab, setTab] = useState(initialTab)` where `initialTab = useSearchParams().get("tab") ?? roleDefault`; on change, `router.replace(`/intake?tab=${id}`, {scroll:false})` so tabs are linkable (Cockpit toasts, My Work rows, and matter-spawn toasts deep-link).

### Nav entry
`src/components/app-shell.tsx` — first item of the **Lifecycle** section (intake is the front door before approvals):
```ts
{ href: "/intake", label: "Intake", icon: Inbox }   // lucide-react Inbox
```
`isActive` already handles it via `pathname.startsWith(href)`.

### Role-shaped tab model
Permission helper (local to page.tsx):
```ts
const can = (p: string) => !!user?.permissions?.includes(p);
const isStaff = can("intake:triage") || can("intake:update");
const isAdmin = can("admin_panel:access");
```
(`user.permissions: string[]` from `useAuth()` / `UserResponse`, types.ts:35. Backend must add `INTAKE_PERMISSIONS = {intake:read, intake:create, intake:update, intake:triage}` per the rbac conventions — member: read+create, legal_reviewer: read/update/triage, approver: read; admin inherits all.)

Tab list construction (order matters):
- **Everyone** (`intake:create`): `new-request`, `my-requests`, `self-service`
- **+ Staff** (`isStaff`), prepended: `my-work`, `inbox`, `cockpit`, `kanban`, and appended: `sla`, `pool-ops`, `routing` (routing visible read-only to staff; mutations gated by `isAdmin`)
- **+ Admin** (`isAdmin`), appended: `teams`, `request-types`

Default tab: `isStaff ? "my-work" : "my-requests"`.

Header (above Tabs): `PageHeader` with title "Legal Intake", description "Mission control for every legal request", `actions` = live `Badge tone="amber"` "N awaiting triage" (from `["intake-tickets"]` count of `isAwaitingTriage`, staff only) + `Button variant="outline" size="sm"` "Agent settings" opening the AgentSettings `Modal` (staff only).

Uses the standard single-file Tabs pattern: `<Tabs tabs={visibleTabs} active={tab} onChange={setTab}/>` + `{tab === "x" && <XTab/>}`. Tab components are **top-level exports in sibling files** (12 tabs is too large for one file; the never-inside-a-component-body rule still applies).

---

## 2. Component tree (file-by-file, with ui.tsx atoms)

Non-route files co-located under the route dir are not routes in App Router; shared panels go in `src/components/intake/` because `[id]/page.tsx` also uses them.

```
src/app/(app)/intake/
  page.tsx                     IntakePage — PageHeader, Tabs, Badge, tab switch, AgentSettings Modal
  [id]/page.tsx                TicketDetailPage — Breadcrumbs, Card*, Badge, Button, CenterSpinner, ErrorState
  tabs/my-work.tsx             MyWorkTab — Card, CardHeader/Title/Body, Badge, EmptyState, SkeletonRows, ErrorState
  tabs/inbox.tsx               InboxTab — StatCard×5, Table/THead/TH/TR/TD, Badge, Input (search), Button ("Show more"), SkeletonRows, EmptyState, ErrorState
  tabs/cockpit.tsx             CockpitTab + BulkConfirmCard + ReassignPicker + ShortcutCheatsheet (all top-level in this file) — Card*, Badge, Button, Textarea, Modal, MessageBar, EmptyState, CenterSpinner
  tabs/new-request.tsx         NewRequestTab + TypePickerGate + StructuredForm + DynamicFields + ConfirmationCard — Card*, Field, Input, Textarea, Select, Button (loading), Badge, MessageBar
  tabs/copilot.tsx             CopilotChat + ConversationStateCard + TopicSwitchBanner — Card*, Textarea, Button, Badge, MessageBar
  tabs/my-requests.tsx         MyRequestsTab — Card, Badge, Button (toggle closed / + New request), EmptyState (teaching, action→new-request), SkeletonRows, ErrorState
  tabs/self-service.tsx        SelfServiceTab + ArticleDetail — Input (search), Badge (category chips), Card*, StatCard, EmptyState
  tabs/kanban.tsx              KanbanTab + KanbanColumn + KanbanCard — Card, Badge, EmptyState, SkeletonRows
  tabs/sla-dashboard.tsx       SlaTab + SlaOpsPanel — StatCard×4+, Table*, Badge, Button ("Run breach scan"), MessageBar, SkeletonRows, ErrorState
  tabs/pool-ops.tsx            PoolOpsTab — StatCard×6, Card*, Badge, Button (Refresh), EmptyState (teaching→teams), SkeletonRows
  tabs/routing.tsx             RoutingTab + RuleEditor + RuleDetail — StatCard×4, Table*, Badge, Modal, Field/Input/Select, Button, Modal (type-name-to-delete confirm), EmptyState
  tabs/teams.tsx               TeamsTab + TeamEditor + MemberRow — Card*, Table*, Field/Input/Select, Button, Badge, Modal, EmptyState
  tabs/request-types.tsx       RequestTypesTab + TypeEditor + FieldEditor — Card*, Table*, Field/Input/Select, Button, Badge, Modal, EmptyState

src/components/intake/
  shared.ts                    slaTone(), statusTone(), priorityTone(), humanizeEvent(), STAGE_COLUMNS, fmtAge() — pure helpers, no components
  timeline-panel.tsx           TimelinePanel — Card*, Badge ("CHAIN-SEALED · TAMPER-EVIDENT" banner = MessageBar intent="info"), EmptyState collapse
  sla-legs-bar.tsx             SlaLegsBar — NEW PRIMITIVE (segmented div bar, see §4.15) + legend list; Badge for "HOLDING NOW"/"BREACH HAPPENED HERE"
  handoff-dialog.tsx           HandoffDialog — Modal, Field, Select (holder + assignee), Textarea (reason), Button, recent-handoffs list
  work-panel.tsx               WorkPanel — Card*, Select (work status), Badge (task chips, click to cycle), Button size="sm" (+15m/+30m/+1h), Field
  recommendation-card.tsx      RecommendationCard — Card*, Badge (confidence/agent/degraded), MessageBar intent="warning" (concerns), Textarea (edit mode), Button
  workflow-strip.tsx           WorkflowStrip — pure presentational stepper over ticket.workflow (see §4.14)
  use-cockpit-shortcuts.ts     useCockpitShortcuts hook (§5)
```

All panels obey the dark-mode rule (surfaces `bg-slate-100`, recessed `bg-slate-50`, raised `bg-slate-200`, borders `border-slate-200`, muted `text-slate-500`, zeroed KPIs `text-slate-300`) and the adaptive-empty-state rule (empty section → one-line all-clear strip; adjacent empties merge).

---

## 3. Data layer

### 3.1 `lib/types.ts` additions
```ts
// ---- Intake ----
export interface WorkflowStep { label: string; done?: boolean; active?: boolean }
export interface AiTriage {
  category: string; risk_flag: string; suggested_assignee: string | null;
  estimated_hours: number | null; similar_matters: number | null;
  confidence: number; routing_rule: string | null;
  source: "regex" | "claude" | "copilot" | "fallback"; complexity: "simple" | "standard" | "complex" | null;
}
export interface PrecedentLink { id: string; title: string }
export interface AgentRecommendation {
  agent_id: string; confidence: number; suggested_action: string;
  drafted_response: string; reasoning: string; concerns: string[];
  precedent_links: PrecedentLink[]; alternative_tone: string | null;
  generated_at: string | null; mock: boolean;
  edited?: boolean; edited_at?: string | null; edited_by?: string | null;
  status: "PENDING" | "APPROVED" | "EDITED" | "REJECTED";
}
export interface ConversationMessage {
  role: "user" | "assistant" | "system"; content: string; ts: number;
  fields_extracted?: Record<string, unknown> | null;
}
export interface IntakeTicketResponse {
  id: ID; ref: string;                       // ref = human "REQ-NNNN"
  source: "form" | "copilot" | "seed" | "email" | "slack" | "teams" | "api";
  requester_name: string; department: string | null; type: string;
  priority: "Critical" | "High" | "Medium" | "Low";
  status: string; stage: "new" | "triage" | "assigned" | "review" | "complete";
  description: string;
  assigned_label: string | null; assigned_to_user_id: ID | null;
  sla_hours: number; sla_status: "On Track" | "At Risk" | "Overdue";
  submitted_at: string;                      // ISO; client derives pct/age
  workflow: WorkflowStep[];
  ai_triage: AiTriage | null;
  agent_recommendation: AgentRecommendation | null;
  conversation: ConversationMessage[] | null;
  triaged_by: string | null; triaged_at: string | null;
  triaged_action: "approved" | "rejected" | "reassigned" | "manual-close" | "snoozed" | "edited-approved" | null;
  agent_processed_at: string | null; agent_outcome: "matched" | "no-match" | null;
  fired_rules: { rule_ids: ID[]; fired_at: string; summaries: { id: ID; name: string; actions: string[] }[] } | null;
  matter_id: ID | null;                      // read-only, server-written
  request_type_id: ID | null; request_field_values: Record<string, unknown> | null;
  handoff_holder: "agent" | "human" | "queue" | null; handoff_user_id: ID | null;
  approval_gate_user_id: ID | null; approval_gate_user_name: string | null;
  work_status: string | null;
  created_at: string | null; updated_at: string | null;
}
export interface TriageActionResult { ticket: IntakeTicketResponse; spawned_matter_id: ID | null }
export interface IntakeTimelineEvent {
  id: ID; action: string; actor_name: string | null; actor_kind: "user" | "agent" | "system";
  chain_position: number; hash_prefix: string; created_at: string;
  details: Record<string, unknown> | null;
}
export interface SlaLeg {
  holder: "queue" | "agent" | "human"; holder_user_id: ID | null; holder_label: string;
  start_ts: number; end_ts: number; elapsed_ms: number; pct_of_sla: number;
  active: boolean; breached_during_leg: boolean;
}
export interface SlaLegsResponse { legs: SlaLeg[]; sla_ms: number; breach_ts: number; total_elapsed_ms: number; breached: boolean; closed: boolean }
export interface HandoffEvent {
  id: ID; from_holder: string | null; to_holder: "agent" | "human" | "queue";
  to_user_id: ID | null; to_user_name: string | null; reason: string | null;
  actor_type: "USER" | "AGENT"; created_at: string;
}
export interface RoutingRuleResponse {
  id: ID; name: string; description: string | null; enabled: boolean; eval_order: number;
  match_type: string | null; match_priority: string | null; match_department: string | null;
  match_keyword: string | null; match_complexity: string | null;
  set_assignee_user_id: ID | null; assignee_name: string | null;
  set_priority: string | null; set_sla_hours: number | null;
  set_team_id: ID | null; team_name: string | null;
  escalate_to_user_id: ID | null; escalate_to_name: string | null;
  require_approval_from_user_id: ID | null; approver_name: string | null;
  times_fired: number; last_fired_at: string | null;
}
export interface TeamMemberResponse { id: ID; user_id: ID; user_name: string; user_email: string; capacity: number; active: boolean; last_assigned_at: string | null }
export interface TeamResponse {
  id: ID; key: string; name: string; description: string | null; active: boolean;
  strategy: "least_loaded" | "round_robin"; overflow_team_id: ID | null; overflow_team_name: string | null;
  sort_order: number; members: TeamMemberResponse[];
}
export interface RequestFieldDef { id?: ID; key: string; label: string; kind: "text" | "textarea" | "select" | "date" | "number" | "boolean"; required: boolean; sort_order: number; options: { value: string; label: string }[] }
export interface RequestTypeResponse { id: ID; key: string; name: string; workstream: string | null; description: string | null; active: boolean; stages: string[]; sort_order: number; fields: RequestFieldDef[] }
export interface MyWorkTicket { id: ID; ref: string; type: string; priority: string; status: string; stage: string; sla_hours: number; sla_status: string; work_status: string | null; submitted_at: string; desc_snippet: string; assigned: boolean; holding: boolean }
export interface MyWorkResponse {
  tickets: MyWorkTicket[];
  tasks: { id: ID; ticket_id: ID; ticket_ref: string; title: string; status: string; ticket_type: string; ticket_priority: string }[];
  reviews: { ticket_id: ID; ticket_ref: string; agent_id: string; created_at: string; ticket_type: string; priority: string; sla_status: string }[];
  counts: { tickets: number; tasks: number; reviews: number; total: number };
}
export interface MyRequestRow { id: ID; ref: string; type: string; status_label: string; sla_status: string; submitted_at: string; closed: boolean; latest_event: string | null }
export interface PoolOpsMember { user_id: ID; user_name: string; capacity: number; active: boolean; open_count: number; utilization_pct: number | null }
export interface PoolOpsTeam {
  id: ID; key: string; name: string; active: boolean; strategy: string; overflow_team_name: string | null;
  members: PoolOpsMember[]; open_total: number; capacity_total: number; utilization_pct: number | null;
  complexity_mix: { simple: number; standard: number; complex: number };
  overdue_count: number; at_risk_count: number; routed_count: number; overflow_in_count: number;
  closed_7d: number; closed_30d: number; effort_minutes: number;
}
export interface PoolOpsSummary { generated_at: string; window_days: number; teams: PoolOpsTeam[]; unassigned_open: number }
export interface SlaOpsSummary {
  open: number; awaiting_triage: number; escalated: number; overdue: number; at_risk: number;
  workload: { user_id: ID; user_name: string; open_count: number }[];
  rule_effectiveness: { rule_id: ID; name: string; times_fired: number; last_fired_at: string | null }[];
}
export interface WorkTask { id: ID; title: string; status: "open" | "in_progress" | "blocked" | "done"; effort_minutes: number }
export interface CopilotTurnResult { message: string; fields_extracted: Record<string, unknown>; ready: boolean; ready_reason: string | null; topic_switch_detected: boolean; topic_switch_to: string | null }
export interface AssigneeOption { id: ID; name: string; email: string }
```

### 3.2 `lib/endpoints.ts` — `intakeApi`
```ts
export const intakeApi = {
  // tickets
  listTickets: (params?: { filter?: string }) => apiFetch<IntakeTicketResponse[]>(`/intake/tickets${qs(params)}`),
  getTicket: (id: ID) => apiFetch<IntakeTicketResponse>(`/intake/tickets/${id}`),
  createTicket: (body: IntakeTicketCreate) => apiFetch<TriageActionResult>(`/intake/tickets`, { method: "POST", body }),   // runs classify+agent+rules server-side, returns canonical ticket
  updateTicket: (id: ID, body: Partial<IntakeTicketUpdate>) => apiFetch<IntakeTicketResponse>(`/intake/tickets/${id}`, { method: "PATCH", body }),
  moveStage: (id: ID, stage: string) => apiFetch<IntakeTicketResponse>(`/intake/tickets/${id}/stage`, { method: "POST", body: { stage } }),      // server derives status + workflow (kanban_map)
  advanceStage: (id: ID) => apiFetch<IntakeTicketResponse>(`/intake/tickets/${id}/advance-stage`, { method: "POST" }),      // 409 at final stage
  triage: (id: ID, body: { action: TriagedAction; edited_response?: string; reason?: string; assignee_user_id?: ID }) =>
    apiFetch<TriageActionResult>(`/intake/tickets/${id}/triage`, { method: "POST", body }),                                 // server authoritative on triaged_by; 403 w/ approval-gate message
  bulkApprove: (ids: ID[]) => apiFetch<{ approved: number; spawned_matter_ids: ID[] }>(`/intake/tickets/bulk-approve`, { method: "POST", body: { ids } }),
  // panels
  timeline: (id: ID) => apiFetch<IntakeTimelineEvent[]>(`/intake/tickets/${id}/timeline`),
  slaLegs: (id: ID) => apiFetch<SlaLegsResponse>(`/intake/tickets/${id}/sla-legs`),
  handoffs: (id: ID) => apiFetch<HandoffEvent[]>(`/intake/tickets/${id}/handoff`),
  handOff: (id: ID, body: { to_holder: string; to_user_id?: ID; reason?: string }) => apiFetch<HandoffEvent>(`/intake/tickets/${id}/handoff`, { method: "POST", body }),
  tasks: (id: ID) => apiFetch<WorkTask[]>(`/intake/tickets/${id}/tasks`),
  addTask: (id: ID, title: string) => apiFetch<WorkTask>(`/intake/tickets/${id}/tasks`, { method: "POST", body: { title } }),
  cycleTask: (id: ID, taskId: ID, status: string) => apiFetch<WorkTask>(`/intake/tickets/${id}/tasks/${taskId}`, { method: "PATCH", body: { status } }),
  logEffort: (id: ID, taskId: ID, minutes: number) => apiFetch<WorkTask>(`/intake/tickets/${id}/tasks/${taskId}/effort`, { method: "POST", body: { minutes } }),
  setWorkStatus: (id: ID, work_status: string) => apiFetch<IntakeTicketResponse>(`/intake/tickets/${id}/work-status`, { method: "POST", body: { work_status } }),
  // self-scoped
  myWork: () => apiFetch<MyWorkResponse>(`/intake/my-work`),
  myRequests: () => apiFetch<MyRequestRow[]>(`/intake/my-requests`),
  assignees: () => apiFetch<AssigneeOption[]>(`/intake/assignees`),
  // dashboards
  slaOps: () => apiFetch<SlaOpsSummary>(`/intake/sla-ops`),
  runBreachScan: () => apiFetch<{ escalated: number }>(`/intake/sla-scan`, { method: "POST" }),                              // admin_panel:access server-side; 403 surfaced
  poolOps: (days = 30) => apiFetch<PoolOpsSummary>(`/intake/pool-ops${qs({ days })}`),
  // config (admin)
  listRules: () => apiFetch<RoutingRuleResponse[]>(`/intake/routing-rules`),
  createRule: (b: RoutingRuleCreate) => apiFetch<RoutingRuleResponse>(`/intake/routing-rules`, { method: "POST", body: b }),
  updateRule: (id: ID, b: RoutingRuleUpdate) => apiFetch<RoutingRuleResponse>(`/intake/routing-rules/${id}`, { method: "PATCH", body: b }),
  deleteRule: (id: ID) => apiFetch<void>(`/intake/routing-rules/${id}`, { method: "DELETE" }),
  listTeams: () => apiFetch<TeamResponse[]>(`/intake/teams`),
  createTeam / updateTeam / deleteTeam / addMember / updateMember / removeMember,                                             // same CRUD shape as rules
  listRequestTypes: (all = false) => apiFetch<RequestTypeResponse[]>(`/intake/request-types${qs(all ? { all: 1 } : undefined)}`),
  createRequestType / updateRequestType / deleteRequestType,
  // copilot
  copilotTurn: (body: { history: ConversationMessage[]; state: Record<string, unknown>; ticket_type: string }) =>
    apiFetch<CopilotTurnResult>(`/intake/copilot/turn`, { method: "POST", body }),
};
```
(Create/Update request interfaces mirror the Response minus server-owned fields — `ref`, `triaged_by`, `fired_rules`, `matter_id`, `handoff_*`, `approval_gate_*` are never client-writable.)

### 3.3 React Query keys + invalidation map
Keys: `["intake-tickets"]`, `["intake-ticket", id]`, `["intake-my-work"]`, `["intake-my-requests"]`, `["intake-timeline", id]`, `["intake-sla-legs", id]`, `["intake-handoffs", id]`, `["intake-tasks", id]`, `["intake-routing-rules"]`, `["intake-teams"]`, `["intake-request-types"]`, `["intake-pool-ops"]`, `["intake-sla-ops"]`, `["intake-assignees"]`.

`["intake-tickets"]` uses `refetchInterval: 30_000` (replaces the original's 30s client recompute — SLA pct/age are derived at render from `submitted_at`/`sla_hours`, never stored).

| Mutation | Invalidates |
|---|---|
| createTicket | intake-tickets, intake-my-requests, intake-my-work |
| triage (any verdict) | intake-tickets, intake-ticket:id, intake-my-work, intake-timeline:id, intake-handoffs:id, intake-sla-legs:id |
| bulkApprove | intake-tickets, intake-my-work |
| moveStage / advanceStage | intake-tickets, intake-ticket:id, intake-timeline:id |
| handOff | intake-handoffs:id, intake-sla-legs:id, intake-ticket:id, intake-tickets, intake-timeline:id |
| task add/cycle/effort, setWorkStatus | intake-tasks:id, intake-ticket:id, intake-pool-ops |
| rule CRUD | intake-routing-rules, intake-sla-ops |
| team CRUD | intake-teams, intake-pool-ops, intake-routing-rules |
| request-type CRUD | intake-request-types |
| runBreachScan | intake-sla-ops, intake-tickets, intake-timeline:* (broad: `qc.invalidateQueries({queryKey:["intake-timeline"]})`) |

Mutations follow house style: plain async + local `busy` state + `notify(...)` + invalidate — **except** the two optimistic paths below, which use `useMutation`.

### 3.4 Optimistic updates

**Kanban drag** (`tabs/kanban.tsx`) — TanStack cache-based pattern, cache is the single source the board renders from:
```ts
const move = useMutation({
  mutationFn: ({ id, stage }) => intakeApi.moveStage(id, stage),
  onMutate: async ({ id, stage }) => {
    await qc.cancelQueries({ queryKey: ["intake-tickets"] });          // mandatory — prevents snap-back
    const prev = qc.getQueryData<IntakeTicketResponse[]>(["intake-tickets"]);
    qc.setQueryData(["intake-tickets"], (old) => old?.map(t =>
      t.id === id ? { ...t, stage, status: STAGE_STATUS[stage], workflow: rebuildWorkflow(t.workflow, stage) } : t));
    return { prev };
  },
  onError: (e, _v, ctx) => { qc.setQueryData(["intake-tickets"], ctx?.prev); notify(msg(e), "error"); },
  onSettled: () => { if (qc.isMutating({ mutationKey: ["intake-move"] }) <= 1) qc.invalidateQueries({ queryKey: ["intake-tickets"] }); },
  mutationKey: ["intake-move"],
});
```
`STAGE_STATUS = { new: "Triage", triage: "Triage", assigned: "Assigned", review: "In Review", complete: "Completed" }` (kanban_map, mirrored client-side purely for the optimistic frame; server response is truth). `rebuildWorkflow`: step i `done = i < stageIdx`, `active = i === stageIdx && stage !== "complete"`; drop on complete → all done. Apply `setQueryData` synchronously in `onDragEnd` (before `mutate`) to avoid the dnd 1-frame flicker. Native HTML5 drag (`draggable` + onDragStart/onDrop) — no dnd lib.

**Cockpit verdicts** — commit immediately, advance immediately, no undo queueing:
1. On keypress: optimistically patch `["intake-tickets"]` (same setQueryData shape: stage/status/workflow/triaged_action per the verdict table in §5), advance selection after 200 ms.
2. Fire `intakeApi.triage(...)` at once. On success: `notify` (with "Matter N created" action-toast deep-linking `/contracts/{spawned_matter_id}` when returned, 6 s), invalidate per the map. On error (incl. 403 approval-gate refusal): rollback that ticket from the pre-mutation snapshot, `notify(e.message, "error")`, move selection back.
3. No client-side undo key — reject/reassign already provide the inverse paths; server is authoritative on `triaged_by`.

---

## 4. Per-surface specs (12 tabs + detail + shared panels)

Common states: loading → `<CenterSpinner label="Loading …"/>` (full-tab) or `<SkeletonRows/>` (list region); error → `<ErrorState error={error}/>`; empty → `<EmptyState icon title description action?/>` or one-line all-clear strip per the adaptive rule.

**4.1 My Work** (staff default). Header line: "N items waiting" / "All clear". Three sections as `Card`s: **Awaiting my review** (amber left border `border-l-2 border-amber-500`, first), **My tickets** (left border = SLA tone; subline: stage · work_status · "baton passed to you" `Badge tone="violet"` when `holding && !assigned`), **My tasks** (blocked rows red accent). Rows are buttons → `router.push(/intake/${id})`. Empty section → one-line strip "Nothing awaiting your review ✓" (`text-slate-500`, merge adjacent); `counts.total === 0` → single `EmptyState icon={Inbox} title="Inbox zero"`. Data: `["intake-my-work"]`.

**4.2 Inbox**. Top: 5 `StatCard`s (Today's requests, Auto-resolved %, In flight, SLA breached [tone red if >0 else value `text-slate-300`], Avg response). Filter chips row (Button variant ghost/secondary toggling): All / My queue (`assigned_to_user_id === user.id`) / SLA breached / At risk / In review / Auto-completed / New; plus `Input` search (id/requester/desc/type/category). `Table` columns: Ref, Requester, Type, Priority (`Badge` priorityTone), Status (`Badge` statusTone), SLA (inline 64px progress bar div, fill = slaTone bg + pct label), Assigned, Age. Overdue rows `bg-red-500/5`, at-risk `bg-amber-500/5`. **Windowing**: render `visible = 60` rows, "Show more" button adds 60 (`useState`) — no virtualization lib (filtered client-side from `["intake-tickets"]`; ponytail: paging button, virtualize only if >2k rows becomes real). Row click → `/intake/{id}`.

**4.3 Triage Cockpit**. Two-column grid (`lg:grid-cols-[1fr_420px]`, stacks on mobile). Queue order: awaiting-triage newest-first, then triaged (`triaged_by && stage !== "complete" && status !== "Snoozed"`) by `triaged_at` desc; snoozed hidden behind header chip `Badge` "⏲ N snoozed" (toggle). Header: position "3 / 17", "Triaged today: N" (localStorage `intake-cockpit-state` `{lastPos, triagedToday, triagedDate}`), search `Input` (shown by `/`), bulk-mode `Badge tone="violet"` when active. Left `Card`: ticket detail — ref/priority/status badges, requester+dept, description (scrollable `max-h-64 overflow-y-auto`), AI Triage block (category, risk, confidence, source badge, fired-rules chips), Copilot transcript (collapsible, `conversation` present), `WorkflowStrip`, `SlaLegsBar` (compact). Right rail: `RecommendationCard` (below), `WorkPanel`, hand-off `Button variant="outline"` → `HandoffDialog`. Approval gate: `MessageBar intent="warning"` "🔒 Approval locked to {approval_gate_user_name}" when set. Footer: visible verdict buttons mirroring every shortcut (a11y requirement) + `?` cheatsheet button. Empty queue → `EmptyState icon={CheckCircle2} title="Queue clear"`. Bulk confirm = `Modal` with count, per-agent breakdown (group `selected` by `agent_recommendation?.agent_id ?? "no-agent"`), expandable ticket list, confirm `Button loading`.

*RecommendationCard*: agent id `Badge tone="cyan"`, confidence `Badge` (≥0.8 green / ≥0.6 blue / ≥0.4 amber / else red; `mock` → amber "DEGRADED"), suggested action, drafted response (mono-ish `bg-slate-50` block; swaps to `Textarea` in edit mode with Save+Approve / Cancel), reasoning, concerns as `MessageBar intent="warning"` list, precedent links, alternative tone (collapsible). No recommendation → one-line strip "No agent draft — manual triage" + Manual close / Reassign buttons.

**4.4 New Request**. Step 1 `TypePickerGate`: grid of type buttons (Card-styled), SIMPLE types → form, COMPLEX + "I'm not sure" → mounts `CopilotChat` (tabs/copilot.tsx) in place; "Switch to form" / "Switch to Copilot" toggle preserved. Step 2 form (`Card`, 2-col `Field` grid): Name (`Input`, prefilled `user.name`), Department (`Select`, 10-dept picklist), Urgency (`Select`: Emergency—deal blocker / Urgent—deadline this week / Standard / Low), Request type (`Select`: built-ins + configured types marked "▣", dedup by name; selecting a configured type shows its stage chips "Workflow: 1. Intake 2. …" and mounts `DynamicFields`), Description (`Textarea` full-width, live classify preview line under it — client regex, cheap), optional file input (native `<input type="file" accept=".docx,.txt,.pdf">` via `apiFetch` `form:` upload to `/intake/documents`). `DynamicFields`: sorted by sort_order, 2-col, textarea spans both, select/date/number/boolean(two-button Yes/No) per kind; `missingRequiredFields` computed → disables submit + lists labels in `MessageBar intent="info"`. Submit `Button loading` label "Triaging + routing to agent…" → `createTicket` → **ConfirmationCard** (§6.4). Reset field values on type change.

**4.5 My Requests** (requester portal). List of read-only status `Card` rows: ref, type, friendly `status_label` `Badge`, SLA posture dot, submitted date, latest humanized event (`text-slate-500`). Header: "Show closed" toggle (`Button variant="ghost" size="sm"`), "+ New request" → `setTab("new-request")`. Empty → teaching `EmptyState icon={Send} title="Nothing filed yet" action={<Button>File your first request</Button>}`. Data: `["intake-my-requests"]`.

**4.6 Self-Service**. `Input` search + category chip row (`Badge` buttons) over a static article list module (`src/components/intake/kb.ts`, ported articles/categories). Article grid of `Card`s → detail view (back button) with "File a ticket" `Button` that switches to new-request with description pre-filled (lift a `prefill` state up to IntakePage). Stats strip: 2 real `StatCard`s (articles, categories); FAQ-agent 7d metrics only if endpoint exists — otherwise omit (no fabricated numbers). Ask-AI chat: **skipped for v1** — link to the existing Ask Aegis page instead; add when an intake FAQ skill lands.

**4.7 Kanban**. 5 columns (STAGE_COLUMNS: New / AI Triage / Assigned / In Review / Complete) as `grid-cols-5` (min-width container, horizontal scroll on small). Column header: label, count, critical tally (`Badge tone="red"` if >0). Cards: ref, type, requester, priority badge, SLA dot (slaTone). Native drag → §3.4 mutation. Column empty → dashed `border-slate-200` drop zone with muted "—". Loading → `SkeletonRows` per column ×2.

**4.8 SLA Dashboard**. Top `SlaOpsPanel` `Card`: 5 inline stats (open / awaiting triage / escalated / past-SLA red / at-risk amber, zeros muted), attorney workload mini-table, rule-effectiveness list (top 5 by times_fired), and — `isAdmin` only — "Run breach scan" `Button variant="danger" size="sm"` with `busy`; success → `notify("Escalated N tickets", "success")` + invalidations; 403 → error toast. Below: 4 `StatCard`s (SLA met %, Active, At risk, Breached), SLA-by-team `Table` (client rollup from `["intake-tickets"]` by team of assignee), active breaches/at-risk list (rows → detail). 24h burndown: **skip v1** (needs history endpoint; add when sla_ops returns a series).

**4.9 Pool Ops**. Header 6 `StatBlock`s as `StatCard`s: Open in pools, Awaiting pickup (amber >0), Routed by rules, Overflow events (amber >0, hint "capacity pressure"), Closed 7d, Overdue (red >0). Per-team `Card`: name + strategy pill `Badge tone="blue"` + "N VIA OVERFLOW" `Badge tone="amber"`, utilization bar (utilColor: null→`bg-slate-300` "∞", ≥100 red, ≥70 amber, else green), complexity-mix chips (Simple/Standard/Complex counts), stat line (Routed / Closed 7d/30d / Effort h if ≥60m / overdue / at-risk), member rows "open/capacity · pct%" or "∞ cap". Refresh `Button variant="ghost" size="sm"` → invalidate `["intake-pool-ops"]`. No teams → teaching `EmptyState` "No pools configured" action → teams tab (admin) or plain text (staff).

**4.10 Smart Routing**. 4 `StatCard`s: Active rules, Total firings, Last fired, Engine `Badge tone="green"` "LIVE". Rule `Table`: eval order, name, enabled toggle (`Button size="sm"` Enable/Disable, admin), conditions summary, actions summary, times fired, last fired; row click → detail pane `Card` (conditions/actions verbatim, description). Admin: "+ New rule" → `RuleEditor` `Modal size="lg"` — `Field`s for name/description/eval order/enabled; Conditions section (type `Select`, priority `Select`, department `Input`, keyword `Input`, complexity `Select`); Actions section (assignee `Select` from `["intake-assignees"]`, team `Select` from `["intake-teams"]`, priority, SLA hours `Input type=number`, escalate-to `Select`, require-approval-from `Select`). Client mirrors server validation: ≥1 condition AND ≥1 action else disable save with hint. Delete → `Modal` requiring typed rule name to enable the `danger` button. Routing-flow diagram: skipped (prose caption "Rules run in order on every save until a human has acted" suffices).

**4.11 Teams**. Team `Card` list: name/key, active badge, strategy `Select` (least_loaded | round_robin), overflow team `Select` (excludes self), sort order. Members `Table`: name, email, capacity `Input type=number` (0 = ∞, shown as "∞"), active toggle, last assigned, remove. Add member: `Select` over org users (reuse existing `adminApi` users list / `["org-users-all"]`). CRUD via busy-flag async + toasts. Empty → `EmptyState icon={Users} title="No pools yet"`.

**4.12 Request Types**. `Table` of types: ▣ name, key, workstream, stages (chips), fields count, active toggle, sort order; "+ New type" and "▸ Edit fields" open `TypeEditor` `Modal size="xl"`: name/key/workstream/description/sort/active `Field`s, stages editor (ordered `Input` list with add/remove/↑↓ — plain array state), fields editor rows (key, label, kind `Select` over 6 kinds, required checkbox-as-toggle, sort, options textarea "value|label per line" shown only for select). Save replaces fields wholesale (matches backend semantics). Delete confirm `Modal`.

**4.13 Ticket Detail** (`/intake/[id]`). `Breadcrumbs [{label:"Intake", href:"/intake?tab=inbox"},{label: ref}]`. Header `Card`: ref + priority/status/type badges, baton holder `Badge` (🤖 WITH AGENT cyan / HELD · {name} violet / IN QUEUE slate), 🔒 gate badge, live SLA line (pct bar + "Xh Ym elapsed of Nh", slaTone). Grid: left — request field values (`humanizeKey`), description, `WorkflowStrip` + "Advance stage" `Button` (409 → info toast "Already at final stage"), `SlaLegsBar` (full, with legend), `TimelinePanel`; right — AI Triage card (category, risk, est hours, similar matters, confidence, SIMPLE/STANDARD/COMPLEX `Badge`, source, routing rule), `RecommendationCard` (read-only unless staff), `WorkPanel`, `HandoffDialog` trigger, Quick Actions `Card` (Escalate to GC → priority Critical + status Escalated; Mark complete). Requester viewing own ticket sees read-only subset (timeline + workflow + fields); 403 from any panel query hides that panel silently.

**4.14 WorkflowStrip** (shared). Horizontal stepper: per step a dot (`done` → filled `bg-brand-600` + check, `active` → ring `border-brand-600` pulse-free, else `bg-slate-200`) + label (`active` → `text-slate-900 font-medium`, else `text-slate-500`), connected by `h-px bg-slate-200` lines (done segments `bg-brand-600`). Wraps in `overflow-x-auto`. Pure props: `{steps: WorkflowStep[]}`.

**4.15 SlaLegsBar** (shared, NEW primitive). Props `{data: SlaLegsResponse, compact?: boolean}`. A `h-3 rounded-full overflow-hidden flex` bar; each leg a flex-child with `width: max(pct, 2)%` of total elapsed, color by holder: queue `bg-slate-400`, agent `bg-cyan-500`, human `bg-violet-500`; breach marker = absolutely-positioned 2px `bg-red-500` tick at `min(slaMs/totalElapsed, 1)*100%` with "100%" caption. Legend (hidden when compact): per leg — holder label, `elapsed` humanized, `pct_of_sla%`, `Badge tone="blue"` "HOLDING NOW" when active, `Badge tone="red"` "⚠ BREACH HAPPENED HERE" when `breached_during_leg`. Empty/one-leg still renders (single full-width segment).

**4.16 TimelinePanel** (shared). `Card` with `MessageBar intent="info"` header "Chain-sealed · tamper-evident — verifiable in the Audit Log". Rows newest-first: actor-kind icon (User/Bot/Cog, lucide), humanized copy (§6.3), actor name, relative time, and muted `#chain_position · hash_prefix` (mono, `text-slate-500 text-xs`). Collapsed to latest 6 + "Show all (N)". Empty → one-line "No activity yet".

**4.17 HandoffDialog** (shared). `Modal size="md"` title "Pass the baton". Current holder line; `Field` "Pass to" `Select` (A named person / Back to the AI agent / The queue) + assignee `Select` (from `["intake-assignees"]`, required when person, `syncAssignee` implied); `Field` reason `Textarea` (hint: "Recorded on the audit trail"); recent hand-offs list (last 5 from `["intake-handoffs", id]`). Submit disabled until valid; same-holder no-op is rejected server-side → error toast verbatim. **While open, Cockpit shortcuts fully suspend (§5).**

**4.18 WorkPanel** (shared). `Card` "Work / delivery": work status `Select` (Not started / In progress / Blocked / Delivered — distinct from ticket status, caption says so); tasks list — each a `Badge` chip (tone by status: open slate / in_progress blue / blocked red / done green) that cycles status on click, with `+15m +30m +1h` `Button size="sm" variant="ghost"` effort buttons and a summed "Nh Ym logged" line; add-task `Input` + Enter. Empty → "No tasks — add the first" one-liner.

---

## 5. Keyboard system — `useCockpitShortcuts`

`src/components/intake/use-cockpit-shortcuts.ts`:
```ts
useCockpitShortcuts({
  enabled: boolean,          // false while HandoffDialog open, any Modal open, or tab !== "cockpit"
  mode: "normal" | "bulk",
  editing: boolean, reassigning: boolean, cheatsheetOpen: boolean, bulkConfirmOpen: boolean, searchOpen: boolean,
  handlers: { next, prev, approve, edit, reject, reassign, close, snooze, toggleBulk, toggleSelect, openSearch, toggleCheatsheet, escape },
})
```
Single `window.addEventListener("keydown")` (capture: false). **Global guards, in order**: bail if `!enabled`; bail if `e.metaKey || e.ctrlKey || e.altKey`; bail if `document.activeElement` is `input | textarea | select | [contenteditable]` (except `Escape`, which always routes to `escape()`).

| Key | Normal mode | Bulk mode |
|---|---|---|
| `j` / `ArrowDown` | selection +1 (clamped) | same |
| `k` / `ArrowUp` | selection −1 | same |
| `a` | approve — only if `current.agent_recommendation && !editing && !reassigning`; else no-op | open BulkConfirmCard (if selection non-empty) |
| `e` | start edit (copy drafted_response into Textarea; Save+Approve = edited-approved) | — |
| `x` | reject (reason "Attorney rejected recommendation") | — |
| `r` | open ReassignPicker | — |
| `c` | manual close (reason "Manual close — no agent draft sent") | — |
| `s` | snooze (does NOT increment triaged-today) | — |
| `b` | enter bulk mode (clear selection) | exit bulk mode |
| `Space` | — | toggle current ticket in `selected[]` (preventDefault) |
| `/` | show + focus search input (preventDefault) | same |
| `?` (`Shift+/`) | toggle ShortcutCheatsheet | same |
| `Escape` | priority order: close cheatsheet → close reassign → close bulk-confirm → cancel edit → exit bulk mode → close+clear search | same chain |

Verdict effects (client optimistic frame; server response canonical):
- approved / edited-approved / manual-close → `stage:"complete"`, `status:"Completed"`, workflow all done; increment triaged-today; advance after 200 ms.
- rejected → `status:"Triage — Rejected by Attorney"`, `stage:"triage"`; advance.
- reassigned → `assigned_label:{name}`, `assigned_to_user_id`, `status:"Reassigned"`; advance.
- snoozed → `status:"Snoozed"`, `stage:"new"` (drops out of default queue); advance.

**Bulk-mode state machine**: `mode:"normal"` —`b`→ `{mode:"bulk", selected:[]}` —`Space`→ toggle id —`a`→ `bulkConfirmOpen:true` —confirm→ `bulkApprove(selected)` → back to normal, selection cleared; `Esc`/`b` at any point exits per the Escape chain. While `bulkConfirmOpen`, only `Esc`/Enter handled.

Cheatsheet: `Modal size="sm"` two-column key/action list mirroring the table above. Suspension: parent passes `enabled={!handoffOpen && !anyModalOpen}` — the hook is inert, not unmounted, so selection state survives. Every shortcut has a visible button equivalent; verdict results announced via a single `aria-live="polite"` region in CockpitTab ("REQ-3704 approved — Matter created"). Queue list rows use roving tabindex (selected row `tabIndex=0`, others `-1`).

---

## 6. Shared UX rules (`src/components/intake/shared.ts`)

**6.1 SLA posture** — derived at render, never stored:
```ts
pct = round(hoursSince(submitted_at) / sla_hours * 100)
// forced On Track when stage==="complete" || status ∈ {"Completed","Auto-Completed"}
slaTone(pct):  >=100 → red ("Overdue")   >=70 → amber ("At Risk")   else green ("On Track")
// bars: bg-red-500 / bg-amber-500 / bg-green-500; Badge tones red/amber/green; dots ditto
```
Pool/util coloring identical thresholds; `null` utilization → `bg-slate-300` + "∞".

**6.2 Badge tone maps** (available tones: slate|blue|green|amber|red|violet|cyan):
- Status: Awaiting Triage `slate` · Assigned/Reassigned/In Review `blue` · Completed/Approved/Auto-Completed `green` · Snoozed `amber` · Triage — Rejected by Attorney `amber` · Escalated to GC `red`.
- Priority: Critical `red` · High `amber` · Medium `blue` · Low `slate`.
- Holder: agent `cyan` · human `violet` · queue `slate`.
- Confidence: ≥0.8 `green` · ≥0.6 `blue` · ≥0.4 `amber` · else `red`; degraded/mock always `amber`.
- Governance: approval gate `amber` 🔒 · "MATTER LINKED" `violet` · "CHAIN-SEALED" `cyan`.

**6.3 Humanized timeline copy** — `humanizeEvent(action)` map (fallback: title-case last segment):
```
intake.ticket.created            → "Request filed"
intake.routing_rule.fired        → "Routing rule applied — {name}"
intake.ticket.assigned           → "Assigned to {name}"
intake.ticket.agent_no_match     → "No agent matched — queued for manual triage"
intake.ticket.handoff            → "Baton passed: {from} → {to}"
intake.recommendation.approved   → "Response approved by legal"
intake.recommendation.edited_approved → "Response edited and approved by legal"
intake.recommendation.rejected   → "AI draft rejected — manual handling"
intake.recommendation.approval_blocked → "Approval refused — locked to {approver}"
intake.ticket.stage_advanced     → "Moved to {stage}"
intake.ticket.sla_breached       → "SLA breached ({slaHours}h window)"
intake.ticket.auto_escalated     → "Escalated automatically"
intake.ticket.closed             → "Closed"
intake.matter.spawned            → "Matter created from this request"
```
My Requests uses the same map on `latest_event`.

**6.4 Post-submit confirmation card** — after `createTicket` resolves, replace the form (not a toast) with a `Card`: big ref "REQ-NNNN" `text-slate-900`, `Badge tone="green"` "Filed", matched agent + confidence badge (or "No matching agent — queued for manual triage" `Badge tone="slate"`), the type's workflow strip, and two `Button`s: "Open in Inbox" (staff → `/intake/{id}`) / "Track it in My Requests" (`setTab("my-requests")`) + ghost "File another". Copilot path renders the identical card.

**6.5 Empty/dark discipline** — every zeroed KPI `text-slate-300`; every empty pane a one-line strip; filler rows cap 2 + "+N more"; verify each surface in dark mode and with sparse seed data before done.

---

Skipped (with re-entry conditions): Self-Service AI chat (link Ask Aegis; add with an intake FAQ skill), SLA 24h burndown (needs a history series endpoint), routing flow diagram (prose caption), Parties/Conflicts panel (depends on a Person/Counterparty entity the CLM lacks — add with a conflicts backend), dnd library (native drag suffices), list virtualization (60-row paging; virtualize past ~2k rows).


<!-- ================================================== -->
# PART 3 — Work breakdown
> Amended by Part 0 — where they conflict, Part 0 wins.

# Work Breakdown — Native Legal Intake Module (Clean-Room CLM)

Branch: `drl`. Migration head continues from `0021_authority_grants`. Backend verify convention: python-in-container function E2E (`docker compose exec backend python - <<'PY'`), API smoke via urllib after `docker compose restart backend` (uvicorn `--reload` misses new files on macOS — every task here adds new files). Frontend verify convention: `npx tsc --noEmit`, browser preview flow, restore the docker frontend when done. All tests delete their throwaway rows FK-safe (deleting an `intake_request` cascades recs/handoffs/tasks; config rows deleted child-first: members → teams, fields via type cascade, rules before teams they reference). Tests never triage seeded/shared requests and never exercise built-in prompts or live Claude — AI paths are verified via canned `IntakeTriageOutput` payloads and the degraded/template branch.

Dependency notation: `←` = hard dependency (must merge first).

---

## Phase 0 — Spine / manual (zero AI, zero rules, fully usable)

### T0.1 — Migration 0022 + all 8 models + registration
**Files:** `backend/alembic/versions/0022_legal_intake.py`, `backend/app/intake/__init__.py`, `backend/app/intake/models.py`, `backend/app/models.py`
**Depends:** — (first task)
**Delivers:** `intake_ref_seq` + 8 tables (`intake_request_type`, `intake_request_field`, `intake_team`, `intake_team_member`, `intake_routing_rule`, `intake_request`, `intake_agent_recommendation`, `intake_handoff`, `intake_task`, `intake_kb_article`) with all indexes/uniques/partial unique per spec §1.11; SQLAlchemy models composing `(TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base)`; delete-orphan `fields` relationship on `IntakeRequestType`; models imported into `app/models.py` `__all__`.

**Acceptance criteria:**
- `make migrate` runs clean from `0021_authority_grants`; running it twice is a no-op (all SQL `IF NOT EXISTS`).
- `downgrade()` drops indexes → tables → sequence in exact reverse order; upgrade→downgrade→upgrade cycles clean.
- SQLAlchemy inspector shows the partial unique `uq_intake_request_org_extmsg` with its `WHERE` clause and `ix_intake_routing_rule_org_eval` on `(org_id, enabled, eval_order)`.
- No DB enums; JSON columns are JSONB; `intake_request` has no soft-delete column.
- All FK actions match spec (CASCADE to request/type/team; SET NULL for type/contract/team/overflow/recommendation refs).

**Verification:** `docker compose restart backend`; in-container python: run inspector assertions on tables/indexes/FKs; insert an `IntakeRequestType` + 2 `IntakeRequestField` rows, delete the type, assert fields cascaded; `SELECT nextval('intake_ref_seq')` ≥ 4001. Delete nothing else — throwaway type is the only row created and it's gone.

---

### T0.2 — RBAC permissions + authority action
**Files:** `app/core/rbac.py`, `app/roles/service.py`, `app/authority/service.py`, `app/authority/schemas.py`
**Depends:** — (parallel with T0.1)
**Delivers:** `INTAKE_PERMISSIONS = {intake:read, intake:create, intake:update, intake:triage}` OR'd into `ALL_PERMISSIONS`; role defaults (member: read+create; legal_reviewer: all four; approver: read; admin via `ALL_PERMISSIONS`); `_PERMISSION_DESCRIPTIONS` entries; `"intake:approve"` added to authority `ACTIONS` and the schema `Field(pattern=...)`.

**Acceptance criteria:**
- `bootstrap_roles` propagates the four perms to existing orgs' built-in roles on next boot.
- `GET /roles` shows the perms with descriptions; a member token carries `intake:create` but not `intake:triage`.
- Creating an authority grant with `action="intake:approve"` validates; `enforce_authority` for `intake:approve` passes when the org has zero grants (progressive enforcement dormant).

**Verification:** restart backend; urllib smoke as dev admin (`admin@example.com` / local-dev-password): GET roles → assert all four perms present on legal_reviewer; POST an `intake:approve` authority grant then DELETE it (FK-safe, no residue). In-container: call `enforce_authority(action="intake:approve", ...)` for a grant-less org, assert no raise.

---

### T0.3 — Request core: schemas + service + routes + request-type CRUD
**Files:** `app/intake/schemas.py`, `app/intake/service.py`, `app/intake/routes.py`, `app/main.py`
**Depends:** ← T0.1, T0.2
**Delivers:** Endpoints 1–5, 11, 20–23 (create/list/get/patch request; mine; timeline; request-type CRUD with wholesale field replace). The `_transition` choke-point (closed-409, stage_timestamps append, audit + timeline, one commit per call); `_get` 404 helper; status constants (`STATUSES`/`TERMINAL`/`OPEN`/`IMMUTABLE`); ref from sequence; field-value validation against field defs; `external_message_id` idempotent dedupe; T10 kanban stage→status mapping; T12 closed-immutable; serializer-derived `workflow`; `build_sla_legs` pure function + endpoint 9 (needed by the detail response; the SLA *engine* — pause/sweep/ops — comes in T1.5). Routing hook stubbed as a no-op call site (`_apply_routing` lands T1.2); triage-job enqueue call site behind a `job_type` guard (lands T1.3).

**Acceptance criteria:**
- POST request as a member → 201, `ref` = `REQ-4xxx`, status `awaiting_triage`, stage `new`, audit `intake.created` + timeline event written, one commit.
- Missing required field for a typed request → 422 listing the field keys; boolean-required demands explicit true/false.
- Duplicate `external_message_id` in-org → returns the existing request (idempotent, no second row).
- PATCH stage to a mid-stage → status `in_review`; to `complete` → `closed`; any edit after closed → 409 "Request is closed — file a follow-up".
- `stage_timestamps` append-only and server-owned (client-sent values ignored); custom-stage types validate stage ∈ `stages(request)`.
- Request-type PATCH with `fields` replaces wholesale; without `fields` preserves; DELETE type → requests keep `type_label`, FK SET NULL.
- GET endpoints all filter `org_id`; `mine` returns only the actor's rows, cap 100.
- Detail response includes legs (single queue leg for a fresh request, `active=true`, `pct_of_sla` correct).

**Verification:** restart backend; in-container E2E: create throwaway type (with select/date/boolean fields) → create request via `service.create_request` → assert ref/status/audit rows → walk T10 stages to closed → assert 409 on further PATCH → assert legs math on a hand-set `submitted_at` → delete request (cascade) then type; urllib smoke: login, POST /intake/requests, GET list + detail + timeline, DELETE nothing seeded — delete the smoke request row in-container afterward.

---

### T0.4 — Manual triage + handoff ledger + tasks + my-work
**Files:** `app/intake/service.py`, `app/intake/routes.py`, `app/intake/schemas.py`
**Depends:** ← T0.3
**Delivers:** Endpoints 6 (manual verdict subset: `approved` with no recommendation = ungated T4, `rejected`, `reassigned`, `manual_close`, `snoozed` — confirmation/gate seams land T1.4), 8 (handoff with `_validate_handoff` state machine + denormalized holder fields + `sync_assignee`), 13–16 (tasks + effort audit), 17 (my-work: 3 self-scoped queries, ranked overdue → priority → oldest), 18 (assignees).

**Acceptance criteria:**
- `triaged_by_user_id` is ALWAYS the actor — a client-supplied value is ignored (anti-spoof).
- Reassign to a non-org user → 404/422; reassign writes human→human handoff row + `intake.assigned` audit only on FK transition.
- Handoff: `to_holder=human` without `to_user_id` → 422; same-holder same-user pass → 422 "already held by X"; ledger rows never updated; `handoff_holder`/`handoff_user_id`/`handoff_updated_at` mirror the last row.
- Snooze sets `triage_action='snoozed'` without touching triaged-stats fields; excluded from awaiting-triage listing.
- Task effort increments audit `intake.task.effort_logged {minutes}` each call; `minutes <= 0` → 422.
- my-work counts only `OPEN_STATUSES`; `reviews` empty (no recs yet) without erroring.

**Verification:** restart backend; in-container: throwaway request → reassign (assert handoff row + audits) → hand off human→queue→human (assert ledger order + legs now show 4 legs with correct holders) → add task, cycle status, log 15m twice (assert 30 total + 2 audit rows) → snooze → manual_close → delete request (cascade removes handoffs/tasks). urllib smoke: GET /intake/my-work as legal reviewer → 200 shape check.

---

### T0.5 — Frontend data layer + tabbed shell + nav
**Files:** `frontend/src/lib/types.ts`, `frontend/src/lib/endpoints.ts`, `frontend/src/app/(app)/intake/page.tsx`, `frontend/src/components/app-shell.tsx`, `frontend/src/components/intake/shared.ts`
**Depends:** ← T0.3 (API shapes)
**Delivers:** All intake TS interfaces (§3.1); `intakeApi` (§3.2, endpoints that don't exist yet still typed — unused until their tab lands); Intake nav item first in Lifecycle (`Inbox` icon); `/intake` page with PageHeader, awaiting-triage Badge (staff), role-shaped tab list, `?tab=` deep-linking via `router.replace`, tab bodies stubbed as one-line placeholders for tabs landing later; `shared.ts` helpers (`slaTone`, `statusTone`, `priorityTone`, `humanizeEvent`, `STAGE_COLUMNS`, `fmtAge`).

**Acceptance criteria:**
- `npx tsc --noEmit` clean.
- Member sees `new-request / my-requests / self-service`, default `my-requests`; legal_reviewer additionally sees the staff tabs, default `my-work`; teams/request-types admin-only.
- `?tab=inbox` deep-link lands on Inbox; switching tabs rewrites the URL without scroll.
- No `bg-white`/hardcoded colors anywhere (slate scale only); no component defined inside a component body.

**Verification:** `npx tsc --noEmit`; browser preview: login as admin → nav shows Intake → tab set + default per role (spot-check member via a member login) → dark mode toggle sanity on the shell. Restore docker frontend.

---

### T0.6 — Frontend: New Request + My Requests
**Files:** `tabs/new-request.tsx`, `tabs/my-requests.tsx` (+ `page.tsx` prefill state lift)
**Depends:** ← T0.5
**Delivers:** TypePickerGate (COMPLEX/"not sure" shows a "Copilot coming soon — use the form" placeholder until T2.4), StructuredForm (name/department/urgency/type/description + live client regex classify preview line), DynamicFields (6 kinds, required-gating with MessageBar), submit → ConfirmationCard (§6.4); My Requests portal list with show-closed toggle, teaching EmptyState, humanized `latest_event`.

**Acceptance criteria:**
- Selecting a configured type renders its stage chips and dynamic fields; changing type resets field values; missing required fields disable submit and are listed by label.
- Submit shows loading label, then replaces the form with the ConfirmationCard (ref, Filed badge, workflow strip, Open-in-Inbox/Track/File-another); "no matching agent" slate badge shown (always, in phase 0).
- My Requests: newest first, closed hidden by default, empty state action jumps to new-request; SLA dot derives from `submitted_at`/`sla_hours` at render.
- Boolean field = two-button Yes/No; select options from field def.

**Verification:** `npx tsc --noEmit`; preview flow: file a request end-to-end with a seeded type incl. one of each field kind → ConfirmationCard renders → appears in My Requests → verify dark mode + the required-fields MessageBar. Then delete the created request in-container (FK-safe cascade) — no test residue.

---

### T0.7 — Frontend: Inbox + Ticket Detail + shared manual panels
**Files:** `tabs/inbox.tsx`, `[id]/page.tsx`, `src/components/intake/timeline-panel.tsx`, `workflow-strip.tsx`, `work-panel.tsx`, `handoff-dialog.tsx`
**Depends:** ← T0.5; T0.4 (handoff/tasks/timeline APIs)
**Delivers:** Inbox (5 StatCards, filter chips, search, table with inline SLA bars, 60-row windowing + Show more, row tinting); Ticket Detail (Breadcrumbs, header badges incl. baton holder, field values, description, WorkflowStrip + Advance stage w/ 409-info-toast, TimelinePanel with chain-sealed banner + collapse-to-6, WorkPanel, HandoffDialog with recent-handoffs list, Quick Actions). RecommendationCard/SlaLegsBar slots render their adaptive empty strips until T1.6/T1.8.

**Acceptance criteria:**
- Zeroed KPIs `text-slate-300`; breached-stat red only when >0; overdue rows `bg-red-500/5`.
- Windowing: 61+ filtered rows show "Show more"; filters + search compose client-side over `["intake-tickets"]` (30s refetch).
- Detail: requester viewing own ticket gets the read-only subset; 403 panels hide silently; advance at final stage → info toast.
- HandoffDialog: submit disabled until valid; server same-holder rejection surfaces verbatim as error toast; mutation invalidates handoffs/legs/ticket/tickets/timeline.
- TimelinePanel humanizes every action in the §6.3 map, falls back to title-case; shows `#chain_position · hash_prefix` muted mono.
- Empty panes are one-line all-clear strips; adjacent empties merge.

**Verification:** `npx tsc --noEmit`; preview: open a seeded request from Inbox → advance a stage → pass the baton via HandoffDialog → add a task + log effort in WorkPanel → confirm timeline grows and badges update → dark mode pass. Mutations hit seeded *demo* rows only via UI actions that are demo-legitimate (stage/handoff/task on the designated in-flight seeded request is intentional demo history per spec §7); anything created fresh gets deleted in-container.

---

### T0.8 — Seed, part 1 (KB + types + manual-lifecycle requests)
**Files:** `app/devtools.py`
**Depends:** ← T0.3, T0.4
**Delivers:** Idempotent seed block (local/development/test only, match-by-key/ref, after demo users): 5 KB articles, 3 request types with fields/stages per §7, 4 non-AI sample requests created through `service.create_request` as user1@/user2@ — 1 contract review (assigned, stage review), 1 general question (queue), 1 approved+closed NDA (full manual lifecycle: reassign → handoffs → approve so timeline/legs are populated), 1 open NDA. Teams/rules/recommendations/backdated-overdue land in T1.10.

**Acceptance criteria:**
- Running seed twice creates zero duplicates (match by `source_ref`/`key`/`ref`).
- Skipped entirely outside local/development/test (same guard as demo users).
- The closed NDA has ≥2 handoff rows, triage stamps, and a complete stage_timestamps trail; all seed audit rows are real service-written rows.

**Verification:** restart backend; in-container: run seed twice, assert row counts identical; assert the closed request's legs render ≥3 legs and `closed=true`; urllib GET /intake/requests → seeded refs present. Seeded data is the demo dataset — intentionally not cleaned up.

---

## Phase 1 — Cockpit + AI + routing + SLA

### T1.1 — Teams, members, pool balancer
**Files:** `app/intake/service.py`, `routes.py`, `schemas.py`
**Depends:** ← T0.3
**Delivers:** Endpoints 24–30; `_pick_from_pool` (§3.3: `FOR UPDATE` member lock, open-count group-by inside the txn, least_loaded/round_robin with deterministic tie-breaks, capacity-0 = unbounded, overflow chain with visited-set cycle break); team delete nulls rule `set_team_id` + other teams' `overflow_team_id`; self-overflow rejected 422.

**Acceptance criteria:**
- least_loaded picks min (open_count, last_assigned_at NULLS FIRST, user_id); round_robin picks oldest cursor; cursor bumped under the lock so two picks in one txn never stack one member.
- Member at capacity excluded; all-at-capacity + no overflow → `None` (unassigned); overflow pick flagged `overflow=True`; A→B→A cycle terminates.
- Team delete leaves rules intact with `set_team_id` NULL; members cascade; config CRUD audits (`intake.team.created` etc.).

**Verification:** restart backend; in-container E2E: throwaway team ×2 (overflow chained) + 3 members with capacities 1/1/0 → create throwaway open requests to load members → call `_pick_from_pool` repeatedly asserting strategy order, capacity exclusion, overflow flag, cycle break → delete requests, members, teams (child-first). urllib smoke: team CRUD round-trip as admin, DELETE at the end.

---

### T1.2 — Routing engine + rule CRUD
**Files:** `app/intake/service.py`, `routes.py`, `schemas.py`
**Depends:** ← T1.1 (pool action), T0.3 (choke-points)
**Delivers:** Endpoints 31–34 (≥1 condition + ≥1 action validation, null-clears/absent-preserves, org-scoped target existence checks, `serialize_rule` name resolution); `_apply_routing` (§3.2: cumulative-ordered eval, never-override-human guard, honest `times_fired` on newly-fired only, escalate idempotence, approval-gate stamping, `fired_rules` full rewrite, per-rule audit with NULL actor) wired into `create_request` and `update_request`.

**Acceptance criteria:**
- Rule with conditions only or actions only → 422; keyword match is ci-substring of description; all non-null conditions AND.
- Later rules see earlier effects (rule 1 sets priority Critical → rule 2 matching Critical fires in the same pass).
- No-op match (values already equal) does not increment `times_fired` and writes no audit.
- Request with `triaged_by_user_id` or `triage_action` set: `_apply_routing` returns immediately — a PATCH after human triage changes nothing rule-driven.
- Escalate action: assign + Critical + status `escalated`, then stops firing on re-saves (idempotent); `require_approval_from` stamps `approval_gate_user_id` (server-owned).
- Re-fire on PATCH audits only rule ids not in prior `fired_rules.rule_ids`.

**Verification:** restart backend; in-container: 3 throwaway rules (priority-set, pool-route, escalate) + throwaway request matching all → assert cumulative effects, fired_rules summaries, times_fired=1 each, audits present → PATCH description (no-op re-eval) → counters unchanged → manually triage then PATCH → guard holds → delete request, rules, team. urllib smoke: rule CRUD round-trip incl. the 422s, then DELETE.

---

### T1.3 — AI layer: classifier, agent registry, skills, triage job, recommendation+confirmation
**Files:** `app/intake/agents.py` (new), `app/ai/schemas.py`, `app/ai/registry.py`, `app/ai/prompt_versions.py` (+ `intake_triage.v1.md` doc), `app/jobs/tasks.py` (`intake_triage` branch in `_run_ai_job`), `app/intake/service.py` (`apply_triage_result`, `_create_intake_confirmation`, auto-baton, KB keyword retrieval)
**Depends:** ← T0.4 (handoffs), T1.2 (routing runs first at create)
**Delivers:** `classify()` (10 ordered regex rules + fallback, stamped to `ai_triage`); `AGENTS` registry with `can_handle` order + per-agent prompt additions + hard floors (§5.1 table incl. code-side sanctions screen, litigation always-flag, ordinal→numeric confidence map); 2 SkillSpecs + output models + prompts; job wiring: create → JobRun(`intake_triage`) → classify/select (code) → `run_job_skill` → `apply_triage_result` (floors, recommendation row + `skill_run_id`, AIConfirmation with SLA-due `expires_at`, exactly-2 auto-baton rows gated on `agent_processed_at` null→set, T2/T3 transition, audits with NULL actor); degraded template path on `AIUnavailable`.

**Acceptance criteria:**
- `classify` order honored (employment-sensitive beats NDA when both match); <10-char text → fallback.
- `apply_triage_result` with a canned litigation output `suggested_action="approve_and_send"` → stored as `flag_for_review` with forced concerns; sanctions-hit vendor output → `escalate` conf 0.92; screen-unavailable → `flag_for_review` conf 0.4; NDA conf `medium` → base−0.15.
- Exactly 2 handoff rows per pipeline pass (`actor_type='agent'`, `created_by_user_id` NULL), second row carries `recommendation_id`; matched+assignee → to human; matched+unassigned → to queue; no-match → to queue with the no-match reason; plus one `intake.handoff` summary audit `{auto, outcome, passes:2}`.
- Re-running the job on a processed request writes nothing (dedupe on `agent_processed_at`).
- Confirmation `expires_at` = `submitted_at + sla_hours`; degraded rec has `degraded=True`, conf 0.4, template concerns.
- Nothing auto-sends: PENDING has no exit path in this task.

**Verification:** restart backend (+ worker: `docker compose restart backend worker` if split); in-container E2E only with canned `IntakeTriageOutput` objects — never a live Claude call, never a built-in prompt launch: throwaway request → `classify` asserts → `apply_triage_result(canned)` → assert rec/confirmation/handoffs/audits → rerun → dedupe holds → simulate `AIUnavailable` path → degraded rec on a second throwaway → delete both requests (cascades recs/handoffs; confirmation rows removed by their own FK/cleanup path — verify no orphans).

---

### T1.4 — Full triage verdicts: approval gate + authority + confirmation seam + bulk + promote
**Files:** `app/intake/service.py`, `routes.py`, `schemas.py`
**Depends:** ← T1.3 (recommendations exist), T0.2 (authority action)
**Delivers:** `record_triage_action` full T4–T8: approved/edited_approved flip rec (+`edited_at`, canonical `override_reason`), `confirm_confirmation` in the same txn, gate refusal §2.5 (isolated-session `record_decision` + intake audit committed BEFORE the 403), `enforce_authority("intake:approve")` with contract-or-shim, resolved-rec 409, reject → `reject_confirmation`, manual_close/snooze/reassign leave PENDING; endpoint 7 bulk-triage (per-id txn, gate refusals reported not raised); endpoint 12 promote (T11: contract/project stamp + `intake.promoted` audit, approved-or-ungated guard).

**Acceptance criteria:**
- Gate mismatch (non-admin, wrong user) → 403 AND both the `intake.approval_blocked` audit and the `record_decision` denial survive the rollback; org admin bypasses.
- Approve: rec `approved`, confirmation confirmed (`assistant.confirmation_confirmed` audit), `triaged_by_user_id=actor` regardless of payload, SLA clock stops (status terminal); edited_approved additionally stamps `edited_at` + override_reason.
- Second verdict on a resolved rec → 409; a NEW triage run appends a new rec row, never overwrites.
- Expired confirmation → 409 from `confirm_confirmation` surfaces cleanly.
- bulk-triage returns `{results:[{id, ok, error?}]}` — one gated failure doesn't poison the batch.
- Promote sets `contract_id` (FK) or `project_id` (plain), audits target+id; blocked when a rec exists and isn't approved.

**Verification:** restart backend; in-container: throwaway request + canned rec + gate stamped to legal1@ → approve as legal2@ → 403, assert both audit rows persisted → approve as legal1@ → assert full stamp set + confirmation state → re-approve → 409 → second throwaway for reject path → bulk over both (one already resolved → per-id error) → promote the approved one to a throwaway contract → delete requests then the throwaway contract. urllib smoke: POST triage approve on a throwaway created via API, then clean up in-container.

---

### T1.5 — SLA engine: pause, sweep, escalation, sla-ops
**Files:** `app/jobs/tasks.py` (`check_intake_slas`), `app/jobs/celery_app.py` (beat entry), `app/intake/service.py` (pause/resume, `get_sla_ops_summary`), `routes.py` (endpoints 10, 40)
**Depends:** ← T0.3 (`build_sla_legs`), T0.4
**Delivers:** Pause semantics §4.3 (pause/resume audits, terminal auto-resume folds live pause); beat entry every 15 min; sweep §4.4 (tier edges: at_risk on `on_track`→0.7, breach on ≠overdue→1.0 with escalate + 2 audits + timeline + notify, pause-adjusted pct, import-safe, empty-table no-op); sla-ops aggregate §4.5.

**Acceptance criteria:**
- `build_sla_legs` with a paused window shifts `breach_ts` by the pause; `breached_during_leg` lands in exactly one leg; zero-length passes advance holder without an empty leg; clamping guards clock skew.
- Sweep on a request backdated past breach → `overdue` + `escalated` + `intake.sla_breached` + `intake.auto_escalated` + timeline + admin notify; immediate second run → all counters 0 (edge-triggered).
- Paused and terminal and already-escalated requests never scanned; 70% request gets `at_risk` + assignee notify once.
- Terminal transition on a paused request folds `paused_at` into `paused_ms_total` and nulls it.
- sla-ops pct formula identical to the sweep's; `breaches_7d` counts audit rows; `by_holder` from denormalized `handoff_holder`; `oldest_open` top 5.

**Verification:** restart backend; in-container: throwaway request, backdate `submitted_at` −2× sla → call `check_intake_slas()` directly → assert breach effects → call again → `{scanned.., at_risk:0, breached:0}` → second throwaway at ~75% → at_risk edge once → pause it → third run skips it → resume, assert `paused_ms_total` > 0 and legs `breach_ts` shifted → GET /intake/sla-ops via urllib, assert the two throwaways counted → delete both requests. Confirm the beat entry loads: `celery inspect registered` or import `celery_app` and assert the schedule key.

---

### T1.6 — Frontend: Triage Cockpit + RecommendationCard + keyboard system
**Files:** `tabs/cockpit.tsx` (CockpitTab, BulkConfirmCard, ReassignPicker, ShortcutCheatsheet — all top-level), `src/components/intake/recommendation-card.tsx`, `use-cockpit-shortcuts.ts`, `page.tsx` (AgentSettings modal hook-up)
**Depends:** ← T0.7 (shared panels), T1.4 (verdict API)
**Delivers:** Two-column cockpit (§4.3): queue ordering, position/triaged-today localStorage, search via `/`, snoozed chip toggle, left detail card (AI triage block, fired-rules chips, collapsible transcript, WorkflowStrip, compact SlaLegsBar slot), right rail (RecommendationCard with edit mode + degraded badge + concerns, WorkPanel, HandoffDialog trigger), approval-gate MessageBar, visible verdict footer + cheatsheet; full shortcut table §5 (guards, bulk-mode state machine, Escape chain, suspension via `enabled` prop, roving tabindex, aria-live announcements); optimistic verdicts §3.4 (patch cache, 200 ms advance, rollback + selection-return on error incl. 403 gate refusal, matter deep-link toast when `spawned_matter_id` returned).

**Acceptance criteria:**
- `a` no-ops without a recommendation or while editing/reassigning; `s` doesn't increment triaged-today; every shortcut bails on cmd/ctrl/alt and focused inputs (except Escape).
- Bulk: `b` → Space toggles → `a` opens confirm modal with per-agent breakdown → confirm calls bulkApprove → normal mode, selection cleared; Esc chain order exact.
- Shortcuts fully suspend (inert, not unmounted — selection survives) while HandoffDialog/any modal open.
- 403 approval-gate refusal: ticket rolls back, error toast verbatim, selection returns.
- Edit mode: Save+Approve sends `edited_response` → server shows edited_approved; Cancel restores.
- Empty queue → "Queue clear" EmptyState; footer buttons mirror every shortcut (a11y).

**Verification:** `npx tsc --noEmit`; preview flow: create 2 throwaway requests via the New Request form (never triage seeded demo recs) → in-container attach canned recs via `apply_triage_result` → cockpit: j/k navigate, `e`-edit-approve one, `x`-reject the other, `?` cheatsheet, `b`+Space+`a` bulk on a third throwaway, verify aria-live text and dark mode → delete all throwaway requests in-container (cascade).

---

### T1.7 — Frontend: My Work + Kanban
**Files:** `tabs/my-work.tsx`, `tabs/kanban.tsx`
**Depends:** ← T0.7; T0.4 (my-work API)
**Delivers:** My Work (§4.1: three bordered sections, baton-passed badge, one-line all-clear strips that merge, inbox-zero EmptyState); Kanban (§4.7: 5 STAGE_COLUMNS, native HTML5 drag, TanStack optimistic mutation §3.4 with `cancelQueries`, synchronous `setQueryData` in onDragEnd, isMutating-guarded onSettled, `STAGE_STATUS` + `rebuildWorkflow` client mirrors).

**Acceptance criteria:**
- Drag renders zero snap-back on success; on server error the card returns to its source column with an error toast.
- Two rapid drags don't clobber each other (mutationKey + isMutating guard).
- Drop on Complete → all workflow steps done; server response remains truth after invalidation.
- My Work rows route to `/intake/{id}`; blocked tasks red accent; `counts.total===0` → single EmptyState.
- Empty column = dashed drop zone; critical tally badge only when >0.

**Verification:** `npx tsc --noEmit`; preview: My Work as legal reviewer with the seeded assigned request → Kanban drag a throwaway request New→Assigned→Complete (creates closed state — throwaway, not seeded) → refresh, position persists → dark mode → delete the throwaway in-container.

---

### T1.8 — Frontend: SLA dashboard + SlaLegsBar + detail/cockpit SLA integration
**Files:** `tabs/sla-dashboard.tsx`, `src/components/intake/sla-legs-bar.tsx`, edits to `[id]/page.tsx` + `tabs/cockpit.tsx` (fill the slots)
**Depends:** ← T1.5 (sweep + sla-ops), T0.7, T1.6
**Delivers:** SlaLegsBar primitive (§4.15: min-2% segments, holder colors, breach tick, legend with HOLDING NOW / BREACH HAPPENED HERE, compact mode); SLA tab (§4.8: SlaOpsPanel stats, workload mini-table, rule-effectiveness top-5, admin-only Run-breach-scan button with busy + toasts, 4 StatCards, by-team rollup, breach/at-risk list); legs bar mounted full on detail, compact on cockpit.

**Acceptance criteria:**
- Backdated seeded request shows a red breach tick inside the correct leg and the "⚠ BREACH HAPPENED HERE" badge on exactly one leg.
- Single-leg fresh request still renders full-width; compact mode hides legend.
- Run breach scan: success toast "Escalated N", invalidates sla-ops/tickets/timeline-broad; non-admin doesn't see the button; a 403 (defense) surfaces as error toast.
- Zeros muted; burndown intentionally absent.

**Verification:** `npx tsc --noEmit`; preview: SLA tab shows the seeded overdue request (T1.10) in breaches list → open its detail → legs bar + legend + breach badge → admin clicks Run breach scan → toast + counts refresh → dark mode. No residue: scan only touches the intentionally-overdue seeded row (its escalation is designed demo history).

---

### T1.9 — Frontend: Routing + Teams admin tabs
**Files:** `tabs/routing.tsx` (RoutingTab, RuleEditor, RuleDetail), `tabs/teams.tsx` (TeamsTab, TeamEditor, MemberRow)
**Depends:** ← T1.1, T1.2 (APIs), T0.5
**Delivers:** Routing (§4.10: 4 StatCards + LIVE badge, rule table with enable toggle, detail pane, RuleEditor modal with client-mirrored ≥1-condition+≥1-action gate, typed-name delete confirm; read-only for non-admin staff); Teams (§4.11: strategy/overflow selects (self excluded), capacity "∞" rendering, member add from org users, busy-flag CRUD + toasts).

**Acceptance criteria:**
- Save disabled with hint until 1 condition + 1 action; delete button disabled until typed name matches exactly.
- Staff without admin sees rules read-only (no editor/toggles); assignee/team/escalate/approver selects populate from live queries.
- Team overflow select excludes self; capacity 0 renders "∞"; rule/team CRUD invalidations per §3.3 map (team CRUD also invalidates pool-ops + routing-rules).

**Verification:** `npx tsc --noEmit`; preview as admin: create throwaway team + member → create throwaway rule targeting it → toggle disable/enable → typed-name delete the rule → remove member, delete team (UI order = FK-safe) → confirm both lists return to seeded-only → dark mode. Zero residue by construction.

---

### T1.10 — Seed, part 2 (teams, rules, recommendations, overdue)
**Files:** `app/devtools.py`
**Depends:** ← T1.1–T1.4
**Delivers:** §7 completion: 2 teams (`contracts-pool` least_loaded caps 5/5; `escalations` round_robin unbounded, overflow target), 3 routing rules (eval 10/20/30: NDA→pool+24h, Critical→escalate, "vendor"→gate+48h), remaining sample requests — 2 NDAs with seeded PENDING recs via `apply_triage_result` + canned `IntakeTriageOutput` (no Claude), 1 litigation flag_for_review rec, one request backdated −30h for the overdue panel.

**Acceptance criteria:**
- Idempotent re-run (match-by-key/ref); recs/handoffs/audits are real service-written rows; rules show real `times_fired` from the seeded creates.
- Cockpit queue, My Work reviews, SLA overdue list, Pool Ops (once T2.5 lands) all non-empty from seed alone.

**Verification:** restart backend; in-container: seed twice, counts stable; assert the NDA rec is PENDING with 2 auto-baton handoffs and confirmation `expires_at` = due instant; urllib GET sla-ops → overdue ≥1. Demo data — no cleanup.

---

## Phase 2 — Deflection + ops + copilot

### T2.1 — Backend: KB article CRUD
**Files:** `app/intake/service.py`, `routes.py`, `schemas.py`
**Depends:** ← T0.3 (retrieval helper already reads the table from T1.3)
**Delivers:** Endpoints 35–38 (`source_ref` unique per org, tags array, active flag, config audits). Retrieval stays keyword-overlap top-3 (ponytail ceiling documented in code).

**Acceptance criteria:** duplicate `source_ref` in-org → 409/422; deactivated article excluded from retrieval; CRUD audits with matching resource_type; list readable by `intake:read`, mutations `_MANAGE`.

**Verification:** restart backend; in-container: create throwaway article, assert retrieval surfaces it for a matching description and drops it when `active=false`, delete it; urllib smoke GET /intake/kb.

---

### T2.2 — Backend: copilot turn skill + endpoint
**Files:** `app/ai/registry.py`/`prompt_versions.py` (already stubbed in T1.3 — activate `intake_copilot`), `app/intake/service.py::copilot_turn`, `routes.py` (endpoint 19)
**Depends:** ← T1.3
**Delivers:** Synchronous `run_structured_skill` turn (`commit=True`, no JobRun, cost guard + AICallLog automatic); state merge (only new/updated keys), ready/topic-switch passthrough; `conversation` persisted onto the request at create when `source='copilot'`.

**Acceptance criteria:**
- 403 without `intake:create`; degraded/AIUnavailable → 503-or-fallback message, never a crash; `fields_extracted` merged into returned state without dropping prior keys; output validated against `IntakeCopilotTurnOutput` (bad model output → clean error).
- No JobRun rows created; AICallLog row per turn.

**Verification:** restart backend; in-container: monkeypatch/stub the model client to return a canned `IntakeCopilotTurnOutput` (never a live prompt), call `copilot_turn` twice asserting state merge + ready flag; assert AICallLog count +2 then rely on existing log-pruning policy (telemetry, not residue); create one copilot-sourced throwaway request asserting `conversation` persisted, delete it.

---

### T2.3 — Frontend: Copilot chat + transcript surfaces
**Files:** `tabs/copilot.tsx` (CopilotChat, ConversationStateCard, TopicSwitchBanner), edits to `tabs/new-request.tsx` (gate wiring, switch toggles), cockpit/detail transcript collapsibles already slotted (T1.6/T0.7 — remove placeholders)
**Depends:** ← T2.2, T0.6
**Delivers:** §4.4 copilot branch: COMPLEX/"not sure" mounts chat in place, switch-to-form/back preserved, extracted-fields state card, topic-switch banner, ready → same ConfirmationCard as the form path; transcripts render on cockpit + detail when `conversation` present.

**Acceptance criteria:**
- Turn round-trip renders assistant message <70w, updates the state card; `ready=true` enables Submit which files via `createTicket` with `source:"copilot"` + transcript; topic switch shows the banner with the suggested type.
- Chat error → MessageBar, form fallback always available; identical ConfirmationCard.

**Verification:** `npx tsc --noEmit`; preview: pick a COMPLEX type → 2 chat turns → submit → ConfirmationCard → open the ticket, transcript collapsible present → delete the throwaway request in-container.

---

### T2.4 — Backend: pool-ops aggregate
**Files:** `app/intake/service.py::get_pool_ops_summary`, `routes.py` (endpoint 39)
**Depends:** ← T1.1, T1.5
**Delivers:** Read-time attribution over current assignee + audit rows: per-team utilization (capacity-0 members excluded from `capacity_total`), complexity mix from `ai_triage`, overflow-in from handoff/audit evidence, routed counts from `intake.routing_rule.fired`, closed 7d/30d, effort minutes, `unassigned_open`.

**Acceptance criteria:** pure read (zero writes); `days` param bounds the window; team with only unbounded members → `utilization_pct: null`; numbers reconcile against seed (e.g. routed_count ≥ rules' times_fired attributable to the team).

**Verification:** restart backend; in-container: compute summary, cross-check `open_total` against a direct OPEN_STATUSES count for seeded pool members; urllib GET /intake/pool-ops?days=7 → 200 shape. No writes → no residue.

---

### T2.5 — Frontend: Pool Ops tab
**Files:** `tabs/pool-ops.tsx`
**Depends:** ← T2.4, T1.9
**Delivers:** §4.9: 6 StatCards, per-team cards (strategy pill, VIA OVERFLOW badge, utilization bar with utilColor + "∞", complexity chips, stat line, member rows), Refresh, teaching EmptyState routing admins to Teams.

**Acceptance criteria:** null utilization renders slate "∞"; ≥100 red / ≥70 amber thresholds match `slaTone` discipline; effort shown only ≥60m; overflow badge only when >0; zeros muted.

**Verification:** `npx tsc --noEmit`; preview with seed: both teams render with correct member math (hand-check one member's open/capacity against Inbox); Refresh invalidates; dark mode. Read-only tab — no residue.

---

### T2.6 — Frontend: Self-Service + Request Types admin
**Files:** `tabs/self-service.tsx` (+ `src/components/intake/kb.ts` static module), `tabs/request-types.tsx` (TypeEditor, FieldEditor)
**Depends:** ← T0.5; T0.3 (type CRUD API)
**Delivers:** Self-Service §4.6 (search + category chips, article grid → detail, "File a ticket" pre-fills new-request via lifted `prefill`, 2 real StatCards, Ask-AI = link to Ask Aegis); Request Types §4.12 (type table, TypeEditor XL modal with ordered stages editor + field rows incl. options textarea for selects, wholesale-replace save, delete confirm).

**Acceptance criteria:**
- "File a ticket" lands on new-request with description pre-filled; no fabricated FAQ metrics (omitted).
- TypeEditor: stage add/remove/↑↓ works on plain array state; options textarea only for select kind; save round-trips fields wholesale (edit → save → reopen shows replaced set); new type immediately appears in New Request picker marked "▣" with its stage chips.

**Verification:** `npx tsc --noEmit`; preview as admin: create throwaway type with 2 stages + 3 fields → file a request against it via New Request (dynamic fields + custom stage chips render) → delete the throwaway request in-container, then delete the type via UI (requests-keep-label semantics already covered in T0.3) → Self-Service: search, open article, File-a-ticket prefill → dark mode.

---

## Rollout checklist

1. **Migration:** `make migrate` (0021 → 0022) on every environment before deploying app code; verify `intake_ref_seq` and the partial unique index exist. Rollback = `alembic downgrade 0021_authority_grants` (drops all intake data — export first if real).
2. **RBAC:** deploy triggers `bootstrap_roles` — confirm member/legal_reviewer/approver picked up the four `intake:*` perms in each org (`GET /roles`); custom roles need manual grants via the roles admin.
3. **Authority:** `intake:approve` in `ACTIONS`; gate is dormant until an org writes its first grant — note this in release comms so admins know to add DoA grants deliberately.
4. **Feature flags:** `feature.ai.intake_triage`, `feature.ai.intake_copilot` enabled by default — flip off per-org if a tenant wants manual-only intake.
5. **Celery:** restart worker + beat so `check-intake-slas` (*/15) registers; confirm via `celery inspect registered`.
6. **Seed:** run `seed` on local/dev/test after demo users; verify idempotency (run twice); production skips automatically.
7. **Prompts:** `DEFAULT_SKILL_PROMPTS` rows for `intake_triage`/`intake_copilot` present; `intake_triage.v1.md` committed.
8. **Frontend:** `npx tsc --noEmit` clean; restore/rebuild the docker frontend image; verify `/intake` nav for each role and dark mode on all 12 tabs.
9. **README note:** add an "Intake module" section — module layout (`app/intake/` mirrors `app/authority/`), the 4 perms and role defaults, the two feature flags, the beat schedule, seed contents, and the deliberate cuts list (email polling = API seam via `external_message_id`, single SLA window, no AgentDecision table).
10. **Docker note:** every backend deploy of this module includes new Python files — `docker compose restart backend` in dev, image rebuild in prod.

---

## Demo script — all 12 surfaces (seeded org, ~10 min)

*Login as `user1@` (member) for 1–3, `legal1@` (legal_reviewer) for 4–10, `admin1@` for 11–12.*

1. **New Request** — file an NDA: pick the "NDA Request" type (▣, stage chips appear), fill counterparty + mutual direction, watch the live classify preview line, submit → ConfirmationCard shows `REQ-4xxx`, matched `nda_agent` + confidence badge, workflow strip. Click "Track it in My Requests".
2. **My Requests** — the new NDA sits on top with friendly status + SLA dot; toggle "Show closed" to reveal the seeded completed NDA with its humanized latest event.
3. **Self-Service** — search "remote work", open the policy article, click "File a ticket" → new-request opens pre-filled. Back out.
4. **My Work** (switch to legal1@) — "Awaiting my review" shows the NDA with the amber border; a seeded ticket shows the violet "baton passed to you" badge. Click through to the ticket.
5. **Ticket Detail** — header: baton badge (🤖 WITH AGENT), live SLA bar; scroll the SlaLegsBar legend (queue→agent→human legs), TimelinePanel's chain-sealed banner and `#position · hash` rows; log +30m in WorkPanel; pass the baton via HandoffDialog and watch a new leg appear.
6. **Inbox** — 5 KPI cards (SLA breached red — the backdated seed), filter "SLA breached", point at the row tint and inline SLA bars; search a ref.
7. **Triage Cockpit** — `j/k` through the queue; open the NDA: RecommendationCard (agent badge, confidence, concerns MessageBar), press `e`, tweak the draft, Save+Approve → aria-live announces, queue advances; press `?` for the cheatsheet; `b` + Space + `a` to bulk-approve two FAQ drafts with the per-agent confirm modal. Show the vendor request's 🔒 gate MessageBar — approve as the wrong user to demo the 403 + audit-surviving refusal.
8. **Kanban** — drag a ticket Assigned → In Review (no flicker), drop one on Complete → workflow fully done; column critical tallies.
9. **SLA Dashboard** — overdue seeded request in the breaches list; open it to show the breach tick inside the owning leg; (as admin) hit "Run breach scan" → "Escalated N" toast; rule-effectiveness list shows real `times_fired`.
10. **Pool Ops** — contracts-pool utilization bar amber, member open/capacity rows, complexity mix chips, overflow badge on escalations; Refresh.
11. **Smart Routing** (admin) — three seeded rules with honest fire counts; open RuleEditor, show the ≥1-condition+≥1-action save gate; typed-name delete confirm (cancel it).
12. **Teams / Request Types** (admin) — teams: strategy + overflow selects, capacity "∞"; request-types: open the Litigation type's TypeEditor, show the custom stage rail (conflicts-check → assessment → response) and the field editor — then jump back to New Request to show those stages rendered end-to-end.

Close: Audit Log page filtered to `intake.*` — creation, rule firings, agent handoffs, the gate refusal, and the confirmed AIConfirmation, all chain-sealed.


<!-- ================================================== -->
# APPENDIX — Adversarial review findings (traceability)

**1. [BLOCKER] Frontend spec §3.2 intakeApi vs Backend spec §6 route table**
- Problem: The two specs describe two different APIs. Frontend calls /intake/tickets/* while the backend mounts /intake/requests/*; and the frontend requires endpoints the backend never defines: POST /tickets/{id}/stage (moveStage), POST /tickets/{id}/advance-stage, POST /tickets/bulk-approve, GET /tickets/{id}/handoff (list — backend only has POST, route 8), GET /tickets/{id}/sla-legs (backend: /requests/{id}/sla), POST /tickets/{id}/work-status (backend: PATCH field), GET /intake/my-requests (backend: /requests/mine), POST /intake/documents (file upload in §4.4). Response shapes also disagree: bulkApprove expects {approved, spawned_matter_ids} vs backend bulk-triage {results:[{id, ok, error?}]}; triage() expects TriageActionResult {ticket, spawned_matter_id} vs backend's IntakeRequestDetailResponse. Nothing on the /intake tab will load as written.
- Resolution: Pick one noun (backend's 'requests' — it matches the module and audit actions) and rewrite frontend §3.2 against the backend §6 table verbatim: listTickets→GET /intake/requests, myRequests→GET /intake/requests/mine, slaLegs→GET /intake/requests/{id}/sla, bulkApprove→POST /intake/requests/bulk-triage with {ids, action:'approved'} and adapt to {results}. Then close the real gaps on the backend side: add GET /intake/requests/{id}/handoffs (or document that the detail response feeds ["intake-handoffs"]), fold moveStage/advanceStage into PATCH {stage} (T10 already covers it), and either add the /intake/documents upload route or cut the file input from §4.4. Delete spawned_matter_id everywhere — promotion is an explicit separate call (T11), triage never spawns matters.

**2. [BLOCKER] Backend spec §5.3 (approve seam step 4: confirm_confirmation) and §2.3 T4**
- Problem: Reusing confirm_confirmation/reject_confirmation as-is breaks the approve seam. _get_pending_confirmation (app/ai/confirmations.py:127-145, verified in code) 403s unless the actor is the confirmation's created_by_user_id or an org admin. Intake confirmations are created by the system Celery triage job (created_by_user_id NULL per the module's actor convention for system writes), so every non-admin legal_reviewer's T4 approve raises 403 'Only the requesting user or an org admin can decide this confirmation'. Additionally confirm_confirmation does db.get(AssistantToolCall, confirmation.tool_call_id) — tool_call_id is NULL for intake confirmations (no AssistantToolCall exists), and Session.get with a None primary key raises rather than returning None.
- Resolution: Don't call confirm/reject_confirmation directly. Add an intake decision path: either extend _get_pending_confirmation with an already-authorized bypass parameter (the route already enforced intake:triage + the approval gate), or have _create_intake_confirmation stamp created_by_user_id = assigned_to_user_id and add a small service._decide_intake_confirmation that does the SELECT FOR UPDATE / 409-expired / status-flip / audit itself, skipping the AssistantToolCall mirror when tool_call_id is NULL. Spec must name this explicitly — it's the core write path.

**3. [BLOCKER] Backend spec §2.3 T2 / §5.3 (confirmation expires_at = SLA due instant)**
- Problem: Terminal-trap state: setting the AIConfirmation's expires_at to submitted_at + sla_hours means the moment a request breaches SLA, _get_pending_confirmation flips the confirmation to EXPIRED and 409s — so overdue requests (exactly the ones the T9 escalation is screaming about) can never be approved or edited-approved again. The PENDING recommendation is permanently stuck; the only exits are reject or manual_close, and 'new triage runs append a NEW recommendation row' never happens because the triage job is gated by agent_processed_at IS NULL (fires once, ever). Also the expiry ignores pause (§4.3 shifts breach_ts but nothing shifts expires_at), and the §7 seed's −30h backdated request with a pending rec is born expired, breaking the demo.
- Resolution: Decouple confirmation expiry from the SLA: leave expires_at NULL (the structural gate is 'PENDING has no exit except record_triage_action', §5.4 — expiry adds nothing), or on a 409-expired in record_triage_action mint a fresh confirmation in the same txn and proceed. If SLA-linked expiry is kept, extend it on pause/resume and specify a re-arm path from escalated.

**4. [BLOCKER] Backend spec §2.3 T10 + T12; Frontend spec §3.4 Kanban drag / §4.7**
- Problem: T10 maps a stage drag to 'complete' → status closed, guarded only by intake:update — but close is otherwise an intake:triage action (T6), and closed is terminal-immutable (T12, 409 forever). So any member of staff with update-but-not-triage closes requests by dragging a kanban card, bypassing the triage permission, the recommendation verdict, and the approval gate (§2.5) — and one accidental drop onto the Complete column is irreversible. The frontend spec makes Complete a normal drop target with an optimistic move.
- Resolution: In T10, treat 'complete' specially: require intake:triage for the complete drop (route can branch the dependency, or service checks actor permissions), leave the pending recommendation/confirmation handling to the T4/T6 paths (drag-to-complete = manual_close semantics, audited as such), and have the frontend either hide the Complete drop zone for non-triage users or route the drop through the triage endpoint with a confirm modal instead of the optimistic moveStage.

**5. [BLOCKER] Backend spec §1.11 'Registration' + Integration checklist row 'app/models.py'**
- Problem: Both say 'import 8 intake models', but §1 defines 10 model classes: IntakeRequest, IntakeRequestType, IntakeRequestField, IntakeRoutingRule, IntakeTeam, IntakeTeamMember, IntakeHandoff, IntakeAgentRecommendation, IntakeTask, IntakeKbArticle. Following the spec literally leaves two models out of app/models.py — the metadata registry Alembic/devtools use — so reset-db/autogenerate tooling silently misses two tables.
- Resolution: Change both references to 'import all 10 intake model classes and append all 10 names to __all__'.

**6. [MAJOR] Backend spec §2.1 OPEN_STATUSES vs §3.3 pool balancer and route 17 my-work**
- Problem: OPEN_STATUSES includes 'approved', but approved is also TERMINAL (SLA stopped, stage complete, T4 done). Since approved requests only leave via nothing (there is no approved→closed transition except a T10 drag), every approved request counts against pool member capacity forever: _pick_from_pool's open_counts inflate monotonically, members hit fake capacity, the balancer overflows or returns None, and my-work fills with finished items. The whole team-capacity feature degrades within weeks of use.
- Resolution: Drop 'approved' from OPEN_STATUSES (open = {awaiting_triage, in_review, escalated}); if approved-but-undelivered work should count, key capacity on work_status != 'delivered' instead — but pick one and make TERMINAL and OPEN disjoint.

**7. [MAJOR] Backend spec §2.1 TERMINAL_STATUSES vs §2.3 T5**
- Problem: 'rejected' is in TERMINAL_STATUSES (SLA clock stops, closed_ts stamped) yet T5 says the request 'stays open for manual handling'. A rejected request is invisible to the SLA sweep, my-work, and pool counts (∉ OPEN), has no SLA clock, and has no outbound transition in the table except a T10 stage drag — a dead-end state where 'manual handling' has no mechanics and no deadline. The rejected-then-forgotten request is the classic intake failure mode this module exists to prevent.
- Resolution: Make T5 non-terminal: rejected recommendation → request returns to status in_review (or awaiting_triage) with stage triage, SLA clock still running; reserve 'rejected' as a request status only if a whole request can be refused outright (then give it an explicit reopen transition). At minimum remove 'rejected' from TERMINAL_STATUSES so the SLA keeps pressure on the manual queue.

**8. [MAJOR] Backend spec §4.2 build_sla_legs (closed_ts = triaged_at or updated_at)**
- Problem: updated_at is TimestampMixin onupdate=utcnow — it moves on every touch. A terminal request that is later promoted (T11 sets contract_id), paused/unpaused bookkeeping, or edited in any way gets its closed_ts silently shifted forward, retroactively rewriting the SLA legs, breach flag, and breach attribution ('evidence' that changes after the fact, on a surface marketed as tamper-evident). Untriaged closes (T6 manual_close never sets triaged_at? — actually T6 does set triage stamps, but T10 drag-close does not) fall to updated_at immediately.
- Resolution: Persist an explicit closed_at TIMESTAMPTZ on intake_request, stamped once inside _transition when status enters TERMINAL_STATUSES, and use it (never updated_at) in build_sla_legs and the ops aggregates.

**9. [MAJOR] Backend spec §5.3 approve seam step 3 (authority-shim)**
- Problem: The shim 'authority-shim(value=None, contract_type=request_type.key, jurisdiction=None)' doesn't match what _grant_covers actually reads (verified authority/service.py:74-110): contract.value_amount, contract.currency, contract.contract_type, contract.jurisdiction, contract.risk_band, contract.risk_level. An object exposing 'value' instead of 'value_amount' and missing currency/risk_band/risk_level raises AttributeError at the approve seam the moment an org defines its first intake:approve grant — i.e., the gate crashes exactly when it stops being dormant. Also note contract_type=request_type.key gives keys like 'litigation-noncourt' while grants store human contract types; the allow-list comparison is case-insensitive string match, so grant authors must use the type keys — worth stating.
- Resolution: Spec the shim as a small dataclass with all six attributes: value_amount=None, currency=None, contract_type=request_type.key if request.request_type_id else request.type_label, jurisdiction=None, risk_band=None, risk_level=None; document that intake:approve grants should use max_value=None and allowed_contract_types matched against intake type keys.

**10. [MAJOR] Frontend spec §3.1 lib/types.ts additions (entire block) vs Backend spec §1/§2 stored values**
- Problem: The convention is 'types.ts mirrors backend response schemas exactly', but nearly every enum literal disagrees with what the backend stores: sla_status 'On Track'|'At Risk'|'Overdue' vs on_track/at_risk/overdue; status values like 'Completed', 'Snoozed', 'Triage — Rejected by Attorney' vs awaiting_triage/in_review/approved/rejected/escalated/closed (snooze isn't a status at all — it's triage_action='snoozed'); triaged_action 'manual-close'/'edited-approved' (hyphens) vs manual_close/edited_approved; agent_outcome 'no-match' vs no_match; AgentRecommendation.status 'PENDING'|'APPROVED'... vs lowercase, plus fields precedent_links/alternative_tone/mock that the backend names citations/short_form_reply/degraded; HandoffEvent.actor_type 'USER'|'AGENT' vs lowercase user|agent|system (system missing); source union includes 'slack'|'teams' which §1.1 doesn't allow; matter_id vs the backend's contract_id/project_id. Cockpit queue logic (status !== 'Snoozed') and statusTone/badge maps are all keyed off these phantom values.
- Resolution: Rewrite §3.1 from the backend serializers: snake_case enum literals throughout, snoozed detected via triaged_action, rename to citations/short_form_reply/degraded/contract_id/project_id, add 'system' to actor_type. Presentation labels ('At Risk', 'Completed') belong in shared.ts label maps, not in the wire types.

**11. [MAJOR] Frontend spec §1 tab model ('routing visible read-only to staff') vs Backend spec §6 route 31**
- Problem: GET /intake/routing-rules is gated `_MANAGE` (admin_panel:access), so the staff-visible read-only Routing tab 403s for every legal_reviewer — an ErrorState where the spec promises a read-only table. The SLA dashboard's rule-effectiveness list is fine (it comes from sla-ops, intake:triage), which makes the inconsistency easy to miss.
- Resolution: Change route 31 to require_permission('intake:triage') (reads), keeping POST/PATCH/DELETE (32-34) on _MANAGE — matching how the frontend already splits visibility (staff) from mutation (isAdmin).

**12. [MAJOR] Frontend spec §6.3 humanizeEvent map vs Backend spec §5.5 audit actions**
- Problem: The humanize keys don't match the audit actions the backend writes: FE has intake.ticket.created / intake.ticket.assigned / intake.ticket.handoff / intake.ticket.stage_advanced / intake.ticket.sla_breached / intake.ticket.auto_escalated / intake.ticket.closed / intake.ticket.agent_no_match / intake.recommendation.approval_blocked / intake.matter.spawned; backend writes intake.created / intake.assigned / intake.handoff / intake.stage_advanced / intake.sla_breached / intake.auto_escalated / intake.closed / intake.agent_no_match / intake.approval_blocked / intake.promoted. Zero keys match — every timeline row and My Requests latest_event falls to the title-case fallback, silently gutting the humanized feed.
- Resolution: Rekey the map to the §5.5 action strings verbatim (intake.created, intake.handoff, intake.promoted, intake.approval_blocked, ...); add intake.paused/intake.resumed/intake.routing_rule.fired which the backend writes but the map misses.

**13. [MAJOR] Frontend spec §4.7 Kanban + §3.4 STAGE_STATUS vs Backend spec §2.2 custom stages**
- Problem: The board has exactly 5 columns (default spine) and STAGE_STATUS only maps new/triage/assigned/review/complete — but request types define custom mid-stages (the seed itself creates 'review/redline/negotiation' and 'conflicts-check/assessment/response'). Requests sitting in 'redline' or 'conflicts-check' match no column and vanish from the kanban; dragging them is impossible and the optimistic status map has no entry. With the shipped seed data the board is wrong on day one.
- Resolution: Bucket by position, not name: server already knows stages(request) — map any custom mid-stage into the 'In Review' (or a generic 'In Progress') column and disable cross-column drag for custom-stage requests (their stage advances happen on the detail page's chip rail per §2.2). Derive the optimistic status from the T10 rule (mid-stage → in_review) instead of a name-keyed map.

**14. [MAJOR] Backend spec §2.3 T2/T3 from-state + §3.2 escalate action + §4.4 sweep scan filter**
- Problem: State-machine hole around 'escalated': a routing rule with escalate_to_user_id fires during create_request, setting status=escalated before the triage job runs — but T2/T3 only transition from awaiting_triage, so the agent pipeline's behavior for an escalated-at-birth request is undefined (does it flip an escalated Critical request back to in_review? skip it?). Separately, the sweep scans only status IN ('awaiting_triage','in_review'), so rule-escalated requests never get intake.sla_breached audit rows even when they blow their SLA — breaches_7d in §4.5 undercounts precisely the highest-severity intake.
- Resolution: Define T2/T3 from-state as 'any non-terminal, untriaged status' with the rule: agent processing never downgrades status (matched on an escalated request keeps status=escalated, still writes the recommendation + batons). In the sweep, scan escalated rows too for the breach tier (they can't be 'escalated more', but the sla_breached audit/timeline row must still fire once on the sla_status edge).

**15. [MAJOR] Backend spec §3.3 _pick_from_pool + §1.5 overflow_team_id**
- Problem: Deadlock: only self-overflow is rejected, so A.overflow=B and B.overflow=A is a legal config (the visited set only breaks infinite recursion, not lock ordering). Two concurrent create_request calls routing to A and B respectively lock member rows A-then-B and B-then-A with FOR UPDATE inside single long transactions (held until the request's one commit) — a textbook Postgres deadlock; one txn dies with a 40P01 that surfaces as a 500 on request submission.
- Resolution: Acquire locks in a deterministic global order: before selecting members, resolve the overflow chain's team ids, sort them, and lock all their member rows in one query ordered by team_id, member id — or simpler, reject overflow cycles (not just self-loops) at team save time by walking the chain, and add `# ponytail: lock order by sorted team_id` on the pick.

**16. [MAJOR] Frontend spec §4.8 runBreachScan + §3.2 (POST /intake/sla-scan) — missing backend endpoint**
- Problem: The SLA dashboard's 'Run breach scan' button posts to /intake/sla-scan expecting {escalated: N}, but the backend spec's §6 table (40 routes) has no such endpoint and §4.4 only defines the beat task. The button 404s. Same class of gap: the header's 'Agent settings' Modal (§1) has no backing surface at all — agents are code-config (§5.1), there is nothing to read or write.
- Resolution: Add route 41: POST /intake/sla-scan, _MANAGE, thin wrapper that runs the check_intake_slas sweep body synchronously and returns its counter dict (reuse the same function — do not fork the logic). Cut the Agent settings modal from the frontend spec, or reduce it to a read-only list rendered from a tiny GET /intake/agents returning the AGENTS registry metadata.

**17. [MAJOR] Frontend spec §4.6 Self-Service (static kb.ts) vs Backend spec §1.10 + §6 routes 35-38**
- Problem: The backend builds a tenant-editable intake_kb_article table with GET /intake/kb open to intake:read expressly so KB content is admin-editable and citable — then the frontend ships a static article module (src/components/intake/kb.ts). Admin edits via routes 36-38 never reach the self-service portal, and the articles the FAQ agent cites (source_refs from the DB) can diverge from what users read. Two sources of truth for the same content.
- Resolution: Delete kb.ts; Self-Service fetches ["intake-kb"] via a new intakeApi.listKb → GET /intake/kb, deriving category chips from tags. The article count StatCard comes from the same query.

**18. [MAJOR] Frontend spec §3.1 SlaOpsSummary vs Backend spec §4.5 get_sla_ops_summary**
- Problem: The two shapes share almost nothing: backend returns {generated_at, open_total, on_track, at_risk, overdue, paused, avg_elapsed_pct, breaches_7d, by_holder, oldest_open}; frontend expects {open, awaiting_triage, escalated, overdue, at_risk, workload[], rule_effectiveness[]}. The SLA dashboard's attorney-workload mini-table and rule-effectiveness list have no backend source anywhere (rule counters exist on routing-rules, but that endpoint is admin-only per route 31).
- Resolution: Make §4.5 the union: add awaiting_triage, escalated, workload (group open requests by assigned_to_user_id, resolve names) and rule_effectiveness (top 5 rules by times_fired — a cheap org-filtered query) to get_sla_ops_summary, and align the frontend interface field-for-field with it.

**19. [MAJOR] Backend spec §6 RBAC block (member gets intake:read) + route 2 GET /intake/requests**
- Problem: intake:read gates the full org-wide request list, detail, timeline, SLA legs and KB — and members (all employees, the submitter population) get it by default. Any employee can enumerate every intake request including harassment/discrimination complaints (policy_qa_agent's explicit sensitive branch), litigation strategy and sanctions escalations, with descriptions and AI drafts. The requester portal need is already covered by /requests/mine, which only needs authentication + ownership. This is the module's biggest data-exposure hole and contradicts the ethical-walls posture of the rest of the CLM.
- Resolution: Split the permission: keep intake:read for staff-wide list/detail (grant to legal_reviewer + approver only) and have /requests/mine + POST /requests + copilot + GET /intake/kb require only intake:create (members). get_request additionally allows requester_user_id == actor.id so a submitter can open their own ticket detail (read-only subset, as frontend §4.13 already assumes).

**20. [MINOR] Backend spec §5.2 SkillSpec intake_copilot_turn (execution_mode="job")**
- Problem: The spec registers the copilot skill with execution_mode='job' but §5.2's own wiring runs it synchronously via run_structured_skill with no JobRun ever created — the registry metadata misdescribes the skill (and anything keying off execution_mode, e.g. ops dashboards, will lie).
- Resolution: Use the mode that matches the sync path (the registry already has non-job modes; 'assistant_tool' or a documented sync convention) — or run it as a job like everything else. One line either way; just make the registry truthful.

**21. [MINOR] Backend spec §1.1 requester_user_id ('ix') vs §1.11 index list**
- Problem: §1.1 promises an index on requester_user_id, but the §1.11 migration's exhaustive index list has no ix_intake_request_org_requester — so /intake/requests/mine (every requester's portal load) sequential-scans. Since migrations are raw SQL, a model-side index=True does nothing without the CREATE INDEX statement.
- Resolution: Add `CREATE INDEX IF NOT EXISTS ix_intake_request_org_requester ON intake_request(org_id, requester_user_id)` to step 12 and its DROP to downgrade().

**22. [MINOR] Backend spec §4.3 pause semantics (POST /intake/requests/{id}/pause)**
- Problem: Not idempotent at either edge: pause while already paused overwrites paused_at, discarding the live pause accrued so far (breach_ts snaps earlier — an SLA breach can be manufactured or hidden by double-clicking the pause button); resume while not paused does `paused_ms_total += now - paused_at` with paused_at NULL → TypeError/500.
- Resolution: Guard both: {paused:true} when paused_at is set → 409 or no-op; {paused:false} when paused_at is NULL → no-op. Also spec whether pause is legal on terminal statuses (it shouldn't be — route through the same closed-is-immutable check as _transition).

**23. [MINOR] Backend spec §4.4 sweep tiers (at-risk requires sla_status == 'on_track')**
- Problem: sla_status never moves down: if a routing rule or PATCH extends sla_hours after a request was marked at_risk (or a long pause pushes pct back under 0.7), the stored column stays at_risk/overdue forever. The UI recomputes live pct so badges are right, but §4.5's oldest_open/counts and anything else reading the column disagree with the display — and a request whose SLA was extended past a prior 'overdue' stamp remains status=escalated with no path back.
- Resolution: Have the sweep also downgrade: recompute posture for every scanned row and write on any edge (overdue→at_risk→on_track), not just upward; or declare the column write-once and make §4.5 compute everything (it already computes pct — just stop persisting sla_status except for the escalation trigger).

**24. [MINOR] Backend spec §2.3 T4 / §6 route 7 bulk-triage — approve with no recommendation**
- Problem: T4's side-effects assume a recommendation + confirmation exist (flip rec, confirm_confirmation), but no-match requests (T3) have neither, and T11 explicitly contemplates 'no recommendation exists (ungated)'. record_triage_action(action='approved') on a no-match request is undefined — bulk-approve over a mixed selection will hit it immediately.
- Resolution: Spec the branch: when no pending recommendation exists, approved/edited_approved skip steps 4-5 (no confirmation to confirm), still stamp triaged_* and audit intake.recommendation-less approve as plain intake.approved (or reject the action 422 'no draft to approve — use manual_close'). Pick one; the frontend's 'a' shortcut already guards on agent_recommendation, so 422 is the smaller surface.

**25. [MINOR] Backend spec §6 route 18 (GET /intake/assignees, intake:triage) vs Frontend §4.17 HandoffDialog**
- Problem: HandoffDialog is reachable from any intake:update surface (route 8 handoff needs only intake:update), but its assignee Select loads route 18 which demands intake:triage. Under the default roles the sets coincide, but any custom role with update-without-triage gets a broken picker (silent 403 → empty Select) on a dialog it's allowed to submit.
- Resolution: Gate route 18 on intake:update (it leaks only id/name/email of org users, which the reassign and handoff flows both legitimately need).

**26. [MINOR] Frontend spec §4.16 TimelinePanel + §3.1 IntakeTimelineEvent (chain_position, hash_prefix) vs Backend spec route 11**
- Problem: The 'CHAIN-SEALED · TAMPER-EVIDENT' banner covers a feed that backend route 11 assembles from BOTH hash-chained audit rows and plain resource_timeline_event rows — the latter have no hash and are pruned after 365d. chain_position isn't a stored column anywhere, and the backend spec never defines the serialization that would supply chain_position/hash_prefix. As written the panel either 500s on missing fields or fabricates tamper-evidence for prunable rows.
- Resolution: Serve the timeline from audit rows only for this panel (they're never pruned and carry row_hash — hash_prefix = row_hash[:8], chain_position = row index within the query, explicitly labeled as per-resource ordinal not global chain position), or keep the mixed feed and show the hash/badge only on rows that have row_hash, with the banner softened to 'audit-backed entries are chain-sealed'.

**27. [MINOR] Backend spec §7 seed item 5 (backdated −30h request) + item 5's pending-recommendation NDAs**
- Problem: If the −30h backdated request is one that carries a seeded PENDING recommendation, its confirmation (expires_at = submitted_at + 24h under §5.3) is born expired and the very first thing a demo user tries — approving the showcase draft — 409s. The spec doesn't say which request gets backdated. (Moot if the expiry finding above is fixed by removing SLA-linked expiry, but the seed should be unambiguous either way.)
- Resolution: Pin it: the backdated request is the no-match general question (queue-held, no recommendation), and add a seed assertion that every seeded PENDING confirmation is unexpired.

**28. [MINOR] Frontend spec §1 permission helper (can = permissions.includes(p))**
- Problem: app/core/rbac.py has_permission supports a '*' wildcard (rbac.py:110-112); a custom role granted '*' passes every backend check but fails every frontend includes() check — the user can call all 40 endpoints yet sees only the requester tabs.
- Resolution: One line: `const can = (p) => !!user?.permissions?.some(x => x === p || x === "*")` (and use the same helper if other pages share the pattern).
