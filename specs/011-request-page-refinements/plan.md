# Implementation Plan: Request page refinements (workflow card, real activity, counterparty files)

Feature ID: 011-request-page-refinements
Spec: ./spec.md (APPROVED)
Created: 2026-10-23
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

Three changes to the shipped 010 request page (`frontend/src/app/(app)/intake/_request-overview.tsx` and its
presentational siblings), plus two new **read-only** backend endpoints in the `intake` domain:

```
GET /intake/requests/{id}/activity      ──► app/intake/activity.py      (NEW)  timeline events of the request
                                                                                + its contract (if the viewer may
                                                                                see it) + hand-offs + workflow
                                                                                comments → plain sentences
GET /intake/requests/{id}/counterparty  ──► app/intake/screening.py     (CHANGED) per-viewer visible set:
                                                                                contracts + earlier requests +
                                                                                counts + note, computed at read time
GET /intake/requests/{id}  (existing)   ──► service.serialize_request   (CHANGED) never serves the stored
                                                                                org-wide relationship dossier
```

- Backend mirrors the existing intake shape: thin handlers in `app/intake/routes.py` (same `_CREATE` permission +
  `intake_service.get_request` row gate as `/handoffs` and `/approval-summary`), logic in a sibling module, Pydantic
  response models in `app/intake/schemas.py`. Contract visibility reuses `app/contracts/access.py`
  (`accessible_contract_filter`, `sealed_off_filter`, `is_sealed_off`, `user_can_access_contract`) and request
  visibility reuses `app/intake/service.py::_accessible` — no new access rules are invented.
- Frontend: the 010 `StepsCard` (side column) is replaced in place by a `WorkflowCard` (main column, tile row) in
  `_request-steps.tsx`; `ActivityCard` and `CounterpartyCard` in `_request-cards.tsx` are rewritten against the two
  new endpoints; `activityTrail` (client-side merge) is deleted. `_request-overview.tsx` is rewired (column order,
  queries, send-back open state). All CSS stays in the existing `.ro` scope strings with palette variables.
