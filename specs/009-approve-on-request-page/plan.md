# Implementation Plan: Approve and reject on the request page

Feature ID: 009-approve-on-request-page
Spec: ./spec.md (must be APPROVED)
Created: 2026-10-22
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

The feature reuses what already decides approvals and adds a read view on top. **It needs no migration,
no new table, no new Celery task, no new setting, no new dependency and no AI call.** The new pieces:

- **The decision route** `POST /approvals/requests/{id}/decision` (`app/approvals/routes.py:322`) gains
  one optional body field, `acknowledged_open_risks`, and a risk-acknowledgement gate. The gate lives in
  the route only, not in `decide_in_app`, which the assistant also uses. Everything else the spec asks to
  keep (no self-approval, authority limit, row lock, reject needs a comment, 409 on a repeat) already
  happens in `decide_in_app` → `_apply_decision` (`app/approvals/service.py:931`, `:732`).
- **"Assigned" now means assigned.** `_user_eligible_to_decide` (`service.py:888`) lets anyone holding
  `approval:admin` decide any rung, and with `DISABLE_RBAC` (on in dev compose) that is every user. A new
  keyword `assigned_only=True` skips that shortcut. It is used for deciding (route and `decide_in_app`)
  and for every `can_decide` the UI reads. Viewing and reassigning keep the admin shortcut.
- **Intake chain rows** (`IntakeService._serialize_chain`, `app/intake/service.py:1479`) gain
  `can_decide` and `decisions`. The chain route's permission changes from `intake:read` to `intake:create`,
  the same gate as `GET /intake/requests/{id}`. `get_request` still allows only intake staff or the
  requester, so the requester can now see the chain (FR-2).
- **A new read endpoint** `GET /intake/requests/{id}/approval-summary`, built by a new module
  `app/intake/approval_summary.py`. It reads only stored rows: `Contract` columns + `metadata_json.field_sources`,
  `ClauseExtraction` for the current version (not stale), `RenewalEvent` for the current version,
  `contract_risk()` (`app/contracts/risk.py:200`, read-only, the page header's own source), or the
  request's `field_values` when there is no contract.
- **The approver view** is the workflow Approval step's `config.summary` (`general | finance | legal`,
  unset = general). `_clean_steps` (`app/workflows/service.py:2158`) passes `config` through as it is. It
  gains one check that refuses an unknown value. A new helper `approval_view_for` in the same file reads
  the view for a rung. The workflow edit is already audited (`workflow.updated`, before and after steps).
- **Frontend:** a new `intake/_approval-card.tsx` rendered inside `_request-overview.tsx` (inside the
  `.ro` scope, reusing the `RO_CSS` classes `.card .ch .cb .facts .f .k .v .decide .dw .reply .db .btn.pri`).
  The chain card shows each rung's decisions, the workflow designer gets one select, and the approvals
  page handles the new 422 (see Decision D4).

Convention templates: the route and service shape mirror `app/approvals/` and `app/intake/`. The pure
read module mirrors `app/intake/sow.py` / `consultancy.py` (module-level functions, no class). The card
mirrors the "Your decision" card already in `_request-overview.tsx:690-713`.

```
 request page (_request-overview.tsx)
   ├─ GET /intake/requests/{id}/approval-chain ──▶ IntakeService.get_approval_chain
   │                                               └─ _serialize_chain(rows, actor) + can_decide, decisions[]
   │                                                    └─ approvals.routes._can_decide_approval(assigned_only=True)
   ├─ _approval-card.tsx (when a rung is pending)
   │    ├─ GET /intake/requests/{id}/approval-summary?approval_request_id= ──▶ intake/approval_summary.build_summary
   │    │        ├─ workflows.service.approval_view_for(run, rung) → config.summary
   │    │        ├─ Contract + field_sources │ ClauseExtraction(current version, !is_stale) │ RenewalEvent(current version)
   │    │        ├─ contracts.risk.contract_risk → open_risk {critical, high}   (same as the page header)
   │    │        └─ no contract: request field_values → "requester-stated"
   │    └─ POST /approvals/requests/{aid}/decision {decision, comment, acknowledged_open_risks}
   │             404 → 409 already decided → 403 not assigned → 422 ack gate → decide_in_app (unchanged gates)
   │             → audit approval.decided (existing) + approval.risk_acknowledged (new) → commit
   │       then: invalidate chain/request/flow-run/approvals/contract-risk/summary
   │             + POST /workflows/runs/{run}/refresh (FR-24: the no-contract path advances now)
   └─ Approval chain card: rung + decisions (who, comment, when)
 workflow designer (_designer.tsx): Approval step → "What the approver sees" select → config.summary
```

Discrepancies flagged (constitution: "match the surrounding code and flag"):
- The constitution mentions `src/lib/demo.ts`. There is no demo mode in this checkout (as 008 noted).
- `has_permission()` returns True for everything under `DISABLE_RBAC` (dev compose), so in dev every user
  holds `approval:admin` and, before this feature, was "eligible" for every rung. `assigned_only` is what
  makes FR-1/FR-3 true in dev as well as in production.
- "Self-approval" in the existing code means `approval.requested_by_user_id == user.id`: whoever advanced
  the workflow into the Approval step. That is not always the intake request's requester. FR-27 says
  "continue to refuse", so the plan keeps that definition. See Open question 1.