- No DB migration, no new table, no Celery task, no setting, no integration, no new audit/timeline event.

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | none (no schema change — see "Database schema") |
| backend-dev | `backend/app/intake/activity.py` (NEW), `backend/app/intake/screening.py`, `backend/app/intake/service.py` (only `serialize_request`'s `"screening"` line + new `_public_screening` helper), `backend/app/intake/routes.py` (two GET handlers), `backend/app/intake/schemas.py` (response models), `backend/tests/test_request_activity.py` (NEW), `backend/tests/test_request_counterparty.py` (NEW), `backend/tests/test_intake_every_party.py` (one assertion) |
| frontend-dev | `frontend/src/lib/types.ts`, `frontend/src/lib/endpoints.ts` (intakeApi additions), `frontend/src/components/lifecycle-view.tsx` (extract `stepFacts`, behaviour-preserving), `frontend/src/lib/workflow-steps.ts` + `.test.ts` (`overdueWords`), `frontend/src/lib/request-facts.ts` + `.test.ts` (delete `activityTrail`, add `groupActivityByDay`, `counterpartySummary`), `frontend/src/app/(app)/intake/_request-steps.tsx` + `_request-steps.test.tsx`, `_request-cards.tsx` + `_request-cards.test.tsx`, `_request-overview.tsx` + `_request-overview.test.tsx`, `frontend/src/app/(app)/intake/page.tsx` (two props on `<RequestOverview>`), `frontend/src/components/lifecycle-view.test.ts` / `lifecycle-view.render.test.tsx` (only if a `stepFacts` case is added there) |
| qa-engineer | `specs/011-request-page-refinements/verification.md` |

Shared-hotspot note:
- `src/lib/types.ts`, `src/lib/endpoints.ts`: T002 only.
- `backend/app/intake/routes.py`, `backend/app/intake/schemas.py`: T001 then T003 (serialized).
- `src/components/lifecycle-view.tsx`: T004 only. `_request-overview.tsx` + its test and `page.tsx`: T006 only.
- `app/models.py`, `app/jobs/tasks.py`, `src/components/ui.tsx`: untouched.

## Interface freeze (contract-first — fixed before implementation starts)

### API contract

Both paths are under the API prefix (`/api/v1`). Both are reads: **no audit row, no timeline event** (AC-28).

| Method | Path | Permission | Request | Response (200) | Errors |
|---|---|---|---|---|---|
| GET | `/intake/requests/{request_id}/activity` | `intake:create` + row gate `IntakeService.get_request` (staff = `intake:read`/admin see the org's requests; anyone else only requests they filed) | none (no query params) | `RequestActivityResponse` | 403 missing `intake:create`; 404 not found / other org / not the viewer's request |
| GET | `/intake/requests/{request_id}/counterparty` | `intake:create` + the same row gate | none | `RequestCounterpartyResponse` | 403; 404 as above |
| GET | `/intake/requests/{request_id}` (existing) | unchanged | unchanged | `RequestResponse`, except `screening` never contains the key `relationship` | unchanged |

Full shapes:

```json
// GET /api/v1/intake/requests/{request_id}/activity  → 200
{
  "items": [
    {
      "id": "t:6c0b…",            // opaque, stable React key; "t:<timeline id>" | "h:<handoff id>" | "c:<run id>:<n>". Never displayed.
      "at": "2026-10-14T09:09:41.123456+00:00",   // ISO-8601 with offset
      "sentence": "AI review complete · 1 issue found",  // plain language, never a code/id/JSON
      "who": "AI",                // a person's name, or "AI", "Aegis" or "Counterparty"
      "source": "contract",       // "request" | "contract"
      "tone": "ai",               // "ai" | "stage" | "done" | "plain" — the dot colour only
      "system": false             // true = hidden until "Show system details"
    }
  ],
  "truncated": false              // true when more than 500 items existed; the oldest were dropped
}
```

- Ordering: newest first by `at`; ties keep source order (timeline, then hand-offs, then comments). Cap: the newest
  **500** merged items (`truncated` says if any were dropped). Paging and the 20-item batch are client-side.
- Sources and their gates (all filtered by `org_id == user.org_id`):
  1. `resource_timeline_event` rows with `resource_type="intake_request", resource_id=request.id` — always (the row
     gate already proved the viewer may see the request).
  2. `resource_timeline_event` rows with `resource_type="contract", resource_id=request.contract_id` — only when
     `request.contract_id` is set AND the viewer has `contract:read`
     (`has_permission(org_access.effective_permission_values(db, user=user), "contract:read")`) AND the contract is
     not soft-deleted AND `not is_sealed_off(db, user=user, contract=c) and
     ContractAccessService(db).user_can_access_contract(contract=c, user=user)` (the contract page's own rule; the
     `is_sealed_off` pre-check makes it silent — `user_can_access_contract` only writes its `access.denied` audit row
     on a wall/clearance deny, which `is_sealed_off` has already excluded).
  3. `intake_handoff` rows of the request — always (same gate as `GET /handoffs` today).
  4. Comments on the latest workflow run (`get_run_for_request(...).context["comments"]`) — only when the viewer
     passes today's run read gate: `intake:read` (or admin) AND (`run.contract_id` is null OR the silent contract
     check of (2) passes for `run.contract_id`). This keeps a requester from seeing comments they cannot see today.
- 500-row timeline query: one `select … where org_id=:org and ((resource_type='intake_request' and
  resource_id=:rid) or (resource_type='contract' and resource_id=:cid)) order by created_at desc limit 501`.

```json
// GET /api/v1/intake/requests/{request_id}/counterparty  → 200
{
  "name": "Initech Software Ltd",          // primary counterparty, same pick as screening (gather_parties: first role=counterparty, else first); null when none
  "summary": { "contracts": 8, "active": 2, "requests": 7 },   // whole visible set, not the batch
  "note": "Known counterparty: 8 prior contract(s), 1 NDA on file, 7 prior request(s).",  // null when name is null
  "contracts": [
    {
      "id": "57f4…", "title": "Mutual NDA — Initech Software Ltd", "contract_type": "NDA",
      "stage": "active",                   // Contract.lifecycle_stage as stored
      "value": 1000000.0,                  // float(value_amount) or null
      "currency": "INR",                   // or null
      "created_at": "2026-10-09T08:00:00+00:00",
      "ended": false                       // closed stage, archived, or expiration_date < today
    }
  ],
  "requests": [
    { "id": "88a5…", "ref": "REQ-7701", "type_label": "NDA", "status": "open",
      "date": "2026-10-01T10:00:00+00:00" }    // submitted_at, else created_at; null if neither
  ]
}
```

- `name: null` ⇒ `summary` all zero, `note: null`, both lists empty (card omitted, FR-27).
- **Visible contracts** = `select(Contract).where(accessible_contract_filter(user), Contract.deleted_at.is_(None),
  Contract.counterparty_name.is_not(None), Contract.id != request.contract_id (when set), or_(*[Contract.counterparty_name.ilike(f"%{t}%") for t in tokens]))`
  then the Python `_name_match(name, c.counterparty_name)` exactly as today. `tokens` = `normalize_party_name(name).split()`;
  empty ⇒ no query, no contracts. Empty list (and zero counts) when the viewer lacks `contract:read`, so a listed
  contract never opens to a refusal (FR-26). `accessible_contract_filter` is org + walls + clearance + owner/creator/
  grant/admin — a strict subset of the contract page's row check, so every listed contract opens.
- **Visible earlier requests** = `select(IntakeRequest).where(_accessible(user), IntakeRequest.id != request.id,
  or_(IntakeRequest.contract_id.is_(None), IntakeRequest.contract_id.in_(select(Contract.id).where(Contract.org_id == user.org_id, sealed_off_filter(user)))))`
  then Python `any(_name_match(name, n) for n in request_party_names(rq))` exactly as today. `_accessible` is the
  same predicate `get_request` enforces, so every listed request opens (FR-26); `sealed_off_filter` drops a request
  tied to a contract the viewer is walled/cleared off from (FR-25, AC-24).
- **Counts**: `contracts` = len(visible contracts); `active` = count with `stage == "active" and not ended`;
  `requests` = len(visible requests). `ended` = `stage == "closed" or archived or (expiration_date and expiration_date < date.today())`.
  NDA count (for the note only) = today's rule (`"nda"` or `"non-disclosure"` in lower(title)).
- **Note** (same wording as today, minus the value, from the visible set only):
  - empty visible set: `"New counterparty. No other contracts or requests on file."`
  - else `"Known counterparty: " + ", ".join(parts) + "."` with parts, each only when > 0:
    `"{n} prior contract(s)"`, `"{n} NDA on file"`, `"{n} prior request(s)"`.
  - **No total value anywhere** (see D6).
- **Ordering**: contracts by `(ended, -created_at)` (live ones first, closed/expired after, newest first within each);
  requests by date desc. Lists capped at **100** each; `summary` still counts the whole set.

Audit actions written by each mutation: none — this feature adds no mutation. `GET` handlers write nothing.

### Event mapping table (server-side, `app/intake/activity.py`, frozen)

Every `event_type` written by the code today (grep of `write_timeline_event` + `_transition(audit_action=…)` +
`f"approval.{kind}"`) and every type in the dev DB for `intake_request`/`contract`. Columns: **sentence** (`title` =
the stored title, which is code-authored prose; anything else is a fixed sentence or a rule over whitelisted
`details` keys), **who** (`person` = creator's `full_name` or `email`, or `"Aegis"` when no creator; `ai` = `"AI"`;
`aegis` = `"Aegis"`; `cp` = `"Counterparty"`), **sys** (hidden by default), **tone**.

| event_type | sentence | who | sys | tone |
|---|---|---|---|---|
| intake.created | title | person | no | plain |
| intake.triaged | "AI triage complete" | ai | no | ai |
| intake.sender.unverified | title | aegis | no | stage |
| intake.attachment.failed | title | aegis | no | stage |
| intake.contract_drafted | title | person | no | done |
| intake.contract_from_attachment | title | person | no | done |
| intake.auto_escalated | title | aegis | no | stage |
| intake.closed | "Request closed" | person | no | stage |
| intake.completed | title | aegis | no | done |
| intake.stage_advanced | "Request moved to its next stage" | person | no | stage |
| intake.submitted_for_approval | "Sent for approval" | person | no | stage |
| intake.approved | title | person | no | done |
| intake.approval_rejected | title | person | no | stage |
| intake.approval_gate_passed | title | aegis | no | done |
| intake.promoted | title | person | no | plain |
| intake.deliverables_changed | title | person | no | plain |
| intake.answers_changed | title | person | no | plain |
| flow.started | title | person | no | plain |
| flow.step_completed | title | person | no | done |
| flow.completed | title | aegis | no | done |
| flow.failed | title | aegis | no | stage |
| flow.cancelled | title | aegis | no | stage |
| flow.returned | title (e.g. Returned to “Legal approval”) | person | no | stage |
| flow.notify | title | aegis | no | plain |
| flow.draft_skipped | title | aegis | no | plain |
| flow.review_retried | "AI review restarted" | person | no | ai |
| approval.decided | title rewritten: `^Approval (approve|reject)d \(step (\d+)\)$` → "Approved (step N)" / "Rejected (step N)"; else title | person | no | done if approved else stage |
| approval.partial | title | person | no | done |
| approval.delegate | title | person | no | plain |
| approval.escalate | title | person | no | plain |
| ai.skill_started | "AI started: {Skill}" | ai | yes | ai |
| ai.skill_succeeded | "AI finished: {Skill}" | ai | yes | ai |
| ai.skill_failed | "AI could not finish: {Skill}" | ai | yes | ai |
| contract.uploaded | "Contract document stored" | person | yes | plain |
| contract.text_extracted | "Document text read" | aegis | yes | plain |
| contract.ai_jobs_dispatched | "AI analysis queued" | aegis | yes | ai |
| contract.ai_metadata_extracted | "AI read the contract's key details" | ai | yes | ai |
| contract.brain_ingested | "Contract Brain updated" | aegis | yes | plain |
| contract.drafted_from_template | "Drafted from our template" | aegis | yes | plain |
| contract.auto_review | title | ai | yes | ai |
| contract.renewal_extracted | "AI read the renewal terms" | ai | yes | ai |
| contract.obligations_extracted | "AI found {n} obligation(s)" (`details.obligation_count` int) else "AI read the obligations" | ai | yes | ai |
| playbook.redline_created | "AI marked its suggested changes in the document" | ai | yes | ai |
| assistant.tracked_change_created | "AI prepared a tracked-change version" | ai | yes | ai |
| assistant.contract_version_replicated | "AI copied a version of the contract" | ai | yes | ai |
| renewal.dates_recomputed | title | aegis | yes | plain |
| contract.lifecycle_changed | "Stage changed to {Label}" when `details.to_stage` ∈ {intake, drafting, review, approval, signature, active, closed} (Label = capitalised), else title | person | no | stage |
| playbook.run_completed | `details.status` present and ≠ "succeeded" → "AI review did not finish"; int `details.deviation_count` → "AI review complete · {n} issue(s) found" (0 → "AI review complete · no issues found"); else "AI review complete" | ai | no | ai |
| playbook.deviation_decided | "Decided a review issue" | person | no | done |
| contract.issue_fixes_drafted | title | ai | no | ai |
| contract.auto_review_retried | "AI review restarted" | person | no | ai |
| assistant.contract_redrafted | "AI redrafted the contract into a new version" | ai | no | ai |
| assistant.contract_generated | "AI generated the contract" | ai | no | ai |
| contract.edit_accepted | "Accepted a suggested change" | person | no | done |
| contract.edit_rejected | "Rejected a suggested change" | person | no | plain |
| contract.redline_applied | title | person | no | done |
| contract.redline_not_applied | title | aegis | no | plain |
| contract.manual_edit_proposed | "Proposed a change by hand" | person | no | plain |
| contract.text_manually_edited | "Edited the document by hand" | person | no | plain |
| contract.edited_in_word | title | person | no | plain |
| contract.version_uploaded | "New version uploaded" | person | no | plain |
| contract.version_restored | "Restored an earlier version" | person | no | plain |
| contract.counterparty_revision | title | person | no | plain |
| contract.revision_countered | title | person | no | plain |
| contract.revision_agreed | title | person | no | done |
| contract.negotiation_revision | title | person | no | plain |
| contract.share_created | "Shared the contract" | person | no | plain |
| contract.changed_by_request | title | person | no | plain |
| contract.metadata_updated | "Contract details updated" | person | no | plain |
| contract.risk_recomputed | "Risk recalculated: {band}" when `details.band` ∈ {low, medium, high, critical} else "Risk recalculated" | person | no | ai |
| workflow.counterparty_sent | title | person | no | plain |
| workflow.counterparty_submitted | title | cp | no | plain |
| signature.completed | title | aegis | no | done |
| renewal.decided | "Renewal decision: {decision}" (`details.decision` with `_`→space) else "Renewal decision recorded" | person | no | done |
| renewal.dates_confirmed | title | person | no | done |
| **any other type** (fallback) | "Request updated" (source request) / "Contract updated" (source contract) | person | **yes** | plain |

- `{Skill}` = `details.skill_name`, else the title's text after `": "` up to `" ("`; `_` → space, first letter
  upper-cased; if that still fails the guard below → "a task".
- **Guard (applied to every final sentence):** a sentence is "raw" when it is empty, equals the event_type, fully
  matches `[a-z0-9_]+(\.[a-z0-9_]+)+`, contains a UUID (`[0-9a-f]{8}-[0-9a-f]{4}-`), or starts with `{` or `[`.
  A raw sentence is replaced by the row's fixed sentence if the table has one, else by the fallback sentence.
  (Catches `_transition`'s `title or audit_action` and any future f-string that prints a code.)
- Sentences are cut at 240 characters with "…".
- **Hand-offs** (source request, sys no): `to_holder` human → "Handed to {to_label or 'a person'}"; agent → "Handed
  to the AI agent"; queue → "Returned to the queue"; else "Handed on"; plus `": {reason}"` when a reason exists.
  who = creator's name when `actor_type == "user"`, else "Aegis". tone plain.
- **Comments** (source request, sys no): kind `comment` → `Commented: “{text}”`; `return` → `Sent back for rework:
  “{text}”` (tone stage); `reply` → `Replied to the rejection: “{text}”`; text cut at 200 chars. who =
  `actor_name` (stored), "Aegis" when it is "system". tone plain unless stated.
- New event types added later fall into the fallback: system, plain wording, never a code (risk R2).

### Database schema

No change. No new table, column or index; no migration. (Existing indexes on
`resource_timeline_event.resource_id/resource_type/org_id`, `contract.counterparty_name/org_id`, and
`intake_request.org_id` serve both reads.) The stored `intake_request.screening` JSON keeps its shape for old rows;
new screening runs simply stop writing the `relationship` key, and the API never serves it (D5). Current head stays
`0074_template_sync`.

### Backend Pydantic models (added to `app/intake/schemas.py`)

```python
class ActivityItem(BaseModel):
    id: str
    at: datetime
    sentence: str
    who: str
    source: Literal["request", "contract"]
    tone: Literal["ai", "stage", "done", "plain"]
    system: bool

class RequestActivityResponse(BaseModel):
    items: list[ActivityItem]
    truncated: bool

class CounterpartySummary(BaseModel):
    contracts: int
    active: int
    requests: int

class CounterpartyContractItem(BaseModel):
    id: str
    title: str
    contract_type: str | None
    stage: str
    value: float | None
    currency: str | None
    created_at: datetime
    ended: bool

class CounterpartyRequestItem(BaseModel):
    id: str
    ref: str
    type_label: str
    status: str
    date: datetime | None

class RequestCounterpartyResponse(BaseModel):
    name: str | None
    summary: CounterpartySummary
    note: str | None
    contracts: list[CounterpartyContractItem]
    requests: list[CounterpartyRequestItem]
```

### Frontend TypeScript models (added to src/lib/types.ts)

```ts
// GET /intake/requests/{id}/activity — the request's and its contract's history, already in plain words.
export interface RequestActivityItem {
  id: string;                 // opaque React key; never displayed
  at: string;                 // ISO-8601 with offset
  sentence: string;
  who: string;                // a person's name, "AI", "Aegis" or "Counterparty"
  source: "request" | "contract";
  tone: "ai" | "stage" | "done" | "plain";
  system: boolean;            // hidden until "Show system details"
}
export interface RequestActivity {
  items: RequestActivityItem[];   // newest first, at most 500
  truncated: boolean;
}

// GET /intake/requests/{id}/counterparty — only what this viewer may see.
export interface CounterpartyContractItem {
  id: ID;
  title: string;
  contract_type: string | null;
  stage: string;              // Contract.lifecycle_stage
  value: number | null;
  currency: string | null;
  created_at: string;
  ended: boolean;             // closed, archived or past its expiry date
}
export interface CounterpartyRequestItem {
  id: ID;
  ref: string;
  type_label: string;
  status: string;             // IntakeStatus value
  date: string | null;
}
export interface RequestCounterparty {
  name: string | null;
  summary: { contracts: number; active: number; requests: number };
  note: string | null;
  contracts: CounterpartyContractItem[];   // live first, then ended; newest first; at most 100
  requests: CounterpartyRequestItem[];     // newest first; at most 100
}
```

`IntakeRequest.screening` keeps its type (`Record<string, unknown> | null`); the page stops reading
`screening.relationship`.

### Endpoint client additions (src/lib/endpoints.ts)

- `intakeApi.activity(id: string) → Promise<RequestActivity>` — `GET /intake/requests/${id}/activity`
- `intakeApi.counterparty(id: string) → Promise<RequestCounterparty>` — `GET /intake/requests/${id}/counterparty`

(placed after `handoffs` in `intakeApi`; `handoffs` stays — other screens may use it.)

### Frozen helper contracts (frontend)

```ts
// src/components/lifecycle-view.tsx — extracted from NextStepCard, which then renders from it (output unchanged).
export interface StepFacts {
  who: string | null;          // live approval's approver; else assignee (+ " (team)"); else "{team} team"; else
                               // "the signers" for a signature step; else today's `who(cur)` text; else null
  members: string[];           // active team members, only when it waits on a team with no assignee/live approval
  authorityNote: string | null;
  due: Date | null;            // live approval due_at, else updated_at + sla_hours
  overdue: boolean;            // live approval's flag, else due < now
}
export function stepFacts(step: WorkflowRunStep, teams?: IntakeTeam[], now?: number): StepFacts;

// src/lib/workflow-steps.ts
export function overdueWords(due: Date, now?: number): string;
// ≥ 1 whole day late → "Overdue N days" ("Overdue 1 day"); less → "Overdue".

// src/lib/request-facts.ts  (activityTrail + TrailItem are DELETED)
export function groupActivityByDay(items: RequestActivityItem[], now?: number): { day: string; items: RequestActivityItem[] }[];
// keeps the given (newest-first) order; day label = local date "14 Oct", with the year when not the current year.
export function counterpartySummary(s: RequestCounterparty["summary"]): string | null;
// parts only when > 0: "N other contract(s)" · "N active" · "N earlier request(s)"; joined " · "; all zero → null.
```

### Frozen component props

```ts
// _request-steps.tsx — StepsCard is REPLACED by WorkflowCard. ApprovalChain, Toggle, FlowLine are kept.
export interface StepRow {
  label: string;
  tone: "done" | "cur" | "warn" | "todo";
  node: string;                               // "✓" | "–" | "!" | "N"
  badge?: string; badgeTone: "done" | "cur" | "warn" | "skip";
  skipped?: boolean;
  sidx?: number;
  stage?: LifecycleStage | null;              // caption on the first tile of each stage
  assign?: string;                            // done tiles: who did it
  wait?: { who: string | null; extra: string | null; due: string | null; overdue: boolean } | null;  // open tiles
  focus?: boolean;                            // the step the check action and doorway sit on
  next?: boolean;                             // first upcoming tile → "Next"
  detail: [string, string][];                 // readable result → "Review result" panel
  doorway?: { href: string; label: "Open the draft" | "Review & redline" | "Open to negotiate" };
  notice?: ReactNode;                         // StepReviewNotice, rendered under the tile row
}
export type FlowState = /* unchanged from 010 */;
export interface ExtraDetail { id: string; label: string; node: ReactNode }   // stepIdx removed
export function WorkflowCard(p: {
  rows: StepRow[];
  countText: string | null;                   // same wording as 010 ("3 of 6 steps done", ", 2 skipped")
  flow: FlowState;
  error: string | null;
  check: { label: string; onClick: () => void; busy: boolean } | null;   // drawn in the focus tile
  extras: ExtraDetail[];                      // "Approval chain", "Negotiation rounds"
  trace: WorkflowRunBriefEntry[];
  sendBack: {
    options: { value: string; label: string }[]; to: string; note: string;
    onTo: (v: string) => void; onNote: (v: string) => void; onSend: () => void; busy: boolean;
    selectRef: RefObject<HTMLSelectElement | null>;
    open: boolean; onToggle: () => void;      // NEW: parent-owned so "Send back for rework…" in the decision area can open it
  } | null;
}): JSX.Element;
export const STEPS_CSS: string;

// _request-cards.tsx
export function ActivityCard(p: {
  status: "loading" | "error" | "ready";
  data: RequestActivity | undefined;
  onRetry: () => void;
}): JSX.Element;                                // always renders a card (FR-14)
export function CounterpartyCard(p: {
  status: "loading" | "error" | "ready";
  data: RequestCounterparty | undefined;
  parties: { name: string }[];                  // r.parties — "All parties" line when > 1
  onRetry: () => void;
  onOpenRequest: (id: string) => void;
}): JSX.Element | null;
// PeopleCard, DocumentsCard, AskAegisCard, CommentsCard: unchanged.

// _request-overview.tsx
export function RequestOverview(p: { id: string; onBack: () => void; canManage: boolean; onOpenRequest?: (id: string) => void }): JSX.Element;
// page.tsx: <RequestOverview key={detailId} id={detailId} … onOpenRequest={setDetailId} />
```

## Component design

### Backend

- Models: none.
- Schemas: `app/intake/schemas.py` — the six models above.
- `app/intake/activity.py` (NEW, ~200 lines):
  - `EVENTS: dict[str, _Spec]` — the mapping table, one entry per row; `_Spec(sentence: str | Callable | None,
    who: Literal["person","ai","aegis","cp"], system: bool, tone: str)` (`None` = use title).
  - `_is_raw(sentence, event_type) -> bool` — the guard. `_sentence(row) -> str`.
  - `request_activity(db, *, user, request) -> dict` — the four sources with their gates (above), one batched
    `select(User)` for names (`User.org_id == user.org_id`), merge, sort, cap 500. Org-scoped on every query. Writes
    nothing.
  - `_contract_visible_silently(db, user, contract_id) -> bool` — `contract:read` + not deleted + `not is_sealed_off`
    + `user_can_access_contract`. Shared by sources 2 and 4.
- `app/intake/screening.py` (CHANGED):
  - `primary_counterparty(r) -> str | None` — extracted from `compute_screening` (same pick).
  - `ScreeningService.visible_counterparty(user, request) -> dict` — the counterparty response (queries above).
    Replaces `counterparty_relationship` (deleted with its module wrapper; no other caller — `grep` confirmed).
    `# ponytail:` comment on the request scan: "Python name match over the viewer's visible requests (68 rows
    today); add a normalized party-name column if a counterparty page gets slow".
  - `compute_screening` no longer returns `relationship`; `run_screening`'s best-effort audit `after` becomes
    `{"counterparty": …}` (the org-wide `prior_contracts` count is gone — D5).
- `app/intake/service.py` (CHANGED, two lines + helper): `"screening": _public_screening(r.screening)` where
  `_public_screening` returns the dict without the `relationship` key (old stored rows keep it in the DB but never
  leave the server — list, pool, my-work and get all go through `serialize_request`).
- Router `app/intake/routes.py`: two handlers, both `current_user=Depends(_CREATE)`, both
  `r = intake_service.get_request(user=current_user, request_id=request_id)` first, then the module function.
  `response_model=RequestActivityResponse` / `RequestCounterpartyResponse`.
- Celery: none. Settings: none (no `validate_runtime_settings()` entry, no phase-10 fixture change).

### Frontend (Next.js page/component tree)

- `src/app/(app)/intake/page.tsx` — `<RequestOverview key={detailId} … onOpenRequest={setDetailId} />` (key so a
  newly opened request never inherits the previous one's typed drafts).
  - `_request-overview.tsx` — data, mutations, derivations, layout (T006):
    - Main column: `DecisionArea` → `WhatWasAsked` → `RiskReview` (cid) → **`WorkflowCard`** → **`ActivityCard`** →
      `CommentsCard`. Side column: **`CounterpartyCard`** → `PeopleCard` → `DocumentsCard` → `AskAegisCard`.
    - Queries: remove `intake-handoffs` (now inside activity). Add
      `["intake-activity", id, flowRun?.status ?? null, flowRun?.current_index ?? null, cid]` →
      `intakeApi.activity(id)` with `placeholderData: keepPreviousData` (the card never blanks while the run moves) and
      `["intake-counterparty", id]` → `intakeApi.counterparty(id)`. `status` = data ? "ready" : isError ? "error" :
      "loading". `done()` and the `postComment`/`returnStep` successes invalidate `["intake-activity", id]`
      (prefix) instead of `intake-handoffs`.
    - Rows: same derivation as 010 plus `wait` for open tiles from `stepFacts(s, teams)` (`due` → `overdueWords` when
      overdue, else `Due {14 Oct, 10:00}`; `extra` = authority note, else team members joined), `focus = s.idx ===
      current?.idx`, `next` on the first `todo` after the last open step, failed badge reads `cancelled` when the run
      was cancelled. `extras` lose `stepIdx` (`id: "approval-chain"`, `"negotiation"`).
    - `const [sendBackOpen, setSendBackOpen] = useState(false)`; the decision area's `onJumpToSendBack` sets it true
      and a `useEffect` on `sendBackOpen` scrolls to and focuses `sendBackRef` once (pending-focus ref flag).
    - Remove `sc.relationship` / `CounterpartyCard` old props; `NextStepCard` import goes (data now in tiles).
    - `onOpenRequest` from the counterparty list: `p.onOpenRequest?.(rid)` then `window.scrollTo({ top: 0 })`.
  - `_request-steps.tsx` — `WorkflowCard` (T004). Layout:
    - Header: `h2 "Workflow"` + `.wfname` (flow name when running/suggested) + `.cnt` countText.
    - Body: `FlowLine` when not running (suggested + Start it / setting up / none + picker), or a one-line state when
      the run is complete/failed/cancelled; error line; tile row `.wft`; notices (`.wfnotice`, each headed by its
      step name) under the row; links row `.wflinks` of `.xtoggle` buttons in this order, each only when it applies:
      "Review result" (any row has `detail`), each `extras` label, `Agent hand-off trace (N)` (trace non-empty),
      "Send back for rework" (`sendBack` set; open state from props); then the open panels in the same order:
      Review result = one block per step with a result (`.lbl` step name + `.sdetail`); extras = their node;
      trace = `<ol class="trace">` with `plainStepName`; send back = today's `.sendback` form (same texts, same
      disabled rule and hint).
    - Tile `.t.{done|cur|warn|skip|todo}`: optional `.tstage` caption (first tile of a stage, LIFECYCLE label);
      `<b>` = "✓ Name" / "– Name" / "! Name" / "N · Name"; then done → `assign ?? "Done"`; skip → "Skipped";
      warn → `badge`; cur → `wait.who ? "With {who}" : badge`, `wait.extra` (2-line clamp, full text in the DOM),
      `wait.due` (`.over` when overdue); todo → "Next" when `next`. Focus tile also gets the check button
      (`.ministep`) and the doorway link.
  - `_request-cards.tsx` — `ActivityCard`, `CounterpartyCard` rewritten (T005):
    - Activity: header "Activity" + `.chsub` "from this request and its contract" when any item has source
      contract. Local state `showSystem=false`, `limit=20`. `visible = items.filter(i => showSystem || !i.system)`;
      render `groupActivityByDay(visible.slice(0, limit))` as `.actv .day` headings and `.ev` rows (dot `i.{tone}`,
      sentence, `.who`, `time` HH:MM). Footer `.actfoot`: "Show earlier activity" (+20) when `visible.length > limit`;
      "Show system details (N)" / "Hide system details" only when N = hidden system items > 0; when
      `truncated` and everything is shown, a dim "Older activity is not listed here." States: loading → one dim
      line "Loading activity…"; error → "Activity could not be loaded." + Retry button; ready and nothing visible →
      "No activity yet." (plus the system toggle when there are hidden items).
    - Counterparty: `null` while loading or when `data.name` is null; error → one line "Counterparty files could not
      be loaded." + Retry. Ready: h2 "Counterparty", `.cpname` name, `.cpstat` = `counterpartySummary` (omitted when
      null), "All parties: A, B" dim line when `parties.length > 1`. Section "Contracts": first 5, "Show N more"
      reveals the rest; each item a `next/link` to `/contracts/{id}` (`.cpfile`): title (wraps,
      `overflow-wrap:anywhere`), meta `.cpmeta` = stage pill (LIFECYCLE label; `ended` → plain pill) · type ·
      `fmtMoney(value, currency)` when value · `fmtDate(created_at)`. Empty but requests exist → one line "No other
      contracts on file you can see". Section "Earlier requests": same mechanics; item = `<a href="/intake?open={id}">`
      whose plain click calls `onOpenRequest(id)` (modifier/middle clicks keep the default so a new tab works);
      text "REF · type", meta status label (open → Open, escalated → Escalated, approved → Approved, closed → Closed,
      else capitalised) · `fmtDate(date)`. Empty but contracts exist → "No earlier requests you can see". Whole set
      empty → only the name and the note. The note is the last line (`.dim`).
- New shared primitives in `src/components/ui.tsx`: none. Nothing new is imported from `@/components/ui`
  (`LoadError`, `MessageBar` stay as in 010). Icons from lucide-react already imported (`ChevronDown`,
  `ChevronRight`, `Loader2`, `Undo2`, `AlertTriangle`; `User` icon no longer needed in steps).
- Demo-mode behavior: none; the request page has no demo data path (as in 010).

### Step-function inventory (FR-6 — every function, its gate, its new place; each appears once)

| Function / datum (010) | Gate (unchanged) | New place |
|---|---|---|
| "Start it" (suggested workflow) | canAct && no run && suggested flow | WorkflowCard FlowLine |
| Pick a workflow (`select[aria-label="Pick a workflow"]`) | canAct && kind none | WorkflowCard FlowLine |
| Setting up line | settingUp | WorkflowCard FlowLine |
| Workflow error line | `flowRun.error` | WorkflowCard error line |
| Check approval / signature / AI review status | `checkLabel` rule | focus tile button |
| Doorway (Open the draft / Review & redline / Open to negotiate) | canAct && current waiting_human && contract | focus tile link |
| StepReviewNotice + Retry review | interrupted review step (+ canAct for Retry) | `.wfnotice` under the tiles |
| Assignee of each step | assignee known | tile line (done: assign; open: "With …") |
| NextStepCard: who it waits on, team members, authority note, due/overdue | current step | open tiles via `stepFacts` |
| NextStepCard: "Then: next step" | — | "Next" on the next tile (the tile row shows the order) |
| NextStepCard: "Workflow complete. Every step is done." | run complete | one-line state in the card |
| Stage headings of the step list | stage known | `.tstage` caption on the first tile of each stage |
| Review result (readableResult) | step has result | "Review result" link → panel |
| Approval chain | approvals non-empty | "Approval chain" link → panel |
| Negotiation rounds (non-actors) | negotiate step && !canAct | "Negotiation rounds" link → panel |
| Agent hand-off trace (N) | brief non-empty | link → panel |
| Send back for rework (select, reason, Send back, hint) | hasRun && !complete && canAct && curIdx > 0 | "Send back for rework" link → `.sendback` form; decision area's "Send back for rework…" opens and focuses it |
| Count text "N of M steps done, K skipped" | rows non-empty | card header `.cnt` |
| Skipped / failed / cancelled marking, not counted as done | — | tile tone + line; count unchanged |
| Parallel open steps highlighted | open steps | every open tile `.cur` |