- `create_flow` writes no audit row today (only `update_flow` does). The spec asks that a change to the
  view be audited "like other workflow edits". Edits are audited. Creation stays as it is.

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | **none.** No schema change: no model, no `app/models.py` entry, no migration. The current Alembic head stays as it is. |
| backend-dev | `backend/app/approvals/routes.py` (payload field, decision route gate + ordering, `_can_decide_approval(assigned_only=)`, `list_approvals` uses `assigned_only=True`), `backend/app/approvals/service.py` (`_user_eligible_to_decide(assigned_only=)` + module wrapper, `decide_in_app` uses it, `_apply_decision` strips the comment), `backend/app/intake/service.py` (`_serialize_chain(rows, *, actor)`, planned rungs get `can_decide`/`decisions`), `backend/app/intake/routes.py` (chain route permission, new summary route), `backend/app/intake/approval_summary.py` (**new module**), `backend/app/workflows/service.py` (`APPROVAL_VIEWS`, `_clean_steps` check, `approval_view_for`), `backend/tests/test_approval_card.py` (**new**), `backend/tests/test_approval_decide_entry_points.py` (may only be extended, not weakened) |
| frontend-dev | `frontend/src/lib/types.ts` (interfaces + one constant), `frontend/src/lib/endpoints.ts` (`intakeApi.approvalSummary`, `approvalsApi.decide` 4th arg), `frontend/src/app/(app)/intake/_approval-card.tsx` (**new**), `frontend/src/app/(app)/intake/_approval-card.test.tsx` (**new**), `frontend/src/app/(app)/intake/_request-overview.tsx` (render card, chain decisions), `frontend/src/app/(app)/intake/_request-overview.test.tsx` (mock additions + chain-decisions case), `frontend/src/app/(app)/approvals/page.tsx` (the 422 acknowledgement retry, D4), `frontend/src/app/(app)/approvals/page.test.tsx` (its case), `frontend/src/app/(app)/workflow-builder/[id]/_designer.tsx` (the select) |
| qa-engineer | `specs/009-approve-on-request-page/verification.md` |

Shared-hotspot note: `src/lib/types.ts` and `src/lib/endpoints.ts` are touched by one frontend task only
(do them first in that task). No task touches `app/models.py`, `app/jobs/tasks.py` or
`src/components/ui.tsx`. Backend and frontend can run in parallel against the freeze below. Inside the
backend, `approvals/*` and `intake/*` + `workflows/service.py` can be one task or two. If two, the
summary task depends on nothing in the approvals task except `open_risk_counts`, which lives in the
summary module and is imported by the route.

**Reload caveat:** `app/intake/approval_summary.py` is a new module. After creating it, run
`docker compose restart backend` (uvicorn `--reload` misses new files on macOS). The worker restarts itself.

## Interface freeze (contract-first — fixed before implementation starts)

### API contract