### CSS (all inside the existing `.ro` scope strings; palette variables only)

- `STEPS_CSS` — keep: `.flowmeta(.warnline) .flowpick .startit .xtoggle .sdetail(.rr .rk .rv) .trace .sendback(.lbl)
  .ministep(.doorway,[disabled]) .appr` family. **Delete**: `.nowblock .steps .step .step::before .snode
  .step.done/.skip/.cur/.warn .snode .shd .sname .sbadge(.done .cur .warn .skip) .stagehd .sassign`. **Add**:
  `.wfname{color:var(--ink-3);font-size:12.5px;overflow-wrap:anywhere;min-width:0}`
  `.wft{display:grid;grid-template-columns:repeat(auto-fill,minmax(128px,1fr));gap:8px}` (wraps — never scrolls the page)
  `.wft .t{min-width:0;border:1px solid var(--border);border-radius:9px;padding:8px 10px;display:flex;flex-direction:column;gap:2px;font-size:12px;overflow-wrap:anywhere}`
  `.wft .t b{font-weight:600;font-size:12.5px} .wft .t small{color:var(--ink-3)}`
  `.wft .t.done{background:var(--good-soft);border-color:transparent} .wft .t.cur{border-color:var(--accent);background:var(--accent-soft)}`
  `.wft .t.warn{border-color:var(--warn);background:var(--warn-soft)} .wft .t.skip{border-style:dashed;color:var(--ink-3)} .wft .t.todo b{color:var(--ink-2)}`
  `.wft .t .over{color:var(--crit);font-weight:600} .wft .t .tstage{font:600 9.5px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}`
  `.wft .t .clamp{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}`
  `.wft .t .ministep{align-self:flex-start;margin-top:4px}`
  `.wflinks{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:10px} .wfpanel{margin-top:6px} .wfnotice{margin-top:8px}`
- `CARDS_CSS` — **delete**: `.trail` family (`.trail`, `li`, `::before`, `::after`, `.you`, `.cp`, `.w`, `.t`) and
  `.glance` family (`.glance .row`, `.k`, `.v`). **Add**:
  `.chsub{margin-left:auto;font-size:11.5px;color:var(--ink-3)}`
  `.actv .day{font:600 10.5px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:8px 0 4px}`
  `.actv .ev{display:flex;gap:10px;align-items:flex-start;padding:5px 0;font-size:12.5px;min-width:0}`
  `.actv .ev>span{min-width:0;overflow-wrap:anywhere} .actv .ev .who{color:var(--ink-3);font-size:12px}`
  `.actv .ev time{margin-left:auto;flex:none;color:var(--ink-3);font-size:11px}`
  `.actv .ev i{width:9px;height:9px;border-radius:50%;flex:none;margin-top:5px;background:var(--border-strong)}`
  `.actv .ev i.ai{background:var(--accent)} .actv .ev i.done{background:var(--good)} .actv .ev i.stage{background:var(--warn)}`
  `.actfoot{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:8px}` (buttons reuse `.xtoggle`)
  `.cpname{font-size:14px;font-weight:650;overflow-wrap:anywhere} .cpstat{font-size:12px;color:var(--ink-3);margin:2px 0 6px}`
  `.cpfile{display:block;padding:7px 0;border-top:1px solid var(--border);text-decoration:none;min-width:0}`
  `.cpfile .n{color:var(--accent);font-weight:600;font-size:12.5px;overflow-wrap:anywhere}`
  `.cpmeta{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:11.5px;color:var(--ink-3);margin-top:2px}`
  `.cpsec{margin-top:8px}` (section label uses `.lbl`)