| Method | Path | Permission | Request body | Response (200) | Errors |
|---|---|---|---|---|---|
| GET | `/intake/requests/{request_id}/approval-chain` (**changed**) | `intake:create` (was `intake:read`) + `get_request` visibility (intake staff or the requester) | — | `IntakeApprovalRung[]` (extended, below) | 404 `"Request not found"` |
| GET | `/intake/requests/{request_id}/approval-summary?approval_request_id=<id>` (**new**, query param optional) | `intake:create` + `get_request` visibility + contract access (`get_contract_for_user`) when the request has a contract | — | `ApprovalSummary` (below) | 404 `"Request not found"` (missing, other org, not visible) · 404 `"Contract not found"` (walled / no clearance / deleted) · 404 `"Approval not found for this request"` (param names a rung outside this request's chain) |
| POST | `/approvals/requests/{approval_request_id}/decision` (**changed**) | `approval:decide` + assigned on the rung (no admin shortcut) | `ApprovalDecisionPayload` (below) | the `ApprovalRequest` row, as today | 404 · 409 · 403 · 422, in the order below |
| PATCH | `/workflows/{flow_id}` (**validation only**) | `workflow:update` (unchanged) | unchanged; an Approval step's `config.summary` ∈ `general\|finance\|legal` or absent | unchanged | 422 `"Approver view must be general, finance or legal"` (new) |
| POST | `/workflows` (**validation only**) | unchanged | as PATCH | unchanged | same new 422 |

The PATCH/POST rows change no shape. `_clean_steps` refuses an unknown view, and the 422 comes back
through the existing path.

**Decision route: check order and exact errors** (each refusal leaves no `ApprovalDecision` row, no
status change and no audit row):

1. 404 `"Approval request not found"`: missing or `org_id` ≠ the user's (AC-5). Existing.
2. 409 `"Approval request is already decided"`: `approval.status != "pending"`. This is a new pre-check
   in the route, with the same text `_apply_decision` uses, so a stale page or a double click gets a clear
   message rather than a 403 (AC-29, edge case).
3. 403 `"You are not assigned to decide this approval"`:
   `_can_decide_approval(db, approval=..., user=..., assigned_only=True)` is False. That covers someone
   not named, a team non-member, an admin who is not assigned, the person who raised the approval
   (self-approval), and a contract not in the Approval stage. Existing text.
4. 422 `"Open critical or high risks must be acknowledged before approving"`: only when
   `decision == "approve"`, `critical + high > 0` (`open_risk_counts`, below) and
   `acknowledged_open_risks` is not true. **New.** The frontend matches this exact string (constant
   `RISK_ACK_REQUIRED_MESSAGE`).
5. `decide_in_app` (unchanged path; now `assigned_only=True` inside):
   - 403 `"You are not assigned to decide this approval"`
   - 403 from `enforce_authority` (the existing message, which contains "delegated authority") on approve
     (AC-28)
   - 422 `"Rejection requires a comment"`. `_apply_decision` now strips the comment first, so
     `"   "` → `None` → refused (FR-17). A stored comment is saved stripped. An empty approve comment is
     stored as `null` (AC-17).
   - 409 `"Approval request is already decided"` (row lock, AC-29) · 409
     `"You have already decided this approval"` (an "all members" rung)
   - 409 from `subject.guard_can_decide` (request closed, contract stage), existing.
6. On success: when step 4 applied (approve, counts > 0, acknowledged), write
   `approval.risk_acknowledged` **after** `decide_in_app` returns and before `db.commit()`, so a refused
   decision leaves no acknowledgement row.

Full request/response JSON shapes:

```json
// POST /approvals/requests/{approval_request_id}/decision — request
{
  "decision": "approve",              // "approve" | "reject" (existing pattern)
  "comment": "Fine within budget",    // string | null, optional (existing); stripped server-side
  "acknowledged_open_risks": true     // NEW: bool, optional, default false; read only for an approve with open risks
}
// response 200 — unchanged: the ApprovalRequest row (FastAPI default serialisation of the model, as today)
```

```json
// GET /intake/requests/{request_id}/approval-chain — response (each rung; NEW keys marked)
[
  {
    "approval_request_id": "a-1",          // null on a "planned" rung
    "step_order": 1,
    "status": "pending",                   // pending | waiting | approved | rejected | cancelled | planned
    "mode": "any",                         // "any" | "all"   (planned rungs: omitted, as today)
    "approvals": 0,                        // (planned rungs: omitted, as today)
    "needed": 1,                           // (planned rungs: omitted, as today)
    "approver_label": "Finance",
    "due_at": "2026-10-29T10:00:00+00:00",
    "can_decide": true,                    // NEW (always present; false on planned rungs)
    "decisions": [                         // NEW (always present; [] on planned rungs), oldest first
      {
        "approver_user_id": "u-7",
        "approver_name": "Priya Shah",     // full_name, else email, else null (user gone / other org)
        "decision": "approve",             // "approve" | "reject"
        "comment": "Within budget",        // string | null
        "decided_at": "2026-10-22T09:14:03+00:00"
      }
    ]
  }
]
```

`can_decide` is true iff **all** of the following hold: `status == "pending"`; the actor holds
`approval:decide` (`has_permission`); `_can_decide_approval(db, approval=a, user=actor, assigned_only=True)`;
and the actor has no `ApprovalDecision` on this rung. `decisions` comes from one org-scoped query over all
the rungs' ids (`ApprovalDecision.org_id == actor.org_id`, ordered by `decided_at` ascending) and one
org-scoped `User` query for names.

```json
// GET /intake/requests/{request_id}/approval-summary?approval_request_id=a-1 — response
{
  "request_id": "q-1",
  "contract_id": "c-1",                    // null when the request has no contract yet
  "approval_request_id": "a-1",            // the rung the view was chosen for; null if none is live
  "profile": "finance",                    // "general" | "finance" | "legal"
  "fields": [
    {"key": "value",         "label": "Value",         "value": "INR 1,200,000.00", "source": "verified",  "quote": false},
    {"key": "term",          "label": "Term",          "value": "2026-11-01 to 2028-10-31", "source": "requester-stated", "quote": false},
    {"key": "payment_terms", "label": "Payment terms", "value": null, "source": null, "quote": false},
    {"key": "liability",     "label": "Liability",     "value": "Each party's aggregate liability shall not exceed the fees paid in the twelve months…", "source": "extracted", "quote": true},
    {"key": "renewal",       "label": "Renewal",       "value": "Renews automatically; notice by 2028-08-02", "source": "extracted", "quote": false}
  ],
  "open_risk": {"critical": 2, "high": 1}
}
```

Rules for the summary response:
- `fields` is exactly the profile's list, in the profile's order (below). Every key is always present.
- `value: null` ⇔ `source: null`. The client renders `null` as **"Not available"** (FR-6). The server never
  sends that text.
- `quote: true` only for values that are clause wording (liability, indemnity, termination, payment
  terms, and governing law when it comes from a clause). The client shows them as quoted text (FR-8).
- Multi-line values (top issues, non-standard) are joined with `"\n"`. The client renders with `white-space: pre-line`.
- `open_risk` is always present: `contract_risk(db, contract)["counts"]` `critical`/`high` when the
  request has a contract, else `{0, 0}`.
- Profile choice for `approval_request_id`: when the param is given, it must be one of the rungs
  `get_approval_chain` returns for this request (otherwise 404). When omitted, the first rung with
  `status == "pending"` (lowest `step_order`) is used, or none.

Audit actions written by each mutation:
- `POST /approvals/.../decision`: `approval.decided` / `approval.partial_approved` /
  `approval.step_activated` + timeline `approval.decided` / `approval.partial` (all existing, unchanged).
  **New:** `approval.risk_acknowledged`, `resource_type="approval_request"`, `resource_id=approval.id`,
  `org_id`, `actor_user_id`, `request_id`,
  `after={"critical": int, "high": int, "contract_id": str|null, "intake_request_id": str|null}`.
  `intake_request_id` is `approval.intake_request_id`, else `contract.metadata_json["intake_request_id"]`.
  It is written only when an approve with counts > 0 succeeds (FR-21, AC-20).
- `PATCH /workflows/{id}`: `workflow.updated` with the before and after steps (existing; it carries
  `config.summary`).
- The two GET routes write nothing.

### Database schema

No change. Data read (all existing, all org-scoped):

| Table | Columns read | Filter |
|---|---|---|
| `approval_request` | id, status, step_order, approver_*, requested_by_user_id, contract_id, intake_request_id | `org_id == user.org_id` |
| `approval_decision` | approval_request_id, approver_user_id, decision, comment, decided_at | `org_id == user.org_id`, `approval_request_id IN (...)` |
| `contract` | value_amount, currency, effective_date, expiration_date, jurisdiction, current_authoritative_version_id, metadata_json.field_sources, metadata_json.intake_request_id | `get_contract_for_user` (org + deleted + walls/clearance) |
| `clause_extraction` | clause_type, text, start_char, created_at | `org_id == contract.org_id`, `contract_id`, `contract_version_id == current_authoritative_version_id`, `is_stale IS false` |
| `renewal_event` | contract_version_id, notice_date, metadata_json.auto_renewal | `org_id == contract.org_id`, `contract_id` (`renewals.service._find_event`) |
| `playbook_*` | via `contract_risk()` | its own org filters |
| `intake_request` | field_values, contract_id, requester_user_id | `get_request` |
| `workflow_run` / `workflow_step_run` | steps[].config.summary, step_type, status, idx, result.approval_ids | `get_run_for_request(org_id=...)` |

Migration: **none**. No revision and no head change. `config.summary` lives in the existing
`workflow.steps` / `workflow_run.steps` JSON.

### Profiles: fields, labels and source rules (frozen)

| Profile | Fields, in order |
|---|---|
| `finance` | `value`, `term`, `payment_terms`, `liability`, `renewal` |
| `legal` | `playbook_risk`, `indemnity`, `termination`, `governing_law`, `non_standard` |
| `general` | `value`, `term`, `overall_risk`, `top_issues` |

Labels: `value` "Value" · `term` "Term" · `payment_terms` "Payment terms" · `liability` "Liability" ·
`renewal` "Renewal" · `playbook_risk` "Playbook risk" · `indemnity` "Indemnity" · `termination`
"Termination" · `governing_law` "Governing law" · `non_standard` "Non-standard clauses" · `overall_risk`
"Overall risk" · `top_issues` "Top issues".

**Field source tag on a contract column** (`tag_for(contract, column)`), from
`contract.metadata_json["field_sources"][column]`:
`"user"` or `"change_request"` → `verified` · `"form"` → `requester-stated` · `"ai"`, `"computed"` or
absent → `extracted`. See Decision D2.

**Snippet** (`snippet(text)`): `" ".join(text.split())`. If longer than 280 characters, cut to the first
279 characters, `rstrip()`, and append `"…"`.

**Current clauses** (`current_clauses(db, contract)`): when `current_authoritative_version_id` is null,
none. Otherwise `ClauseExtraction` rows for that version with `is_stale == False`, grouped by
`canonical_clause_type(clause_type)`. Per type, the first row by (`start_char` ascending, nulls last;
`created_at` ascending). Rows of any other version are never read (FR-7).

**With a contract** (`C`, the request's `contract_id`, else the run's `contract_id`, same as `get_approval_chain`):

| Key | Value | Source | quote |
|---|---|---|---|
| value | `C.value_amount` → `f"{C.currency} {amount:,.2f}"` (currency omitted if null) | `tag_for(C,"value_amount")` | false |
| term | both dates → `"{eff} to {exp}"`; only effective → `"From {eff}"`; only expiry → `"Until {exp}"` (ISO dates) | `verified` if every date used is verified, else the tag of the first unverified one (effective before expiry) | false |
| payment_terms | snippet of clause `payment_terms` | `extracted` | true |
| liability | snippet of clause `limitation_of_liability` | `extracted` | true |
| indemnity | snippet of clause `indemnification` | `extracted` | true |
| termination | snippet of clause `term_and_termination` | `extracted` | true |
| governing_law | `C.jurisdiction` if set (tag `tag_for(C,"jurisdiction")`, quote false), else snippet of clause `governing_law` (`extracted`, quote true) | as stated | as stated |
| renewal | `RenewalEvent` whose `contract_version_id == C.current_authoritative_version_id` and whose `metadata_json["auto_renewal"]` is a bool: `True` → `"Renews automatically"` (+ `"; notice by {notice_date}"` when `notice_date` is set); `False` → `"Does not renew automatically"`. Anything else (no event, other or null version, `auto_renewal` null) → null | `extracted` | false |
| playbook_risk | `R = contract_risk(db, C)`; `R["band"] == "unknown"` → null; else `"{critical} critical · {high} high"` | `extracted` | false |
| overall_risk | band unknown → null; else `band.capitalize()` (`"High"`, `"Medium"`, `"Low"`) | `extracted` | false |
| top_issues | band unknown → null; no drivers → `"No open issues"`; else the first 3 `R["drivers"]` as `"{severity}: {issue}"` (issue truncated to 160 characters + "…"), joined by `"\n"` | `extracted` | false |
| non_standard | band unknown → null; no drivers → `"None found"`; else all `R["drivers"]` (`contract_risk` caps them) in the same line format | `extracted` | false |

**Without a contract** (FR-12, FR-29); `fv = request.field_values`. Every non-null value is tagged `requester-stated`:

| Key | Value |
|---|---|
| value | `IntakeApprovalSubject(request, type_key=None).value_amount` / `.currency` (the Authority gate's own reading) → same format as above |
| term | `drafting.request_facts(fv)` `effective_date`/`expiration_date` → same format as above; else `fv["term"]` or `fv["nda_term"]` as stated |
| payment_terms | `fv["payment_terms"]` (string) |
| governing_law | `fv["governing_law"]` or `fv["jurisdiction"]` |
| renewal | `fv["term"] == "Renews automatically"` → `"Renews automatically"` + `", each renewal {renewal_term}"` + `", {notice_days} notice"` (each only when present); `"Fixed end date"` → `"Does not renew automatically"`; `"No end date (evergreen)"` → as stated; else null |
| non_standard | `snippet(fv["nonstandard"])` when non-blank |
| liability, indemnity, termination, playbook_risk, overall_risk, top_issues | null |

A blank string is treated as absent everywhere (→ null).

**View for a rung** (`workflows.service.approval_view_for(db, *, run, approval_request_id) -> str`):
no run → `"general"`. Otherwise take the run's approval step runs (`_step_runs`, `step_type == "approval"`).
Use the one whose `result["approval_ids"]` contains `approval_request_id`; failing that, the lowest-`idx`
one whose `status` starts with `"waiting"`. Then read `run.steps[idx]["config"]["summary"]`; a value not in
`APPROVAL_VIEWS` (or none) → `"general"`. Team names and roles are never read (FR-14).

`APPROVAL_VIEWS = ("general", "finance", "legal")` lives in `app/workflows/service.py`. `_clean_steps`: for
`type == "approval"`, if `config.get("summary") is not None` and it is not in `APPROVAL_VIEWS` → 422
`"Approver view must be general, finance or legal"`.

### Frontend TypeScript models (added to src/lib/types.ts)

```ts
// 009 — approve on the request page. Mirrors plan.md's interface freeze exactly.
export type ApprovalView = "general" | "finance" | "legal";
export type ApprovalFactSource = "verified" | "extracted" | "requester-stated";
export type ApprovalFactKey =
  | "value" | "term" | "payment_terms" | "liability" | "renewal"
  | "playbook_risk" | "indemnity" | "termination" | "governing_law" | "non_standard"
  | "overall_risk" | "top_issues";
export interface ApprovalFact {
  key: ApprovalFactKey;
  label: string;
  value: string | null;              // null → render "Not available"
  source: ApprovalFactSource | null; // null iff value is null
  quote: boolean;                    // clause wording: render as quoted text
}
export interface ApprovalSummary {
  request_id: ID;
  contract_id: ID | null;
  approval_request_id: ID | null;
  profile: ApprovalView;
  fields: ApprovalFact[];
  open_risk: { critical: number; high: number };
}
export interface ApprovalDecisionEntry {
  approver_user_id: ID;
  approver_name: string | null;
  decision: "approve" | "reject";
  comment: string | null;
  decided_at: string;
}
/** The decision route's 422 when open critical/high risks were not acknowledged (exact server text). */
export const RISK_ACK_REQUIRED_MESSAGE = "Open critical or high risks must be acknowledged before approving";
```

`IntakeApprovalRung` (types.ts:169) gains two **required** members (the server always sends them):

```ts
  can_decide: boolean;
  decisions: ApprovalDecisionEntry[];
```

`WorkflowStep.config` is already `Record<string, unknown>`, so `config.summary` needs no type change. The
designer narrows it with `ApprovalView`.

### Endpoint client additions (src/lib/endpoints.ts)

- `intakeApi.approvalSummary(id: string, approvalRequestId?: string) → Promise<ApprovalSummary>`:
  `GET /intake/requests/${id}/approval-summary` (+ `?approval_request_id=${encodeURIComponent(approvalRequestId)}` when given).
- `approvalsApi.decide(id: string, decision: "approve" | "reject", comment?: string, acknowledgedOpenRisks?: boolean) → Promise<ApprovalRequest>`:
  `POST /approvals/requests/${id}/decision` with body `{ decision, comment, acknowledged_open_risks: acknowledgedOpenRisks ?? false }`.
  Existing three-argument callers are unchanged.

## Component design

### Backend
- Models: none.
- Schemas: `ApprovalDecisionPayload` (in `app/approvals/routes.py`, where it lives today) gains
  `acknowledged_open_risks: bool = False`. The summary is returned as a plain dict, as the chain is
  (`intake/routes.py` returns dicts for both). No new Pydantic response model.
- Service `app/approvals/service.py`:
  - `ApprovalsService._user_eligible_to_decide(self, *, approval, user, assigned_only: bool = False)`:
    when `assigned_only`, skip the `approval:admin` early return. Everything after it is unchanged
    (requester refused first, then person, role, team). The module wrapper
    `_user_eligible_to_decide(db, *, approval, user, assigned_only=False)` passes it through.
  - `decide_in_app`: calls `self._user_eligible_to_decide(..., assigned_only=True)`. That covers the route
    and the assistant tool (AC-4 "by any means").
  - `_apply_decision`: first line `comment = (comment or "").strip() or None`. Everything else unchanged.
- Router `app/approvals/routes.py`:
  - `_can_decide_approval(db, *, approval, user, contracts=None, assigned_only: bool = False)` passes
    `assigned_only` to `_user_eligible_to_decide`.
  - `list_approvals`: `can_decide=_can_decide_approval(..., assigned_only=True)`. The approvals page then
    stops offering buttons that would now 403 (D3). `_can_view_approval`, `reassign_candidates` and
    `reassign_approval` are unchanged (admin may still view and reassign).
  - `decide_approval`: the check order above. It imports `open_risk_counts` and `subject_contract` from
    `app.intake.approval_summary` (lazily, inside the function, like the other cross-domain imports).
    Then the `approval.risk_acknowledged` audit row and the commit. A `ponytail:` comment names the
    ceiling: the assistant's `decide_approval` tool is not gated by acknowledgement (spec out of scope);
    the upgrade is to move the gate into `decide_in_app` with an `acknowledged` argument the tool must pass.
- New module `app/intake/approval_summary.py` (pure functions, the `sow.py` shape):
  - `PROFILES: dict[str, tuple[str, ...]]`, `LABELS: dict[str, str]`, `QUOTE_KEYS`
  - `tag_for(contract, column) -> str`, `snippet(text) -> str`
  - `request_contract(db, request) -> Contract | None`: `request.contract_id`, else the run's
    `contract_id`. It returns the row only when `org_id` matches.
  - `subject_contract(db, approval) -> Contract | None`: `approval.contract_id` (org-checked); else, for an
    intake row, `request_contract` of its request; else None.
  - `open_risk_counts(db, contract | None) -> {"critical": int, "high": int}`: `contract_risk(...)["counts"]`, or zeros.
  - `current_clauses(db, contract) -> dict[str, ClauseExtraction]`
  - `build_summary(db, *, actor, request, approval_request_id: str | None) -> dict`: chooses the rung
    (validating the param against `IntakeService.get_approval_chain` rows), the view via
    `approval_view_for`, and the fields per the tables above. When the request has a contract, it is loaded
    through `get_contract_for_user` (404 if walled). **No AI, no job, no write.**
- `app/intake/service.py`: `_serialize_chain(self, rows, *, actor)` adds `can_decide` and `decisions`.
  It imports `_can_decide_approval` lazily from `app.approvals.routes`, as it already does for
  `_quorum_needed`. `get_approval_chain` passes `actor`, and planned rungs get `"can_decide": False, "decisions": []`.
- Router `app/intake/routes.py`:
  - `approval_chain`: `current_user=Depends(_CREATE)` (was `_READ`). `get_request` inside still enforces
    staff-or-requester.
  - **new** `GET /requests/{request_id}/approval-summary` (`current_user=Depends(_CREATE)`, query
    `approval_request_id: str | None = None`) → `get_request`, then `build_summary`. The handler stays thin.
- `app/workflows/service.py`: `APPROVAL_VIEWS`, the `_clean_steps` check, and `approval_view_for` (above).
- Celery: none. FR-24 is met by the client calling the existing `POST /workflows/runs/{id}/refresh`
  right after the decision (D5). The existing `resume-workflow-runs` beat (every 2 minutes) stays the backstop.
- Settings: none. No `MOCK_*` flag and no `validate_runtime_settings()` entry.

### Frontend (Next.js page/component tree)
- `src/app/(app)/intake/_request-overview.tsx`: renders `<ApprovalCard requestId={id} rungs={approvals ?? []} runId={flowRun?.id ?? null} />`
  in the right rail, directly above the "Approval chain" card, inside `.ro`. In the chain card, under each
  rung's row, one line per `decisions` entry: `{approver_name ?? "Approver"} · approved|rejected · {fmtDateTime(decided_at)}`
  plus the comment in quotes when present (reusing `.why`-style dim text). Nothing else on the page changes.
  "Your decision" stays for human steps; approval steps never reach it, because their status is
  `waiting_job`, not `waiting_human`.
  - `_approval-card.tsx` (`"use client"`, exports `ApprovalCard`):
    - `active = rungs.find(r => r.can_decide) ?? rungs.find(r => r.status === "pending" && r.approval_request_id)`.
      None → render nothing.
    - `useQuery(["intake-approval-summary", requestId, active.approval_request_id], () => intakeApi.approvalSummary(requestId, active.approval_request_id!))`.
    - Card header "Approval", with a dim note `General view` / `Finance view` / `Legal view`.
    - Facts (`.facts .f .k .v`): label; value or **"Not available"** (dim); `quote` values in quotation
      marks, italic; a small tag after each non-null value reading `verified` / `extracted` /
      `requester-stated`, styled with the page's CSS vars (`--good` for verified, `--ink-3` for the other
      two). The values use `white-space: pre-line`. The card's few new rules sit in its own `<style>`,
      scoped `.ro .apc …`, with **no hardcoded colours and no `bg-white`**.
    - Read-only (`!active.can_decide`): one dim line `Waiting on {active.approver_label}`. No checkbox,
      textarea or buttons (FR-2, FR-23).
    - Decider (`active.can_decide`), in a `.decide` block:
      - risk line: `critical + high > 0` → `.dw` warn `"{c} critical + {h} high risk(s) open"`; else `.dw.ok` `"No critical or high risks open"`.
      - checkbox, only when `critical + high > 0` (FR-20): label `"I have seen the open critical and high risks"`. Approve is disabled until it is ticked.
      - `<textarea>` "Comment (required to reject)", `maxLength={2000}`.
      - Approve (`.btn.pri`) → `decide("approve")`; Reject (`.btn`) → if `!comment.trim()` show inline
        `"Rejection requires a comment"` and do not call the server (FR-17); else `decide("reject")`.
    - `decide` mutation: `approvalsApi.decide(active.approval_request_id!, decision, comment.trim() || undefined, decision === "approve" && gate > 0 ? ack : false)`.
      - onSuccess: `notify(decision === "approve" ? "Approved" : "Rejected", "success")`; clear the comment and
        checkbox; invalidate `["intake-approval-chain", requestId]`, `["intake-request", requestId]`,
        `["flow-run", requestId]`, `["approvals"]`, `["contract-risk"]` (prefix), `["intake-approval-summary", requestId]` (prefix);
        then, if `runId`, `workflowsApi.refreshRun(runId).then(run => qc.setQueryData(["flow-run", requestId], run)).catch(() => qc.invalidateQueries({ queryKey: ["flow-run", requestId] }))`
        (FR-24; a refused refresh is swallowed, see D5).
      - onError: `notify(message, "error")` and the same message inline. The comment and checkbox are kept.
        Invalidate `["intake-approval-chain", requestId]` so a 409 re-renders the current state.
    - Buttons are disabled while the mutation is pending, which is the client half of AC-29.
- `src/app/(app)/approvals/page.tsx` (D4): in `decide`, on an `HttpError` with `status === 422` and
  `message === RISK_ACK_REQUIRED_MESSAGE`, ask
  `window.confirm(`${message}.\n\nApprove anyway?`)`. If the user confirms, call
  `approvalsApi.decide(req.id, "approve", comment, true)` once. Otherwise notify the message. Nothing else
  on the page changes.
- `src/app/(app)/workflow-builder/[id]/_designer.tsx`: in the `tab === "assign"` block, only for
  `cur.type === "approval"`, after "Who approves", add one field `What the approver sees`:
  `<select className="inp" value={cfgStr(cur.config, "summary") || "general"} onChange={e => patchCfg(sel!, "summary", e.target.value)}>`,
  with options `general` "General: value, term, overall risk, top issues", `finance` "Finance: value,
  term, payment terms, liability, renewal", `legal` "Legal: playbook risk, indemnity, termination,
  governing law, non-standard clauses". Saving goes through the existing PATCH.
- New shared primitives in `src/components/ui.tsx`: **none**. The card uses the request page's own
  `.ro` classes (as every card on that page does), plus `useToast` and `fmtDateTime` (from `@/lib/utils`),
  both already imported by the overview.
- Demo-mode behaviour: none. This checkout has no demo mode.

## Test strategy

Every test carries a docstring naming the failure mode it guards (CLAUDE.md).

- **Backend pytest**: new `backend/tests/test_approval_card.py`, using the rollback `db` fixture pattern of
  `tests/test_request_approval_chain_for_contract.py`. Routes are called as functions (the
  `test_approval_decide_entry_points.py` pattern) or through the service.
  - B1 `can_decide` true for the named approver / team member on the pending rung (AC-1).
  - B2 `can_decide` false for: the raiser of the approval, another user, an unassigned user holding
    `approval:admin`, and a user without `approval:decide` (AC-2, FR-3).
  - B3 After an "all members" partial approve, the decider's `can_decide` is false and another member's
    stays true (AC-3, FR-23).
  - B4 Decision route 403 for a non-assignee and for an unassigned admin, with no decision row and the
    status unchanged; the same through `tool_runtime._decide_approval` (AC-4).
  - B5 Other org: summary → 404, chain → 404, decision → 404 (AC-5).
  - B6 Summary starts no AI work: `create_job`, `dispatch_job` and the Claude client are monkeypatched to
    raise; the summary succeeds and the job count is unchanged (AC-6, FR-4).
  - B7 Tags: `field_sources.value_amount = "user"` → verified; `"form"` → requester-stated; `"ai"` and absent →
    extracted; the liability clause → extracted (AC-7, FR-5).
  - B8 Finance view with no payment clause → `payment_terms` value and source null (AC-8, FR-6).
  - B9 A clause on an older version → null; `is_stale=True` on the current version → null; a renewal event
    on an older version → null (AC-9, FR-7).
  - B10 Liability value equals the snippet of the stored text with `quote: true`; a 1,000-character
    clause comes back ≤ 280 characters ending "…" (AC-10, FR-8, edge case).
  - B11 Field keys exactly `["value","term","payment_terms","liability","renewal"]` for finance (AC-11),
    the legal list (AC-12), and the general list when `config.summary` is unset (AC-13). A step whose team
    is named "Finance" with `summary: "legal"` → legal (AC-14).
  - B12 `_clean_steps` keeps `summary: "finance"` and 422s `"budget"`. `update_flow` with finance, then a run
    started on that workflow, gives a summary profile of `finance` (AC-15, FR-13).
  - B13 No contract: value and term from the form are tagged `requester-stated`; liability, risk and the rest
    are null; a form with no value → value null (AC-16, AC-31, FR-12, FR-29).
  - B14 Approve with no comment → decision row `comment is None` (AC-17).
  - B15 Reject with `"   "` → 422 `"Rejection requires a comment"`, no decision row (AC-18).
  - B16 Approve with open critical/high and no acknowledgement → 422 `RISK_ACK_REQUIRED_MESSAGE`; the rung
    is still pending; no decision and no audit row (AC-19, FR-18).
  - B17 Same, acknowledged → approved; one `approval.risk_acknowledged` audit row whose `after` has the
    counts and the request id (AC-20, FR-21).
  - B18 Summary `open_risk` equals `contract_risk(db, C)["counts"]` critical/high, with seeded deviations
    (AC-21, FR-19).
  - B19 Zero critical/high → approve without acknowledgement succeeds and writes no acknowledgement row
    (AC-22, FR-20).
  - B20 After a decision, the chain rung's `decisions` has the name, the comment and `decided_at` (AC-23, FR-22).
  - B21 No-contract request, last rung approved, then `refresh_run` → the run moves past the Approval step
    (AC-24, FR-24).
  - B22 Two-rung chain (step_order 1 pending, 2 waiting): approve 1 → 2 is pending, and the request status
    is not "approved" (AC-25, FR-25).
  - B23 Reject with a comment → sibling rungs cancelled, the run sent back (`pending_rework_reply`
    non-null), and the chain shows the rejection and its comment (AC-26, FR-26).
  - B24 A second approve on the same rung → 409 `"Approval request is already decided"`, still one decision
    row (AC-29, FR-27).
  - B25 The raiser is refused with 403 (AC-27). The authority refusal is already pinned by
    `test_approval_decide_entry_points.py::test_refuses_an_approver_above_their_authority`, which must keep
    passing (AC-28).
  - B26 The requester (holding only `intake:create`) gets the chain and the summary for their own request;
    another member gets 404 (AC-2, permissions).
  - B27 `GET /approvals` row `can_decide` is false for an unassigned admin and true for the assignee (FR-3, D3).
  - Existing suites that must stay green: `test_approval_decide_entry_points.py` (its `FakeDB` route path:
    `get` returns None, so `subject_contract` must return None without querying further),
    `test_approvals_listing.py`, `test_approval_reassign_targets.py`, `test_approval_chain_reroute.py`,
    `test_request_approval_chain_for_contract.py`, `test_phase6_9_integration.py`.
- **Frontend vitest**:
  - `_approval-card.test.tsx` (follows `approvals/page.test.tsx`: hoisted mocks of `@/lib/endpoints`, `useToast`):
    F1 controls shown when `can_decide` (AC-1); F2 read-only with no buttons when not (AC-2, AC-3);
    F3 "Not available" for null values, and tags verified / extracted / requester-stated next to values
    (AC-7, AC-8, AC-16, AC-31); F4 a quote value is rendered as quoted text (AC-10); F5 the profile's
    labels are rendered in order (AC-11..13); F6 checkbox present when counts > 0 and Approve disabled
    until ticked, then `decide(..., true)` (AC-19, AC-20); F7 no checkbox when counts are 0 (AC-22);
    F8 Reject with spaces shows "Rejection requires a comment" and does not call `decide` (AC-18);
    F9 Approve with an empty comment calls `decide(id, "approve", undefined, false)` (AC-17); F10 on success
    the listed query keys are invalidated and `workflowsApi.refreshRun(runId)` is called (AC-23, AC-24);
    F11 on a rejected promise the comment and checkbox keep their values and the error shows (edge case).
  - `_request-overview.test.tsx`: add `approvalsApi` and `intakeApi.approvalSummary` to the
    `@/lib/endpoints` mock. F12 the chain card lists a rung's decision with name, comment and time (AC-23,
    AC-26).
  - `approvals/page.test.tsx`: F13 a 422 with `RISK_ACK_REQUIRED_MESSAGE` plus a confirmed `window.confirm`
    → a second `decide` call with `true`; a declined confirm → no second call (FR-18, FR-28, AC-30).
  - The designer has no test harness today. AC-15 is pinned by B12 and the /verify browser walk.