- Pills reuse the base `.pill` (`.pill.warn`, `.pill.bad`) plus one new `.pill.ok{background:var(--good-soft);color:var(--good)}`
  and `.pill.cur{background:var(--accent-soft);color:var(--accent)}` in `RO_CSS`; ended → `.pill` with
  `background:var(--surface-2);color:var(--ink-3)` (`.pill.idle`). No hex, no `bg-white`; every variable above is
  already used by the 010 CSS (dark values exist in `globals.css`).
- Every deleted selector is checked by T004/T005 with a grep of `frontend/src` so none is orphaned or still used.

## Test strategy

### Backend pytest (`docker compose exec backend python -m pytest tests/test_request_activity.py tests/test_request_counterparty.py tests/test_intake_every_party.py -q`)

Real rows via `db_session` / `client`; walls built as in `tests/test_portfolio_filter.py`. Each test's docstring
names the failure mode.

`tests/test_request_activity.py` (T001):
- happy: request + contract with `intake.created`, `intake.triaged`, `flow.started`, `contract.lifecycle_changed`
  (`to_stage=review`), `playbook.run_completed` (`deviation_count=1`), `contract.issue_fixes_drafted`,
  `ai.skill_started`, a hand-off and a run comment → items newest first, sentences exactly as the table ("Stage
  changed to Review", "AI review complete · 1 issue found", "AI started: Playbook review"), sources and `system`
  flags right, `who` = "AI" for ai rows and the person's name for person rows. (AC-7, AC-10, AC-11)
- every `event_type` in `EVENTS` plus a made-up `contract.something_new` and an `intake.stage_advanced` row whose
  title equals its code → no sentence equals/contains its event_type, no UUID, no `{`; the unknown type reads
  "Contract updated" and is `system`. (AC-9, R2)
- coverage guard: every string captured by `event_type\s*=\s*"([^"]+)"` or `audit_action[^=\n]*=\s*"([^"]+)"`
  (the second also catches the `audit_action: str = "contract.text_manually_edited"` default) in `app/**/*.py`,
  whose prefix is one of `intake.`/`flow.`/`contract.`/`playbook.`/`assistant.`/`approval.`/`ai.`/`signature.`/
  `renewal.`/`workflow.`, is a key of `EVENTS`; plus `approval.delegate` and `approval.escalate` (written via
  `f"approval.{kind}"`). Fails when someone adds an event without a sentence. (R2)
- walled contract: viewer with `intake:read` who is behind an ethical wall on the request's contract gets the
  request events but **no** contract event, and no `access.denied` audit row is written by the call. (AC-13, AC-28)
- viewer without `contract:read` → no contract events.
- non-staff requester: sees request events and hand-offs of their own request, **no** run comments; another
  member's request → 404. (AC-13, AC-26)
- other org's request → 404; missing `intake:create` → 403.
- more than 500 events → 500 items, `truncated: true`.
- the GET writes no `audit_log` and no `resource_timeline_event` row (counts before/after). (AC-28)

`tests/test_request_counterparty.py` (T003):
- visible set: 3 matching contracts (one behind a wall for the viewer), the request's own contract, a contract of
  another counterparty → `contracts` lists the 2 visible ones, `summary.contracts == 2`, the walled title/id appear
  nowhere in the JSON, the note says "2 prior contract(s)". (AC-18, AC-22)
- every matching contract hidden → `summary` zeros, lists empty, note "New counterparty. No other contracts or
  requests on file." (AC-23)
- name matching unchanged: request names "Umbrella Corp", contract "Umbrella Corporation" → matched; "Umbrella
  Pharma" vs "Acme" → not; the ILIKE prefilter never drops a `_name_match` hit (parametrised pairs incl. accents,
  punctuation and a name that is all short tokens). (AC-19)
- requests: this request excluded; non-staff requester sees only their own earlier requests (another member's
  matching request not listed nor counted); staff sees both; an earlier request tied to a walled contract is not
  listed nor counted for the walled viewer. (AC-18, AC-24)
- ordering: closed/expired/archived contracts after live ones, newest first within each; `active` counts only
  stage `active` and not ended. (FR-18 edge case)
- viewer without `contract:read` → no contracts, counts 0 (requests still listed).
- every listed contract passes `get_contract_for_user` and every listed request passes `get_request` for the same
  viewer. (AC-25)
- request with no party → `name: null`, empty. Other org → 404; missing permission → 403.
- `GET /intake/requests/{id}` and `GET /intake/requests` never include `screening.relationship`, even for a row
  stored with the old dossier; `compute_screening` returns no `relationship`. (FR-24 leak)

`tests/test_intake_every_party.py` (T003): line 66 `assert result["relationship"]["note"]` → assert
`"relationship" not in result` (the primary counterparty assertion stays).

### Frontend vitest (`npm run typecheck && npm run test` from `frontend/`)

- `lib/workflow-steps.test.ts` (T004): `overdueWords` — 2 days 3 h late → "Overdue 2 days"; 1 day → "Overdue 1
  day"; 3 h → "Overdue".
- `components/lifecycle-view.render.test.tsx` (T004, existing — must pass unchanged; add one `stepFacts` case:
  live approval wins over team, team + members when no assignee).
- `_request-steps.test.tsx` (T004, rewritten for WorkflowCard): six rows at step 4 → one card, "3 of 6 steps done",
  six tiles in one `.wft`, three `.done`, the fourth `.cur` with "With EY-Dev legal approval team", "Overdue 2
  days" and "Check approval status", two `.todo` with the first "Next" (AC-2); skipped and failed tiles say
  "Skipped"/"failed" and the count excludes them (AC-3); Review result / Approval chain / trace hidden until
  their link is clicked, no `{`/`deviation_id` in the panel (AC-4); no links/buttons when given none (AC-5);
  send back: hidden until "Send back for rework" (or `open: true`), "Send back" disabled until a step is picked;
  FlowLine states (Start it only when allowed, setting up, none + picker); two open parallel steps both `.cur`.
- `_request-cards.test.tsx` (T005): Activity — loading line; error line + Retry calls `onRetry` (AC-12); empty →
  "No activity yet." and no system toggle; 25 non-system items → 20 shown + "Show earlier activity" reveals 25
  (AC-14); two days → two `.day` headings, newest day first, newest event first in a day (AC-8); system items
  hidden, "Show system details (2)" reveals them in order, "Hide system details" hides; no toggle without system
  items (AC-11). Counterparty — name, summary "8 other contracts · 2 active · 7 earlier requests", 5 + 5 shown
  with "Show 3 more" / "Show 2 more" revealing the rest (AC-20); contract link `href="/contracts/{id}"` with
  stage, type, value, date (AC-16); request click calls `onOpenRequest(id)`, href `/intake?open={id}` (AC-17);
  contracts but no requests → one line for requests (AC-21); empty set → name + note only, no count (AC-23);
  `name: null` → renders nothing; error → one line + Retry. Remaining PeopleCard/DocumentsCard/AskAegis/Comments
  tests unchanged.
- `lib/request-facts.test.ts` (T005): delete the `activityTrail` block; add `groupActivityByDay` (order kept,
  labels, year on an old date) and `counterpartySummary` (parts omitted at 0, all zero → null).
- `_request-overview.test.tsx` (T006): mocks gain `intakeApi.activity` (default `{ items: [], truncated: false }`)
  and `intakeApi.counterparty` (default name null); `handoffs` mock may stay unused. Changes:
  - layout test (line ~987): main = `["Approval", "Your decision", "What was asked", "AI risk review", "Workflow",
    "Activity", "Comments and questions"]`, side = `["Counterparty", "Owner and people", "Documents", "Ask Aegis"]`,
    and no "Steps" heading anywhere (AC-1).
  - `ladder()` → `.wft`; skipped-steps tests read tile text ("Skipped", count text unchanged); AC-7 test clicks
    "Review result" link.
  - send-back tests open the card's "Send back for rework" first; the decision area's "Send back for rework…"
    opens the form and focuses the select without calling `returnStep`; AC-18 test keeps each action exactly once.
  - "notes written on the workflow" test: the comment appears in Comments; Activity comes from the mocked endpoint
    (assert the mocked sentence renders) — the client no longer merges comments.
  - AC-6 test: "Globex" now appears in the facts and once as the Counterparty card name (011 FR-16 supersedes the
    010 single-occurrence rule for the counterparty name; header still has none).
  - new: requester (canManage false) sees tiles and states but no Check/Send back/Start/picker/doorway (AC-5,
    AC-26); activity endpoint rejecting → Activity shows the error line and the rest of the page renders
    (AC-12, edge case); typing a comment then clicking "Show earlier activity" keeps the typed text (edge case).

### Acceptance verification (qa-engineer, T007)

| AC | Evidence |
|---|---|
| AC-1 | overview layout vitest + browser REQ-7705 screenshot |
| AC-2, AC-3 | `_request-steps` vitest + browser on REQ-7705 (approval step) |
| AC-4 | browser as admin on an open request: each control used; audit/timeline row counts before/after equal today's (`docker compose exec -T postgres psql … audit_log / resource_timeline_event`) |
| AC-5, AC-26 | overview vitest (canManage false) + browser as a member requester, an approver, a legal_reviewer |
| AC-6, AC-29 (narrow) | browser at 390 px: `document.scrollingElement.scrollWidth <= innerWidth`, every tile/link reachable |
| AC-7, AC-8, AC-10 | browser REQ-7705 Activity + `test_request_activity` happy path |
| AC-9 | `test_request_activity` guard test + browser text scan of Activity for `\.\w+_|[0-9a-f]{8}-` |
| AC-11, AC-12, AC-14 | `_request-cards` vitest + browser (toggle on REQ-7705) |
| AC-13 | `test_request_activity` walled + requester cases |
| AC-15–AC-21 | `test_request_counterparty` + `_request-cards` vitest + browser on the Initech request |
| AC-22–AC-24 | `test_request_counterparty` wall / hidden / requester cases; browser as a walled user (wall created in Admin → Walls on one Initech contract) |
| AC-25 | `test_request_counterparty` open-check case + browser click-through of every listed item |
| AC-27 | browser light + dark screenshots of the four cards |
| AC-28 | `test_request_activity` no-write case + DB counts around a page open and an item click |
| AC-29 / FR-30 | side-by-side with mockup screen 2 for REQ-7705 + the six request states listed in FR-30; differences recorded for the user |

## Risks & decisions

- **D1 — Server-side merge and wording (chosen) vs client-side mapping (rejected).** The event codes, `details`
  JSON and ids never leave the server, so no client bug can print one (AC-9 enforced at the boundary); one
  table, one test that walks it. Paging a merge of three differently gated sources is only correct where they are
  merged. Rejected: sending raw `event_type`/`details` to the browser and mapping in TS — exposes identifiers and
  needs the same table twice.
- **D2 — One capped list (500) + client batches of 20 (chosen) vs cursor pagination (rejected).** The newest 500
  merged events cover every request in the dev DB (REQ-7705 has 42); the "system details" toggle and the
  hidden-count need the whole list anyway. `# ponytail:` in `activity.py`: "500-item cap; switch to a
  `before=<at>` cursor if a request's history ever passes it (the response already says `truncated`)".
- **D3 — Comments follow today's run gate.** Requesters cannot read the run today (`/runs/by-request` needs
  `intake:read`), so their Activity carries no comments; hand-offs and request events (spec FR-13) they do get.
  Rejected: comments for everyone who sees the request — a new exposure the spec does not ask for.