- **Gates**: `docker compose exec backend ruff check .`, `docker compose exec backend python -m pytest -q`
  (CI also runs `alembic upgrade head`; unaffected), and from `frontend/`: `npm run typecheck`, `npm run test`.
- **Acceptance verification** (`/verify`): run `docker compose restart backend` first (new module).
  With two approvers in teams on two workflows (Finance view and Legal view), and the admin **added to a
  team**, since an unassigned admin can no longer decide:
  - open the request as each approver and compare the facts with the contract page;
  - approve rung 1 of a two-rung chain and see rung 2 become active;
  - reject with a comment and see the send-back and the reply prompt;
  - as the requester and as an unrelated user, see no buttons and get 403 from a direct API call;
  - on a contract with critical findings, see the checkbox gate and the audit row;
  - on a no-contract request, see the run advance right after the last approval;
  - on the approvals page, approve a risky contract through the confirm;
  - check dark and light themes.

  Each AC maps to its B/F test above. Run `/repo-graph` afterwards.

## Risks & decisions

- **D1 Summary keyed by request, not by approval.** `GET /intake/requests/{id}/approval-summary` with
  `intake:create` + `get_request`. Rejected: `GET /approvals/requests/{id}/summary` with `approval:*`. The
  spec says anyone who can view the request sees the card; the `member` role (requesters) holds neither
  `approval:read` nor `approval:decide`, and the request page knows its request id, not always a rung id.
  The optional `approval_request_id` keeps parallel rungs on their own step's view.