- **D4 — Silent contract check.** `not is_sealed_off(...) and user_can_access_contract(...)` gives the contract
  page's exact rule (incl. pending approver / step assignee) without writing an `access.denied` audit row on page
  open (AC-28). Rejected: `get_contract_for_user` (logs denials) and `accessible_contract_filter` alone (would hide
  the contract's events from the approver the request is waiting on).
- **D5 — The stored dossier stops being stored and served.** `compute_screening` drops `relationship`;
  `serialize_request` strips it from old rows; the audit row of the screening job keeps only the counterparty name.
  This closes the leak where every viewer received org-wide counts/total inside `RequestResponse.screening`.
  Rejected: recomputing the stored note per viewer (it is per request, not per viewer) or a data migration
  (unneeded once nothing serves the key). Note: `intake.relationship.checked`'s `after` loses `prior_contracts` —
  the only change to an existing audit payload, made because that count is exactly the org-wide figure FR-24 says
  must not be computed for a viewer.
- **D6 — No total value.** Contracts carry mixed currencies (`Contract.currency`) and today's note printed `~$K`
  over INR amounts; FR-17 makes the total optional. The note keeps today's wording minus the value.
- **D7 — Contracts list uses `accessible_contract_filter` (SQL)**, which is stricter than the contract page's row
  check (no pending-approver/step-assignee allowance). Listed ⇒ opens (FR-26); a contract the viewer could open
  only as a pending approver is not listed — acceptable, never a leak.
- **D8 — SQL prefilter for the name match.** `ILIKE '%token%'` OR'd over the request name's normalized tokens is a
  sound superset of `_name_match` (every match shares a token, and each normalized token is a substring of the
  lower-cased raw name), so only candidates reach Python. Today's code loaded all 1,034 contracts per screening.
  Requests (68 rows; names live in JSON) stay a Python scan with a `ponytail:` ceiling.
- **D9 — WorkflowCard replaces StepsCard in the same file** (fewer files; ApprovalChain/Toggle/FlowLine reused).
  `NextStepCard`'s logic is extracted to `stepFacts` and reused by the tiles, so the "who / due / overdue / team"
  rules cannot drift between the contract page and the request page. Rejected: rendering `NextStepCard` under the
  tiles (repeats "With …" and the due date — 010's de-duplication rule).
- **D10 — Request links open in place.** `/intake` reads `?open=` only on mount, so a plain `<Link>` to another
  request would change the URL and nothing else. The list calls `onOpenRequest(id)` (page sets `detailId`, keyed
  remount drops drafts of the previous request); the `href` keeps new-tab/middle-click working.
- **R1 — Access leak via the stored note**: closed by D5; pinned by the "never includes relationship" test.
- **R2 — Event-type drift**: unknown types get the system fallback ("Contract updated"/"Request updated"), never a
  code; the source-scan test fails CI when a new literal event type has no row in `EVENTS`.
- **R3 — Per-viewer filtering cost**: SQL does org/wall/clearance/ownership + the ILIKE prefilter; Python only
  confirms candidates. Requests scan is O(visible requests) — fine at today's size, ponytail-marked.
- **R4 — Long real titles** ("Initech — Master Services Agreement (their paper)", subjects like
  `Today_14_test`): every new text container has `min-width:0; overflow-wrap:anywhere`; authority notes clamp to
  two lines; tiles wrap onto more rows instead of shrinking below 128 px.
- **R5 — Dirty working tree**: the 010/009 files these tasks edit are uncommitted; agents edit the working copy,
  not `HEAD`.
- FR traceability:
  - FR-1 → `_request-overview.tsx` column JSX (T006); FR-2 → card collapse rules in `_request-cards.tsx` /
    `WorkflowCard` (T004/T005).
  - FR-3, FR-4 → `WorkflowCard` tiles + row derivation (`wait`, `focus`, `next`, failed/cancelled badge) + `stepFacts`,
    `overdueWords` (T004, T006).
  - FR-5, FR-6 → `WorkflowCard` links/panels + send-back open state + inventory table (T004, T006).
  - FR-7, FR-29 → `.wft` auto-fill grid, wrapping links, `min-width:0` (T004/T005); browser check (T007).
  - FR-8, FR-9, FR-10, FR-11, FR-13 → `GET /activity` + `activity.py` mapping table and gates (T001); ActivityCard
    day grouping, toggle (T005).
  - FR-12, FR-14 → ActivityCard loading/error/empty states; `keepPreviousData` query (T005, T006).
  - FR-15 → ActivityCard 20-item batches (T005); server cap D2 (T001).
  - FR-16–FR-24 → `GET /counterparty` + `visible_counterparty` (T003); CounterpartyCard (T005).
  - FR-24 (no leak) → D5: `compute_screening` + `_public_screening` (T003).
  - FR-25, FR-26 → `accessible_contract_filter`, `_accessible`, `sealed_off_filter`, `contract:read` check (T003);
    `onOpenRequest` / contract links (T005, T006).
  - FR-27 → CounterpartyCard returns null when `name` is null; endpoint behind the request's own gate (T003, T005).
  - FR-28 → CSS section (T004, T005), dark/light check (T007). FR-30 → T007.

## Task split (proposal for /tasks)

| Task | Agent | Files | dependsOn | Wave |
|---|---|---|---|---|
| T001 [P] Activity read: `activity.py`, schemas (activity models), route, tests | backend-dev | `app/intake/activity.py`, `app/intake/schemas.py`, `app/intake/routes.py`, `tests/test_request_activity.py` | — | 1 |
| T002 [P] Frontend contract: types + client wrappers | frontend-dev | `src/lib/types.ts`, `src/lib/endpoints.ts` | — | 1 |
| T003 Counterparty read + stop serving the dossier | backend-dev | `app/intake/screening.py`, `app/intake/service.py`, `app/intake/schemas.py`, `app/intake/routes.py`, `tests/test_request_counterparty.py`, `tests/test_intake_every_party.py` | T001 (shares routes/schemas) | 2 |
| T004 [P] WorkflowCard + `stepFacts` + `overdueWords` | frontend-dev | `_request-steps.tsx` (+test), `src/components/lifecycle-view.tsx` (+ render test case), `src/lib/workflow-steps.ts` (+test) | — | 1 or 2 |
| T005 [P] ActivityCard + CounterpartyCard + helpers | frontend-dev | `_request-cards.tsx` (+test), `src/lib/request-facts.ts` (+test) | T002 | 2 |
| T006 Wire the page | frontend-dev | `_request-overview.tsx` (+test), `page.tsx` | T002, T004, T005 | 3 |
| T007 Verification | qa-engineer | `specs/011-request-page-refinements/verification.md` | T001, T003, T006 | 4 |

T001/T003 run beside the frontend tasks (frontend codes against the frozen contract and mocks). After T006,
`python scripts/build_repo_graph.py` is the orchestrator's (graph files are not a task's to claim). Backend gate:
`ruff check .` → `alembic upgrade head` (no-op, still one head `0074_template_sync`) → `pytest -q`. A new module
means `docker compose restart backend` before the live check.

## Open questions

None blocking. Two decisions are worth the user's glance at plan approval (both inside the spec's latitude):
- D5: the screening job's audit row stops recording the org-wide `prior_contracts` count (the figure FR-24 forbids
  computing for a viewer). Say so if that audit field must stay.
- D3: a requester's Activity has request events and hand-offs but not workflow comments, because they cannot read
  those comments anywhere today.