- **D2 The tag of a contract column follows `field_sources`, not the column alone.** The brief tagged every
  contract column `verified`. But `apply_request_facts` copies the form's value onto the contract
  (`"form"`), and the metadata job writes AI readings there (`"ai"`). Calling those "verified" is what FR-5
  ("confirmed") and FR-29 ("an unchecked number never looks confirmed") forbid. So: person or change
  request → verified, form → requester-stated, AI / computed / unknown → extracted. Unknown (`field_sources`
  absent, older contracts) errs towards "extracted", because it never overstates. No new tag is added.
  **Please confirm.**
- **D3 `assigned_only` everywhere a decision is offered or taken.** That means the decision route,
  `decide_in_app` (and so the assistant), the intake chain's `can_decide`, and the approvals list's
  `can_decide`. Rejected: gating only the new card. FR-3 and AC-4 say "by any means", and a list that
  offered buttons the route then 403s would be worse. Consequence: an admin who is not on the step's team
  can no longer approve from the approvals page or the assistant (the spec's "Admin role confers no
  decision right unless assigned"). Admins keep view and delegate/escalate. In dev, add the admin to a
  team to test.
- **D4 The approvals page meets the acknowledgement gate with a confirm.** FR-18 says the server refuses an
  unacknowledged approve "regardless of the page", and FR-28 says the approvals page keeps working.
  Without a change, an approver on that page could never approve a contract with open high risks: it has
  no checkbox, so every try would 422. The smallest change that keeps both is to catch that exact 422, ask
  `window.confirm`, and resend once with `acknowledged_open_risks: true`. This is the same pattern the
  request page used for human steps before this feature. The acknowledgement is still explicit and still
  audited. Rejected: (a) the approvals page always sending `true` (it would void FR-18); (b) a full
  checkbox UI there (scope). **Please confirm**: it is a visible change to the approvals page.
- **D5 FR-24 by a client refresh, not a server hook.** This was decided upstream. After a decision the
  card calls the existing `POST /workflows/runs/{id}/refresh`. Rejected: calling `refresh_run` inside the
  decision route, which would change the approvals page and assistant paths and adds engine work to a
  locked transaction. The refresh route needs `intake:read`. Every default role that holds
  `approval:decide` (admin, legal_reviewer, approver) also holds `intake:read`, so the prompt path covers
  every default approver. A custom role without it falls back to the `resume-workflow-runs` beat (every
  2 minutes, `app/jobs/celery_app.py`). On the contract path the decision itself moves the contract on.
  There, a refresh refused because the approver's "pending approver" access has ended is harmless and is
  swallowed.
- **D6 Acknowledgement gate in the route only.** Decided upstream. The assistant's `decide_approval` tool is
  not gated; the `ponytail:` comment at the gate names that ceiling and the upgrade path.
- **D7 The pre-checks in the route (409, then 403) duplicate checks inside `_apply_decision`.** That is
  deliberate. A stale page gets "already decided" rather than "not assigned", and the gate refuses before
  `contract_risk` is computed. The service still re-checks under the row lock, which is what makes a
  concurrent double submit one decision.
- **D8 Chain permission widened from `intake:read` to `intake:create`.** Needed for FR-2: the requester
  sees the chain. Safe because `get_request` already limits non-staff to their own request (404 otherwise).
- **D9 The view is read from the run's own copy of the steps.** A run keeps the steps it started with
  (`run.steps`), so a view changed in the builder applies to runs started afterwards, as every other step
  setting does. AC-15 is tested and verified on a run started after the save.
- **D10 Non-standard clauses = the playbook review's findings (`contract_risk` drivers)**, and top issues =
  the first three of the same list. Rejected: a new "non-standard" classification of `ClauseExtraction`.
  That data does not exist, and creating it would need AI (FR-4).
- Risk: changing `_user_eligible_to_decide`'s signature breaks a test that monkeypatches it with a fixed
  lambda. Only `test_approvals_listing.py` patches `routes._user_eligible_to_decide`, and only through
  `_can_view_approval`, which does not pass `assigned_only`. Keep that call without the keyword.
- FR traceability:
  - FR-1 → `can_decide` in `_serialize_chain` + `ApprovalCard` controls
  - FR-2 → `ApprovalCard` read-only branch + chain permission `intake:create` (D8)
  - FR-3 → `assigned_only` in the route, `decide_in_app` and the lists (D3)
  - FR-4 → `approval_summary.build_summary` (stored rows only)
  - FR-5 → `tag_for` + the source column (D2)
  - FR-6 → `value: null` → "Not available"
  - FR-7 → `current_clauses` (current version, not stale) + renewal version check
  - FR-8 → `snippet` + `quote: true`
  - FR-9 / FR-10 / FR-11 → `PROFILES`
  - FR-12 → the no-contract table
  - FR-13 → `config.summary` default general + `_clean_steps` check
  - FR-14 → `approval_view_for` (config only)
  - FR-15 → the designer select + `workflow.updated` audit
  - FR-16 / FR-17 → the card's comment box + `_apply_decision` strip
  - FR-18 → route gate 422 (+ D4)
  - FR-19 → `open_risk_counts` = `contract_risk` counts
  - FR-20 → no checkbox / no gate when 0
  - FR-21 → `approval.risk_acknowledged`
  - FR-22 → `decisions` + the card's invalidations
  - FR-23 → `can_decide` false after deciding
  - FR-24 → `refreshRun` after the decision (D5)
  - FR-25 / FR-26 → the existing `_apply_decision` + refetch
  - FR-27 → existing gates + the route's 409 pre-check
  - FR-28 → approvals page unchanged except D3/D4
  - FR-29 → no-contract tags `requester-stated`

## Open questions

All resolved by the user on 2026-10-22:

1. **Self-approval (AC-27): keep the existing rule.** Only `approval.requested_by_user_id` is refused; the filer's `requester_user_id` is not added, and the designer's "The requester" option stays.
2. **D2 and D4 confirmed.** A form value copied onto the contract shows "requester-stated" (tags follow `field_sources`), and the approvals page asks once with a confirm, then resends with the acknowledgement.
