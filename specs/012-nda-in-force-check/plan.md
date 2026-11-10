# Implementation Plan: NDA already in force check at intake

Feature ID: 012-nda-in-force-check
Spec: ./spec.md (APPROVED)
Created: 2026-10-27
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

A plain, per-viewer database look-up (no AI, no job) in the `intake` domain, a fixed contract-type list in the
`contracts` domain, one JSON column on `intake_request`, and three UI surfaces (wizard aside, request page card,
contract page + Contracts list prompt for missing types).

```
GET  /intake/nda-check?counterparty_id=&counterparty_name=  ─► app/intake/nda_check.py (NEW) nda_check(user, …)
     (wizard, before filing; writes nothing)                     visible NDAs in force / in progress + live MSAs
POST /intake/requests  (existing, + optional nda_check)      ─► service.create_request → nda_check.record_filing
     (filing; ONE audit + ONE timeline row, same txn)            recompute for the filer ∩ ids the form showed
GET  /intake/requests/{id}/nda-check                         ─► nda_check.request_nda_check(user, request)
     (request page; writes nothing)                              recomputed for THIS viewer + stored filing record
                                                                 re-filtered by THIS viewer
GET  /intake/requests/{id}/counterparty (existing, 011)      ─► screening.visible_counterparty: note says
                                                                 "N NDA in force" (FR-24), not a title count
GET  /contracts/types                                        ─► app/contracts/contract_types.py (NEW) fixed list
GET  /contracts/type-needed                                  ─► ContractService.list_type_needed (visible only)
PUT  /contracts/{id}/type                                    ─► ContractService.set_contract_type (audit+timeline)
GET  /contracts/{id} etc. (existing)                         ─► ContractResponse + computed type_needed/suggested_type
```

- Backend mirrors the 011 shape: thin handlers in `app/intake/routes.py` / `app/contracts/routes.py`, logic in a
  sibling module, Pydantic models in the domain `schemas.py`. Contract visibility reuses
  `accessible_contract_filter(user)` + `contract:read` exactly as `screening.visible_counterparty` does (011 D7: a
  strict subset of the contract page's row check, so every listed contract opens). Name matching reuses
  `normalize_party_name` / `_name_match` and the 011 ILIKE token prefilter. Request visibility reuses
  `IntakeService.get_request`. "Is this an NDA request" reuses `drafting.resolve_doc_type(r) == "nda"` (the rule
  behind `draftable_doc_type`). No new access rule is invented.
- "Type needed" is **derived at read time** from `contract_type`, `title` and `metadata_json` — no column, no
  backfill. The day-one 7 contracts appear on the list without a data migration.
- One migration: `intake_request.nda_check` (JSON, nullable) — the filing record. It cannot live in
  `field_values` (served raw to every viewer, read by AI triage — FR-4/FR-23) or `screening` (overwritten by every
  re-screen).
- No Celery task, no setting, no integration, no `ui.tsx` primitive, no new page route. Frontend: wizard
  (`_agreement-forms.tsx`) aside panel, request page side-column card, contract page `.ctx` prompt row, a
  collapsible "type needed" panel on `/contracts`.

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | `backend/app/intake/models.py` (one column on `IntakeRequest`), `backend/alembic/versions/0075_nda_check.py` (NEW). `backend/app/models.py`: untouched (no new model). |
| backend-dev | `backend/app/contracts/contract_types.py` (NEW), `backend/app/contracts/schemas.py`, `backend/app/contracts/service.py` (`set_contract_type`, `list_type_needed`), `backend/app/contracts/routes.py` (3 handlers), `backend/app/intake/nda_check.py` (NEW), `backend/app/intake/schemas.py`, `backend/app/intake/routes.py` (2 GET handlers), `backend/app/intake/service.py` (`create_request`: one call), `backend/app/intake/screening.py` (`visible_counterparty` NDA wording + shared status helper), `backend/app/intake/activity.py` (2 `EVENTS` rows), `backend/tests/test_contract_types.py` (NEW), `backend/tests/test_nda_check.py` (NEW), `backend/tests/test_request_counterparty.py` (one test's expected note) |
| frontend-dev | `frontend/src/lib/types.ts`, `frontend/src/lib/endpoints.ts` (intakeApi + contractsApi additions), `frontend/src/lib/nda-check.ts` + `nda-check.test.ts` (NEW), `frontend/src/app/(app)/intake/_nda-check.tsx` + `_nda-check.test.tsx` (NEW), `frontend/src/app/(app)/intake/_agreement-forms.tsx`, `frontend/src/app/(app)/intake/_agreement-wizard.test.tsx`, `frontend/src/app/(app)/intake/_request-overview.tsx` + `_request-overview.test.tsx`, `frontend/src/components/contract-type-picker.tsx` + `contract-type-picker.test.tsx` (NEW), `frontend/src/app/(app)/contracts/_type-needed.tsx` + `_type-needed.test.tsx` (NEW), `frontend/src/app/(app)/contracts/page.tsx` (one line: render the panel), `frontend/src/app/(app)/contracts/[id]/_clm-workspace.tsx` + `_clm-workspace.test.tsx` |
| qa-engineer | `specs/012-nda-in-force-check/verification.md` |

Shared-hotspot note:
- `src/lib/types.ts`, `src/lib/endpoints.ts`: T002 only.
- `backend/app/intake/activity.py`: T003 then T004 (serialized). `backend/app/intake/schemas.py`,
  `routes.py`, `service.py`, `screening.py`: T004 only. `backend/app/contracts/*`: T003 only.
- `app/models.py`, `app/jobs/tasks.py`, `src/components/ui.tsx`: untouched.
- Migration: T001 only (one revision; nothing else touches Alembic).

## Interface freeze (contract-first — fixed before implementation starts)

### API contract

All paths under `/api/v1`. GETs write **nothing** (no audit row, no timeline row, no `access.denied` row —
`accessible_contract_filter` is SQL and never logs) (AC-19).

| Method | Path | Permission | Request | Response | Errors |
|---|---|---|---|---|---|
| GET | `/intake/nda-check` | `intake:create` | query `counterparty_id` (str ≤ 36, optional), `counterparty_name` (str ≤ 255, optional) | 200 `NdaCheckResponse` | 403 missing permission; 422 only for over-long query values |
| GET | `/intake/requests/{request_id}/nda-check` | `intake:create` + row gate `IntakeService.get_request` (staff `intake:read`/admin: org queue; else own requests) | none | 200 `RequestNdaCheckResponse` | 403; 404 not found / other org / not the viewer's request |
| POST | `/intake/requests` (existing) | unchanged (`intake:create` + screen `intake` ADD) | `RequestCreate` + optional `nda_check: NdaCheckFiling` | unchanged `RequestResponse` (201) | unchanged — `nda_check` never adds an error (see validators) |
| GET | `/intake/requests/{request_id}/counterparty` (existing) | unchanged | unchanged | unchanged shape; `note` wording changes (FR-24) | unchanged |
| GET | `/contracts/types` | `contract:read` | none | 200 `list[ContractTypeOption]` | 403 |
| GET | `/contracts/type-needed` | `contract:read` | none | 200 `TypeNeededResponse` | 403 |
| PUT | `/contracts/{contract_id}/type` | `contract:update` + screen `contracts` EDIT (`_CONTRACTS_EDIT`) + `get_contract_for_user` | `{"contract_type": "<value from /contracts/types>"}` | 200 `ContractResponse` | 403; 404 (not visible / other org / deleted); 422 value not in the list |
| PATCH | `/contracts/{contract_id}` (existing) | unchanged | `ContractUpdate`; **`contract_type` now refused** | unchanged | + 422 `"Set the contract type with PUT /contracts/{id}/type"` when `contract_type` is present (D5) |
| GET | `/contracts`, `/contracts/{id}` (existing) | unchanged | unchanged | `ContractResponse` + `type_needed`, `suggested_type` (computed) | unchanged |

Route order: `/contracts/types` and `/contracts/type-needed` are declared next to `/contracts/counterparties`,
**before** `/{contract_id}`, or FastAPI matches them as a contract id.

Full shapes:

```json
// GET /api/v1/intake/nda-check?counterparty_id=cp-1&counterparty_name=Initech%20Software%20Ltd  → 200
{
  "matched_by": "register",          // "register" (id resolved in the viewer's org) | "name" | null (no id, no name)
  "in_force": [
    {
      "id": "57f4…",
      "title": "Mutual NDA — Initech Software Ltd",
      "status": "in_force",          // "in_force" | "in_progress"
      "stage": "active",             // Contract.lifecycle_stage as stored
      "kind": "mutual",              // "mutual" | "one_way" | null (= "Type not recorded", FR-12)
      "direction": null,             // "we_share" | "they_share" | null; only ever set when kind == "one_way"
      "end_date": "2026-12-01",      // Contract.expiration_date (ISO date) or null (= "No end date recorded")
      "ending_soon": true,           // status in_force AND end_date AND 0 <= days to end <= 60
      "days_left": 35                // set only when ending_soon, else null
    }
  ],
  "in_progress": [ /* same item shape, status "in_progress", ending_soon false, days_left null */ ],
  "msas": [
    { "id": "a1b2…", "title": "Initech — Master Services Agreement", "end_date": null }
  ]
}
```

- **Nothing visible** ⇒ all three lists `[]` (same object whether nothing exists or everything is hidden, FR-16/26).
  Viewer without `contract:read` ⇒ lists `[]`, `matched_by` still as computed (it depends only on the input).
- `counterparty_id` that does not resolve in the viewer's org (`parties.get(db, org_id=user.org_id,
  kind="counterparty", record_id=…)` is None) ⇒ treated as absent: name mode, never an error (no cross-org probe).

```json
// GET /api/v1/intake/requests/{request_id}/nda-check  → 200
{
  "applies": true,       // resolve_doc_type(r) == "nda" OR r.nda_check is not None; false ⇒ check null, filing null
  "check": { /* NdaCheckResponse as above, for THIS viewer now; null when applies is false or no counterparty */ },
  "filing": {            // null when the request has no stored filing record
    "choice": "continue",
    "reason": "Different purpose: a new research project",   // null when not given
    "at": "2026-10-27T09:12:00+00:00",
    "listed": [          // stored ids re-filtered by THIS viewer's visibility; hidden ones silently dropped
      { "id": "57f4…", "title": "Mutual NDA — Initech Software Ltd", "kind": "nda", "status": "in_force" }
    ]
  }
}
```

```json
// POST /api/v1/intake/requests — the one added key (everything else unchanged)
{
  "type_label": "New agreement · NDA", "field_values": { "request_form": "new_agreement", "agreement_type": "NDA",
    "counterparty_id": "cp-1", "counterparty": "Initech Software Ltd", "...": "..." },
  "nda_check": {                       // sent ONLY when the form displayed ≥ 1 item (any of the three lists)
    "shown_ids": ["57f4…", "a1b2…"],   // every contract id the panel displayed
    "reason": "Different purpose"      // optional; null/blank = none
  }
}
```

```json
// GET /api/v1/contracts/types  → 200 (order = display order)
[
  { "value": "NDA", "label": "NDA" },
  { "value": "MSA", "label": "MSA (master services)" },
  { "value": "Statement of Work", "label": "Statement of Work (SOW)" },
  { "value": "Software / SaaS Agreement", "label": "Software / SaaS" },
  { "value": "Vendor Agreement", "label": "Vendor" },
  { "value": "Consultancy Agreement", "label": "Consultancy" },
  { "value": "DPA", "label": "Data processing (DPA)" },
  { "value": "Customer Agreement", "label": "Customer" },
  { "value": "other", "label": "Other" }
]
// values are exactly the strings drafting already stores (CONTRACT_TYPE_OF_KIND / _FORM + the custom-draft "other"),
// so a person's choice and a drafted contract never differ in spelling (FR-36).

// GET /api/v1/contracts/type-needed  → 200
{
  "items": [
    { "id": "c9…", "title": "Mutual NDA - Umbrella", "counterparty_name": "Umbrella Corp",
      "stage": "active", "contract_type": "other", "suggested_type": null }
  ],
  "truncated": false      // true when more than 500 qualified; newest 500 returned
}

// PUT /api/v1/contracts/{contract_id}/type   request → 200 ContractResponse (incl. type_needed false)
{ "contract_type": "NDA" }

// ContractResponse — two added computed keys
{ "...": "...", "type_needed": false, "suggested_type": null }
```

#### Frozen rules (backend, `app/contracts/contract_types.py` + `app/intake/nda_check.py`)

**Type recognition** — `canonical_contract_type(raw: str | None) -> str | None` (FR-8, FR-31, FR-38). Normalise:
lower-case, every non-`[a-z0-9]` run → one space, strip. Empty ⇒ None. First matching row wins (order matters):

| value | matches normalised text by (`re.search`, word-bounded) |
|---|---|
| `NDA` | `\b(nda|mnda|non disclosure|nondisclosure|confidentiality agreement)\b` |
| `DPA` | `\b(dpa|data processing)\b` |
| `Statement of Work` | `\b(sow|statement of work)\b` |
| `MSA` | `\b(msa|master services? agreement)\b` |
| `Software / SaaS Agreement` | `\b(saas|software)\b` |
| `Consultancy Agreement` | `\b(consultancy|consulting)\b` |
| `Vendor Agreement` | `\b(vendor|supplier)\b` |
| `Customer Agreement` | `\bcustomer agreement\b` |
| `other` | normalised text == `other` |

Examples pinned by tests: "NDA", "Mutual Non-Disclosure Agreement", "Mutual Non-Disclosure Agreement (MNDA)",
"non_disclosure_agreement" → `NDA`; "Master Services Agreement", "MSA" → `MSA`; "Services Agreement" → None;
blank → None; "Other" → `other`. Title is **never** passed to this for recognition (FR-8).

**Type needed** — `type_needed(contract_type, title, metadata_json, lifecycle_stage, archived) -> bool`
(FR-35, FR-37): always `False` when `archived` or `lifecycle_stage == "closed"` (user decision 2026-10-27, spec
FR-37 amended: closed/archived contracts are never listed and never prompted). Otherwise
`True` when `canonical_contract_type(contract_type) is None` (blank or unrecognised), **or** when it is `other`
AND `canonical_contract_type(title)` is a value other than None/`other` AND
`metadata_json.field_sources.contract_type != "user"` (a person who chose Other is not asked again).
**Suggested type** — `suggested_type(...) -> str | None`: only when `type_needed`;
`canonical_contract_type(metadata_json.ai_suggestions.metadata.contract_type)` (the document reading's
suggestion, FR-35), else None. Never the title.

**Status** — `contract_status(c, today) -> "in_force" | "in_progress" | None` (FR-5, FR-6, FR-7):
None when `c.archived` or `c.lifecycle_stage == "closed"` or (`c.expiration_date` and
`c.expiration_date < today`); `"in_force"` when stage `active`; otherwise (`intake`/`drafting`/`review`/
`approval`/`signature`) `"in_progress"`. `today = utcnow().date()` (the app's one today, tests/test_one_today.py).

**The check** — `nda_check(db, user, *, counterparty_id, counterparty_name, exclude_contract_id=None) -> dict`:
1. `row = parties.get(db, org_id=user.org_id, kind="counterparty", record_id=counterparty_id)` when an id is given.
   `name = row.name if row else (counterparty_name or "").strip()`. `matched_by = "register" if row else ("name" if
   name else None)`. None ⇒ return the empty result.
2. If not `has_permission(user.permission_values, "contract:read")` ⇒ empty lists (same as 011).
3. `tokens = normalize_party_name(name).split()`; `by_name = or_(*[Contract.counterparty_name.ilike(f"%{t}%") for t
   in tokens])` (or `false()` when no tokens). Register mode: `who = or_(Contract.counterparty_id == row.id,
   and_(Contract.counterparty_id.is_(None), by_name))`; name mode: `who = by_name` (FR-10).
4. `select(Contract).where(Contract.org_id == user.org_id, accessible_contract_filter(user),
   Contract.deleted_at.is_(None), who)` (+ `Contract.id != exclude_contract_id` when given). Python keeps a row
   when (register mode and `c.counterparty_id == row.id`) or `_name_match(name, c.counterparty_name or "")`.
5. For each kept row: `t = canonical_contract_type(c.contract_type)`, `s = contract_status(c, today)`. `t == "NDA"`
   and `s` ⇒ NDA item in `in_force` / `in_progress`. `t == "MSA"` and `s == "in_force"` ⇒ `msas`. Everything else is
   ignored (FR-9).
6. Kind/direction for NDA items (FR-12, FR-13): one query
   `select(IntakeRequest.contract_id, IntakeRequest.field_values).where(IntakeRequest.org_id == user.org_id,
   IntakeRequest.contract_id.in_(nda_ids))`. Known **only** when the contract was drafted from our NDA template
   — `(c.metadata_json or {}).get("drafted_from_template", {}).get("key") == "nda"`, **or** the linked request's
   `field_values.paper == "Our template"` (contracts drafted before the marker existed; DEC-3) — AND that request's
   `field_values.nda_kind` is `"Mutual"` → `mutual` / `"One-way"` → `one_way`; direction (only for one_way):
   `nda_direction` `"We share"` → `we_share`, `"They share"` → `they_share`, else null. Anything else ⇒ null (never
   guessed; an NDA on their paper reads "Type not recorded" — decision DEC-1).
7. Sort every list by `(end_date is None, end_date or date.max, title.lower())` (FR-14). `ending_soon` /
   `days_left` per the shape above (59 → flagged, 60 → flagged, 61 → not; FR-15, AC-25). No cap (an NDA list per
   counterparty is short; `# ponytail:` note — add a cap if one ever passes ~50).

**Request page** — `request_nda_check(db, user, request) -> dict`: `applies` as above. Counterparty =
`primary_counterparty(request)`; register id = `request.counterparty_id` only when `parties.get(...)` resolves it
AND `normalize_party_name(row.name) == normalize_party_name(primary name)` (parties edited to someone else ⇒ name
mode). `exclude_contract_id = request.contract_id` (never listed against itself). `filing`: from
`request.nda_check`; `listed` = stored entries whose `id` is in
`select(Contract.id, Contract.title).where(Contract.org_id == user.org_id, accessible_contract_filter(user),
Contract.deleted_at.is_(None), Contract.id.in_(stored_ids))` — and `[]` without `contract:read`; title is the
contract's **current** title (the record stores no titles). Order kept as stored.

**Filing** — `record_filing(db, actor, request, filing: NdaCheckFiling | None, http_request_id) -> None`, called
in `create_request` after `db.flush()` and before the commit, inside the request's own transaction (the session
already holds the audit lock from `intake.created`; no second session — CLAUDE.md audit-lock rule):
- Return (store nothing, write nothing) when `filing` is None, or `resolve_doc_type(request) != "nda"`.
- Recompute `nda_check(db, actor, counterparty_id=fv.get("counterparty_id"), counterparty_name=fv.get(
  "counterparty"))` (fv after `_resolve_party_records`), wrapped in `try/except Exception` → log warning and
  return (the check may never break filing, FR-4).
- `listed` = recomputed items (`in_force` and `in_progress` as `kind:"nda"`, `msas` as `kind:"msa"`) whose id ∈
  `filing.shown_ids`. Empty ⇒ return (nothing the filer can see was shown). Only ids the filer can see survive
  the intersection, so a forged id never enters the record (spec audit rule).
- `request.nda_check = {"choice": "continue", "reason": reason, "at": utcnow().isoformat(), "matched_by": …,
  "listed": [{"id", "kind", "status"}]}`.
- `write_audit_log(db, action="intake.nda_check_continued", resource_type="intake_request", resource_id=r.id,
  org_id=actor.org_id, actor_user_id=actor.id, request_id=http_request_id, after={"choice": "continue", "reason":
  reason, "matched_by": …, "listed": [{"contract_id", "kind", "status"}]})` — ids, kind and status only, no titles.
- `write_timeline_event(db, org_id=actor.org_id, resource_type="intake_request", resource_id=r.id,
  event_type="intake.nda_check_continued", title="Filed after the NDA check · chose to continue",
  actor_user_id=actor.id, request_id=http_request_id, details={"choice": "continue", "reason_given": bool})` —
  no contract details in the timeline (the Activity card shows only the title).
- The duplicate-retry early return in `create_request` stays before this (a retry records nothing new).

**Counterparty note** (FR-24, `visible_counterparty`): the title-word `ndas` list is replaced by
`[c for c in contracts if canonical_contract_type(c.contract_type) == "NDA" and contract_status(c, today) ==
"in_force"]` and the note part reads `"{n} NDA in force"` (omitted at 0). The local `ended(c)` becomes
`contract_status(c, today) is None`. Everything else in 011's contract is unchanged.

**Set type** — `ContractService.set_contract_type(*, contract, user, contract_type, request_id) -> Contract`:
422 `"Pick a contract type from the list"` when not a listed value. `old = contract.contract_type`; when
`old == contract_type` and `field_sources.contract_type == "user"` ⇒ return unchanged, write nothing. Else set
`contract_type`, `metadata_json.field_sources.contract_type = "user"` (so a later AI run never overwrites it —
`_persist_metadata` already skips non-blank, non-"ai" values), `updated_by_user_id`; write
`write_audit_log(action="contract.type_changed", resource_type="contract", before={"contract_type": old},
after={"contract_type": new})` and `write_timeline_event(event_type="contract.type_changed", title=…,
details={"from": old, "to": new})` with title `"Contract type set to {label} (was not set)"` (old blank),
`"Contract type changed from {old} to {label}"`, or `"Contract type confirmed as {label}"` (same value, first
person confirmation); commit, refresh. Nothing else on the row is touched — no stage move, no deadline recompute,
no playbook re-pick, no workflow call (FR-40).

**Type-needed list** — `ContractService.list_type_needed(*, user) -> dict`: `select(Contract).where(
Contract.org_id == user.org_id, Contract.deleted_at.is_(None), accessible_contract_filter(user),
Contract.archived.is_(False), Contract.lifecycle_stage != "closed", or_(
Contract.contract_type.is_(None), func.trim(Contract.contract_type) == "", func.lower(Contract.contract_type).
not_in([v.lower() for v in VALUES if v != "other"]))).order_by(Contract.created_at.desc())`, then Python
`type_needed(...)`; first 500, `truncated` if more. Closed and archived contracts are excluded in SQL and again by
`type_needed` (decision DEC-2). The same rule hides the contract-page prompt, since the prompt reads
`ContractResponse.type_needed`.

Audit actions written by each mutation:
- `POST /intake/requests` with a qualifying `nda_check`: `intake.nda_check_continued` (audit) +
  `intake.nda_check_continued` (timeline), exactly once, in the filing transaction. Without one: nothing new.
- `PUT /contracts/{id}/type`: `contract.type_changed` (audit) + `contract.type_changed` (timeline on the contract).
- Cancel on the form: no request call at all ⇒ nothing (FR-18). Every GET: nothing.
- `app/intake/activity.py` `EVENTS` gains `"intake.nda_check_continued": _s(None, "person")` and
  `"contract.type_changed": _s(None, "person")` (title sentence) — required by 011's coverage-guard test.

### Backend Pydantic models

```python
# app/contracts/schemas.py
class ContractResponse(BaseModel):          # existing — two computed fields added
    ...
    @computed_field
    @property
    def type_needed(self) -> bool:
        return ct.type_needed(self.contract_type, self.title, self.metadata_json, self.lifecycle_stage, self.archived)
    @computed_field
    @property
    def suggested_type(self) -> str | None:
        return ct.suggested_type(self.contract_type, self.title, self.metadata_json, self.lifecycle_stage, self.archived)
    # `from app.contracts import contract_types as ct` — module alias so the property names don't shadow the functions

class ContractUpdate(BaseModel):            # existing — validator added
    @field_validator("contract_type")
    @classmethod
    def _no_type_here(cls, v): ...          # v is not None ⇒ ValueError("Set the contract type with PUT /contracts/{id}/type")

class ContractTypeOption(BaseModel):
    value: str
    label: str

class ContractTypeSet(BaseModel):
    contract_type: str = Field(min_length=1, max_length=160)   # membership checked in the service (422)

class TypeNeededItem(BaseModel):
    id: str
    title: str
    counterparty_name: str | None
    stage: str
    contract_type: str | None
    suggested_type: str | None

class TypeNeededResponse(BaseModel):
    items: list[TypeNeededItem]
    truncated: bool

# app/intake/schemas.py
class NdaCheckItem(BaseModel):
    id: str
    title: str
    status: Literal["in_force", "in_progress"]
    stage: str
    kind: Literal["mutual", "one_way"] | None
    direction: Literal["we_share", "they_share"] | None
    end_date: date | None
    ending_soon: bool
    days_left: int | None

class MsaHintItem(BaseModel):
    id: str
    title: str
    end_date: date | None

class NdaCheckResponse(BaseModel):
    matched_by: Literal["register", "name"] | None
    in_force: list[NdaCheckItem]
    in_progress: list[NdaCheckItem]
    msas: list[MsaHintItem]

class NdaFilingItem(BaseModel):
    id: str
    title: str
    kind: Literal["nda", "msa"]
    status: Literal["in_force", "in_progress"]     # as at filing

class NdaFilingRecord(BaseModel):
    choice: Literal["continue"]
    reason: str | None
    at: datetime
    listed: list[NdaFilingItem]

class RequestNdaCheckResponse(BaseModel):
    applies: bool
    check: NdaCheckResponse | None
    filing: NdaFilingRecord | None

class NdaCheckFiling(BaseModel):
    shown_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    # mode="before" validators that never raise (FR-4: the check can't refuse a filing):
    # shown_ids → non-empty strings only, first 200, each cut to 36 chars; reason → strip, cut to 500, "" → None.

class RequestCreate(BaseModel):     # existing — one optional field
    ...
    nda_check: NdaCheckFiling | None = None
```

### Database schema

| Table | Column | Type | Constraints |
|---|---|---|---|
| `intake_request` | `nda_check` | JSON | nullable; no default; no index (only read by id with its row) |

Stored shape (frozen): `{"choice": "continue", "reason": str | null, "at": ISO-8601, "matched_by": "register" |
"name", "listed": [{"id": contract id, "kind": "nda" | "msa", "status": "in_force" | "in_progress"}]}`. Org
scoping: inherited from the row (`intake_request.org_id`, already indexed); every read goes through
`get_request`. Model line in `app/intake/models.py` next to `screening`:
`nda_check = Column(JSON, nullable=True)  # filing record of the NDA check — see intake/nda_check.py`.
It is **not** added to `serialize_request` (never served raw; only via the per-viewer endpoint).

Migration: revision ID **`0075_nda_check`** (14 chars), `down_revision = ("0074_merge_clearance",
"0074_template_sync")` — it merges today's two heads AND adds the column, leaving one head.
`upgrade`: `op.add_column("intake_request", sa.Column("nda_check", sa.JSON(), nullable=True))`.
`downgrade`: `op.drop_column("intake_request", "nda_check")`. After it lands: `make test-db` (re-run migrations
on `legal_clm_test`) and `alembic heads` shows only `0075_nda_check`.

### Frontend TypeScript models (added to src/lib/types.ts)

```ts
// GET /intake/nda-check and the "check" of GET /intake/requests/{id}/nda-check — only what this viewer may see.
export interface NdaCheckItem {
  id: ID;
  title: string;
  status: "in_force" | "in_progress";
  stage: ContractLifecycleStage;
  kind: "mutual" | "one_way" | null;          // null → "Type not recorded"
  direction: "we_share" | "they_share" | null; // only with one_way
  end_date: ISODate | null;                   // null → "No end date recorded"
  ending_soon: boolean;
  days_left: number | null;                   // set only when ending_soon
}
export interface MsaHintItem {
  id: ID;
  title: string;
  end_date: ISODate | null;
}
export interface NdaCheckResult {
  matched_by: "register" | "name" | null;
  in_force: NdaCheckItem[];                   // soonest end first, no end date last
  in_progress: NdaCheckItem[];
  msas: MsaHintItem[];
}
export interface NdaFilingItem {
  id: ID;
  title: string;
  kind: "nda" | "msa";
  status: "in_force" | "in_progress";         // as at filing
}
export interface NdaFilingRecord {
  choice: "continue";
  reason: string | null;
  at: string;
  listed: NdaFilingItem[];                    // only what THIS viewer may see now
}
export interface RequestNdaCheck {
  applies: boolean;
  check: NdaCheckResult | null;
  filing: NdaFilingRecord | null;
}
// Sent inside POST /intake/requests as `nda_check` when the form showed at least one item.
export interface NdaCheckFiling {
  shown_ids: ID[];
  reason: string | null;
}
export interface ContractTypeOption {
  value: string;
  label: string;
}
export interface TypeNeededItem {
  id: ID;
  title: string;
  counterparty_name: string | null;
  stage: ContractLifecycleStage;
  contract_type: string | null;
  suggested_type: string | null;
}
export interface TypeNeededList {
  items: TypeNeededItem[];
  truncated: boolean;
}
// ContractResponse gains (append to the existing interface):
//   type_needed: boolean;
//   suggested_type: string | null;
```

### Endpoint client additions (src/lib/endpoints.ts)

- `intakeApi.ndaCheck(p: { counterparty_id?: string | null; counterparty_name: string }) → Promise<NdaCheckResult>`
  — `GET /intake/nda-check?${new URLSearchParams(...)}` (omit `counterparty_id` when empty).
- `intakeApi.requestNdaCheck(id: string) → Promise<RequestNdaCheck>` — `GET /intake/requests/${id}/nda-check`
  (placed after `counterparty`).
- `contractsApi.types() → Promise<ContractTypeOption[]>` — `GET /contracts/types`.
- `contractsApi.typeNeeded() → Promise<TypeNeededList>` — `GET /contracts/type-needed`.
- `contractsApi.setType(id: string, contract_type: string) → Promise<ContractResponse>` — `PUT /contracts/${id}/type`
  body `{ contract_type }`.
- `intakeApi.create` keeps its `Record<string, unknown>` signature; the wizard adds `nda_check` to the body.

### Frozen helper + component contracts (frontend)

```ts
// src/lib/nda-check.ts
export function hasNdaResult(c: NdaCheckResult | null | undefined): boolean;  // any of in_force/in_progress/msas non-empty
export function shownIds(c: NdaCheckResult): string[];                        // ids of all three lists, in display order
export function kindWords(i: NdaCheckItem): string;
// mutual → "Mutual"; one_way + we_share → "One-way · we share information with them";
// one_way + they_share → "One-way · they share information with us"; one_way + null → "One-way · direction not recorded";
// null → "Type not recorded"
export function endWords(end: string | null): string;       // "Ends 12 Mar 2027" (fmtDate) | "No end date recorded"
export function endingSoonWords(days: number): string;      // 0 → "Ends today", 1 → "Ends in 1 day", n → "Ends in n days"

// src/app/(app)/intake/_nda-check.tsx
export function NdaCheckList(p: { check: NdaCheckResult }): JSX.Element;
// in_force section → in_progress section → MSA hint; or the quiet "No NDA in force found" line; plus
// "Matched by name" note when matched_by === "name". Links open in a new tab (form answers never lost).
export function NdaCheckPanel(p: {
  status: "loading" | "error" | "ready";
  data: NdaCheckResult | undefined;
  continued: boolean;
  reason: string;
  onContinue: () => void;
  onReason: (v: string) => void;
  onCancel: () => void;
}): JSX.Element;
export function NdaCheckCard(p: {
  status: "loading" | "error" | "ready";
  data: RequestNdaCheck | undefined;
  onRetry: () => void;
}): JSX.Element | null;

// src/components/contract-type-picker.tsx
export function ContractTypePicker(p: {
  contractId: string;
  current: string | null;          // preselects suggested ?? (current if listed) ?? ""
  suggested: string | null;
  onSaved?: (c: ContractResponse) => void;
}): JSX.Element;
// Select (from @/components/ui) of contractsApi.types() (query key ["contract-types"], staleTime Infinity) + Button
// "Save type" (disabled until a value is picked); on success invalidates ["contract", id], ["contracts-type-needed"],
// ["contracts-list"] and toasts "Contract type saved"; on error toasts the server message.

// src/app/(app)/contracts/_type-needed.tsx
export function TypeNeededPanel(): JSX.Element | null;      // null while loading, on error, or with 0 items
```

## Component design

### Backend
- Models: `app/intake/models.py` — `IntakeRequest.nda_check` (T001).
- `app/contracts/contract_types.py` (NEW, ~80 lines): `CONTRACT_TYPES` (list of `{value,label}`), `VALUES`,
  `_RULES` (the recognition table), `canonical_contract_type`, `type_needed`, `suggested_type`, `label_of`,
  `contract_status`. Pure functions, no DB. A `__main__` assert self-check is not needed — `test_contract_types.py`
  pins them.
- `app/contracts/schemas.py`: computed fields + validator + 4 models above.
- `app/contracts/service.py`: `set_contract_type`, `list_type_needed` (org + `accessible_contract_filter` + not
  deleted on every query; audit as frozen). `update_contract_metadata` is unchanged (PATCH refusal lives in the
  schema, so service-level callers/tests are unaffected).
- `app/contracts/routes.py`: `GET /types` and `GET /type-needed` (`require_permission("contract:read")`, placed
  before `/{contract_id}`); `PUT /{contract_id}/type` (`require_permission("contract:update")`,
  `_CONTRACTS_EDIT`, `get_contract_for_user` first).
- `app/intake/nda_check.py` (NEW, ~170 lines): `nda_check`, `request_nda_check`, `record_filing`, `_kind_map`.
  Imports `normalize_party_name`, `_name_match`, `primary_counterparty` from `screening`; `screening` imports
  `contract_status`/`canonical_contract_type` from `app.contracts.contract_types` (no cycle).
- `app/intake/schemas.py`: models above; `RequestCreate.nda_check`.
- `app/intake/routes.py`: `GET /nda-check` (`current_user=Depends(_CREATE)`, query params with `Query(None,
  max_length=…)`, `response_model=NdaCheckResponse`); `GET /requests/{id}/nda-check` (`_CREATE`, `get_request`
  first, `response_model=RequestNdaCheckResponse`). Both call `nda_check` functions with `intake_service.db`.
- `app/intake/service.py` `create_request`: one call `record_filing(db, actor, r,
  getattr(payload, "nda_check", None), request_id)` right after the attachments loop, before `intake.created`
  is written (order irrelevant inside the txn; one commit as today). Lazy import inside the method.
- `app/intake/screening.py`: FR-24 wording + `contract_status` reuse.
- `app/intake/activity.py`: two `EVENTS` rows.
- Celery: none. Settings: none (no `validate_runtime_settings()` entry, no phase-10 fixture change).
- FR-34 (drafted type): **no code change**. Verified: `draft_contract_for_request` passes
  `TEMPLATES[doc]["contract_type"]` ("NDA" for the NDA template) and `apply_request_facts` sets
  `CONTRACT_TYPE_OF_KIND[agreement_type]` with source `form`; custom drafts get `"other"` (then the kind's type
  when it has one, e.g. "Customer Agreement"). The metadata job only fills blanks or its own values, so a form
  type is never overwritten. AC-27 is pinned by a test. Contracts made from a request's attachment
  (`ingest_attachment_as_contract`) on a form answer with no type are "uploaded" and fall under FR-35.

### Frontend (Next.js page/component tree)

- `src/app/(app)/intake/page.tsx` → `AgreementWizard` (`_agreement-forms.tsx`, CHANGED):
  - `const cpName = String(values.counterparty ?? "").trim(); const ndaOn = def.key === "new_agreement" &&
    values.agreement_type === "NDA" && !!cpName;`
  - `ndaQ = useQuery({ queryKey: ["nda-check", values.counterparty_id ?? null, cpName], queryFn: () =>
    intakeApi.ndaCheck({ counterparty_id: (values.counterparty_id as string) || null, counterparty_name: cpName }),
    enabled: ndaOn, retry: false, staleTime: 30_000 })` — not part of `body` (the approval-preview key is untouched).
  - State `ndaContinued` (bool), `ndaReason` (string) — kept across steps and counterparty changes (typed text is
    never lost).
  - Aside: `{ndaOn && <NdaCheckPanel status=… data={ndaQ.data} … onCancel={onBack} />}` as the FIRST aside block on
    every step (incl. Review). Hidden when `ndaOn` is false (FR-2).
  - `submit()`: unchanged flow; when `ndaOn && hasNdaResult(ndaQ.data)` add `nda_check: { shown_ids:
    shownIds(ndaQ.data!), reason: ndaReason.trim() || null }` to the create payload. Never awaits or refetches the
    check; a loading/failed check sends nothing (FR-4).
  - `_nda-check.tsx` (NEW): `NdaCheckList`, `NdaCheckPanel`, `NdaCheckCard` — Tailwind with `slate-*` and semantic
    tokens only, primitives `Badge`, `Button`, `Input` from `@/components/ui` (all exported today).
    - Panel box: `rounded-xl border border-slate-200 bg-slate-100 p-4 shadow-card` (same as the aside cards), heading
      "NDA check". loading → dim "Checking for an NDA already in force…"; error → "The NDA check could not load.
      You can still file." (one line); ready + nothing → `NdaCheckList` quiet line.
    - With a result: `NdaCheckList`, then (only when `hasNdaResult`) actions row: `Button variant="outline"`
      "Cancel request" (→ `onCancel`), `Button` "Continue" (→ `onContinue`; hidden once continued); when continued:
      `Input` "Why carry on? (optional)" `maxLength={500}` placeholder "e.g. different purpose" + dim line
      "You can file as usual."
    - List item: title as `<a href="/contracts/{id}" target="_blank" rel="noopener noreferrer">` (brand link
      colour), then `kindWords` · `endWords`; ending soon → `Badge tone="amber"` "Ending soon" + `endingSoonWords`.
      In-force section heading "NDA in force" (`Badge tone="green"`), in-progress heading "In progress, not yet in
      force" (`Badge tone="blue"`) + stage label, MSA hint in a `border-dashed border-slate-300` box headed "Has
      confidentiality terms: check whether it covers this" (no badge, `text-slate-600`, never the word "NDA in
      force"). Empty: `text-slate-500` "No NDA in force found". `matched_by === "name"`: dim "Matched by the
      counterparty's name."
- `src/app/(app)/intake/_request-overview.tsx` (CHANGED): `ndaQ = useQuery({ queryKey: ["intake-nda-check", id],
  queryFn: () => intakeApi.requestNdaCheck(id) })`; side column renders `<NdaCheckCard … />` directly ABOVE
  `CounterpartyCard`. `NdaCheckCard` uses the page's `.card > .ch / .cb` classes (h2 "NDA check") with
  `NdaCheckList` inside: returns null while loading, when `!applies`, or when `check` and `filing` are both null;
  error → "The NDA check could not be loaded." + Retry. Filing block under the list (border-top): "The requester
  saw an NDA check when filing and chose to continue." + "Reason: …" when given + "They were shown:" and each
  `listed` item (link, "NDA"/"MSA", "in force"/"in progress" at filing) only when `listed` is non-empty.
- `src/app/(app)/contracts/[id]/_clm-workspace.tsx` (CHANGED): `const canSetType = contract?.type_needed &&
  (user.permissions.includes("contract:update") || user.permissions.includes("*")) && canEdit`
  (`useScreenAccess("contracts")`). When true, a `.ctx` row (same pattern as the negotiation row) after the
  lifecycle `nav`: text "**This contract has no recognised type.** Set it so checks like the NDA check can find
  it." + `<ContractTypePicker contractId={id} current={contract.contract_type} suggested={contract.suggested_type}
  />`. On save the contract query refetches and the row disappears.
- `src/app/(app)/contracts/page.tsx` (CHANGED, one line): `<TypeNeededPanel />` between the briefing (`.top`) and
  the toolbar.
  - `_type-needed.tsx` (NEW): query `["contracts-type-needed"]` → `contractsApi.typeNeeded()`; header button
    "{n} contract(s) need a type" + dim "so the NDA check can find them", collapsed by default; open → rows: title
    `next/link` to `/contracts/{id}`, counterparty (or "—"), stage (title-cased), current type ("blank" when null),
    and `ContractTypePicker` when the viewer can set types (same rule as above), else dim "Ask someone who can
    edit contracts". `truncated` → dim "Showing the newest 500." Tailwind, `slate-*` only.
- `src/components/contract-type-picker.tsx` (NEW): as frozen. Shared by the two contract surfaces (hence
  `components/`, like `lifecycle-view.tsx`; not a `ui.tsx` primitive).
- New shared primitives in `src/components/ui.tsx`: none. Imported from `@/components/ui`: `Badge`, `Button`,
  `Input`, `Select` — all exported today (verified).
- Demo-mode behavior: none — no `src/lib/demo.ts` exists in this checkout; the pages have no demo path.

## Test strategy

### Backend pytest (`docker compose exec backend python -m pytest tests/test_nda_check.py tests/test_contract_types.py tests/test_request_counterparty.py tests/test_request_activity.py -q`, against `legal_clm_test`)

Fixtures follow `tests/test_request_counterparty.py` (rolled-back connection, own org, patched
`permission_values`, walls via `EthicalWall`/`EthicalWallPrincipal`). Each test's docstring names the failure mode.

`tests/test_nda_check.py` (T004):
- status: active future end → in_force; active no end → in_force, `end_date` null; closed, archived, active ended
  yesterday → absent; review stage → `in_progress`, separate list; in-progress ended → absent (AC-3, AC-4, AC-5).
- recognition: "NDA" + "Mutual Non-Disclosure Agreement (MNDA)" listed; blank type titled "Non-Disclosure
  Agreement" and "Services Agreement" titled "…NDA…" not listed (AC-18, AC-31).
- order + ending soon: three in force (end in 61 days, 59 days, none) → order 59, 61, none; only 59 `ending_soon`
  with `days_left` 59 (AC-8, AC-9, AC-25).
- kind: template-drafted NDA with request `nda_kind` Mutual → `mutual`; One-way/They share → `one_way`/`they_share`;
  uploaded NDA (no `drafted_from_template`) whose request says Mutual → `null` (AC-7).
- matching: register mode lists linked contract with a different stored name + unlinked same-name contract, not a
  same-name contract linked to ANOTHER register row; name mode (no id) lists by name only, `matched_by` "name";
  foreign-org register id → name mode (AC-24, AC-16).
- visibility: only NDA behind a wall → response byte-identical (`==`) to a counterparty with no contracts, for the
  form endpoint and the request endpoint (AC-10); same for a walled live MSA with no NDA (AC-23); other org's NDA
  with the same name never listed (AC-16); viewer without `contract:read` → empty lists; every listed id passes
  `get_contract_for_user` for the viewer (AC-17).
- MSA: live MSA + no NDA → `in_force == []`, `msas` has it; ended/archived/in-review MSA absent; both → both
  lists filled (AC-20, AC-21, AC-22).
- request page: request's own contract never listed; after the NDA's `expiration_date` is moved to yesterday the
  re-read shows it gone (AC-13); `applies` false for an MSA request (check/filing null).
- filing (via `client.post("/api/v1/intake/requests")`): with `nda_check` and a visible NDA → `nda_check` column
  stored, exactly ONE `audit_log` row `intake.nda_check_continued` and ONE timeline row; reason stored/`null`;
  `shown_ids` containing a walled contract id → that id absent from the column and the audit row (AC-12, AC-19);
  without `nda_check` → no row, column null; `nda_check` with a 2,000-char reason and 500 ids → 201 (truncated,
  never refused) (FR-4); filing with an `nda_check` that matches nothing → 201, nothing stored (AC-26).
- request page filing record: lawyer behind a wall on the listed NDA sees `filing.choice`/`reason` and
  `listed == []` (AC-14); requester sees their own record.
- no writes on read: `audit_log` / `resource_timeline_event` counts equal before/after both GETs (AC-19).
- 403 without `intake:create`; request endpoint 404 for another member's request and another org's.

`tests/test_contract_types.py` (T003):
- `canonical_contract_type` table (every example in the frozen list); `type_needed` cases: blank, "Services
  Agreement", "other" + NDA title (source not user) → True; "other" + NDA title + source user → False; "NDA" → False;
  blank type + stage `closed` → False; blank type + `archived` → False (DEC-2).
- `GET /contracts/types` returns the frozen list in order.
- `test_closed_or_archived_blank_type_is_not_listed`: a blank-type closed contract and a blank-type archived (active)
  contract are absent from `GET /contracts/type-needed`, and `GET /contracts/{id}` returns `type_needed: false` for
  both (so no contract-page prompt), while an open blank-type contract beside them is listed (AC-29, DEC-2).
- `GET /contracts/type-needed`: blank-type and other-titled-NDA contracts listed; a walled one and another org's not
  listed (AC-29); `truncated` past 500 (cheap: patch the cap constant).
- `PUT /contracts/{id}/type` blank → "NDA": 200, `type_needed` false, exactly one `contract.type_changed` audit
  (`before`/`after` old/new, `actor_user_id`) and one timeline row on the contract; every other column
  (stage, dates, value, owner, archived, review_playbook_id, …) unchanged (AC-30); then it appears in
  `nda_check` (AC-28); value not in list → 422; walled → 404; no `contract:update` → 403; repeat same value → no
  new rows.
- after a person sets the type, `_persist_metadata` with a high-confidence different `contract_type` leaves it
  (person wins).
- `PATCH /contracts/{id}` with `contract_type` → 422; PATCH without it unchanged.
- AC-27: drafting a new_agreement NDA request (`draft_contract_for_request`, mocks on) → `contract_type == "NDA"`.

`tests/test_request_counterparty.py` (T004): `test_own_contract_and_other_counterparties_are_excluded_and_nda_counted`
→ contract "Mutual NDA - Initech" gets `contract_type="NDA"`; expected note `"Known counterparty: 2 prior
contract(s), 1 NDA in force."`; add: ended NDA only → no NDA part in the note (AC-15).

`tests/test_request_activity.py`: unchanged; its coverage guard now passes only with the two new `EVENTS` rows.

### Frontend vitest (`npm run typecheck && npm run test` from `frontend/`)

- `lib/nda-check.test.ts` (T005): `kindWords` 5 cases, `endWords`, `endingSoonWords` 0/1/n, `hasNdaResult`,
  `shownIds` order.
- `intake/_nda-check.test.tsx` (T005): list order and sections (in force → in progress → MSA hint), "Type not
  recorded", direction words, "Ending soon" + "Ends in 35 days", "No end date recorded", MSA box has no "NDA in
  force" text (AC-20/22), empty → "No NDA in force found" with no count; name note; links `href="/contracts/{id}"`
  `target="_blank"`; panel loading/error lines; Cancel calls `onCancel`; Continue reveals the reason input;
  card: null when `applies` false, filing block shows reason and only given `listed`, error + Retry.
- `intake/_agreement-wizard.test.tsx` (T005; mocks gain `ndaCheck: vi.fn()`): NDA + counterparty → panel with the
  NDA; switching kind to "Services (MSA)" hides it; changing counterparty refetches with the new key (AC-1);
  `ndaCheck` rejecting → error line, Submit still calls `create` without `nda_check` (AC-2); Cancel request →
  `onBack` called, `create` never called (AC-11, AC-26); Continue + reason → `create` payload carries `nda_check:
  { shown_ids, reason }`; no reason → `reason: null`; typed answers unchanged after the panel appears.
- `intake/_request-overview.test.tsx` (T007): mock `requestNdaCheck`; card sits above "Counterparty" in the side
  column; `applies:false` → no "NDA check" heading; existing layout assertion updated for the new side card.
- `components/contract-type-picker.test.tsx` (T006): options from mocked `types()`, Save disabled until picked,
  calls `setType(id, value)`, preselects `suggested`.
- `contracts/_type-needed.test.tsx` (T006): hidden at 0 items; header count; rows show title link, counterparty,
  stage, "blank"; picker only with permission.
- `contracts/[id]/_clm-workspace.test.tsx` (T006): prompt row when `type_needed` + `contract:update`; absent
  without the permission or when `type_needed` false (the server returns false for closed/archived contracts —
  the client adds no rule of its own).

### Acceptance verification (qa-engineer, T008)

| AC | Evidence |
|---|---|
| AC-1 | wizard vitest + browser: New agreement → NDA → pick Initech (dev data has NDAs) → panel; switch kind → gone |
| AC-2 | wizard vitest (rejecting check) + browser with the endpoint blocked (devtools request blocking) → filing works |
| AC-3, AC-4, AC-5 | `test_nda_check` status cases |
| AC-6, AC-17 | `_nda-check` vitest + browser click-through of every listed link |
| AC-7 | `test_nda_check` kind cases + `_nda-check` vitest |
| AC-8, AC-9, AC-25 | `test_nda_check` order/ending-soon + vitest wording |
| AC-10, AC-23 | `test_nda_check` byte-identical cases + browser as a walled user (wall on one Initech NDA in Admin → Walls) |
| AC-11, AC-26 | wizard vitest + DB counts (`intake_request`, `audit_log`) before/after a browser cancel |
| AC-12, AC-19 | `test_nda_check` filing cases + `psql` of the new request's `nda_check` and its single audit row |
| AC-13, AC-14 | `test_nda_check` request-page cases + browser as the assigned lawyer |
| AC-15 | `test_request_counterparty` ended-NDA case |
| AC-16 | `test_nda_check` other-org case |
| AC-18, AC-31 | `test_nda_check` + `test_contract_types` recognition cases |
| AC-20, AC-21, AC-22 | `test_nda_check` MSA cases + `_nda-check` vitest + browser on a counterparty with a live MSA |
| AC-24 | `test_nda_check` matching cases |
| AC-27 | `test_contract_types` drafting case + browser: draft an NDA request, contract page shows type NDA, no prompt |
| AC-28, AC-30 | `test_contract_types` + browser: set one contract to NDA, it leaves the list and appears in the check; audit/timeline rows via `psql` |
| AC-29 | `test_contract_types` list cases (walled one absent) + `test_closed_or_archived_blank_type_is_not_listed`; browser: `/contracts` panel lists the dev blank/other-titled contracts that are not closed or archived (count checked against `psql` with `archived = false AND lifecycle_stage <> 'closed'`), and a closed one shows no prompt on its contract page |
| (all UI) | light + dark screenshots of the panel, card, prompt row and list (no `bg-white`, no hex) |

## Risks & decisions

- **D1 — One JSON column for the filing record (chosen)** vs `field_values` / `screening` (rejected). `field_values`
  is served raw by `serialize_request` to every viewer and read by AI triage, so a stored list would leak titles
  (FR-23/26) and could change triage (FR-4); `screening` is overwritten by every re-screen. A new table (rejected)
  is more moving parts for a one-per-request record.
- **D2 — "Type needed" derived at read time (chosen)** vs a stored flag + backfill (rejected). The rule is a pure
  function of `contract_type`, `title`, `metadata_json`; a flag would need writing on every type change, AI run
  and upload, and a backfill for the 7 day-one contracts. Exposed as computed fields on `ContractResponse`, so
  every contract read carries it without route changes.
- **D3 — Stored list = server recompute for the filer ∩ ids the form showed (chosen).** Trusting client titles/ids
  (rejected) lets a forged id or a since-hidden contract into the record; recompute-only (rejected) can record
  items the requester never saw. The intersection is both "what the filer saw" and "only what the filer may see".
  The record stores ids/kind/status only; titles are read at view time for the viewer.
- **D4 — Kind/direction only for NDAs drafted from our template** (request answer + `drafted_from_template.key ==
  "nda"`, or the linked request's `paper == "Our template"` — DEC-3). For their paper the request's answer is what was asked for, not what the document says, so it reads
  "Type not recorded" (FR-12 "never guess"). Confirmed by the user (DEC-1).
- **D5 — PATCH `/contracts/{id}` refuses `contract_type`; the only way a person sets it is `PUT .../type`.** One
  path enforces the fixed list (FR-36) and writes the old/new history row (FR-39). No frontend sends
  `contract_type` on PATCH today (grep: renewals/obligations send dates only). Rejected: validating PATCH against
  the list too — a second path that writes a generic "Contract details updated" row without old/new.
- **D6 — Fixed list values = the spellings drafting already stores** ("NDA", "MSA", "Statement of Work",
  "Software / SaaS Agreement", "Vendor Agreement", "Consultancy Agreement", "DPA", "Customer Agreement", "other").
  "Customer Agreement" is included because "Selling to a customer" drafts store it — leaving it out would flag our
  own drafts as "type needed". "Confidentiality agreement" is recognised as an NDA spelling (FR-8).
- **D7 — AI metadata path unchanged.** It still fills blank types; an unrecognised AI value simply reads as "type
  needed". Its existing `contract.ai_metadata_extracted` audit (before/after) and timeline row cover an AI fill;
  `contract.type_changed` is for people (FR-39 context). Person-wins already holds (`field_sources == "user"`).
  The approvals NDA fast lane's own `_NDA_TYPES` set is out of scope and untouched.
- **D8 — Single merge+change migration `0075_nda_check`** with a tuple `down_revision`, rather than a separate no-op
  merge first: one file, one head after it, working downgrade.
- **D9 — Visibility = `accessible_contract_filter` + `contract:read` (011 D7).** Stricter than the contract page
  (no pending-approver allowance), so listed ⇒ opens (FR-27); never a leak.
- **R1 — Residual inference via the filing fact (accepted by AC-14).** A lawyer who can see none of the listed
  contracts still sees "the requester saw an NDA check and chose to continue", which implies something visible to
  the requester existed. The spec requires showing the fact (FR-23/AC-14); no title, count, date or kind is shown.
- **R2 — Timing differences (FR-26).** The same queries run whether or not hidden rows exist (filtering is in
  SQL); no extra round-trip on a hit. Not measured below query-plan noise.
- **R3 — Prompt during upload processing.** A just-uploaded contract reads "type needed" until the metadata job
  fills the type (seconds). A person setting it first wins; acceptable.
- **R4 — Reason text is free text** and may name a contract; it is shown only to people who may see the request
  (spec permits).
- **R5 — Dirty working tree**: 009–011 files are uncommitted; agents edit the working copy.
- FR traceability:

| FR | Design component |
|---|---|
| FR-1 | wizard `ndaOn` + `ndaQ` + `NdaCheckPanel`; `GET /intake/nda-check` |
| FR-2 | query key on counterparty id/name; panel hidden when not NDA or no counterparty |
| FR-3 | `nda_check.py` pure SQL/Python, no AI, reads no AI output (kind comes from form answers) |
| FR-4 | check not in `body`; `retry:false`; submit never waits; `NdaCheckFiling` validators never raise; `record_filing` try/except; routing/triage read nothing new |
| FR-5, FR-6, FR-7 | `contract_status` |
| FR-8, FR-38 | `canonical_contract_type` NDA rule (type only) |
| FR-9 | step 5 of the check ignores every non-NDA/MSA type |
| FR-10 | register/name modes, `matched_by`, "Matched by the counterparty's name" |
| FR-11 | `NdaCheckItem` + `NdaCheckList` |
| FR-12, FR-13 | `_kind_map` (template-drafted only) + `kindWords`; no fit wording anywhere |
| FR-14 | sort key |
| FR-15 | `ending_soon`/`days_left` + `endingSoonWords` |
| FR-16 | empty result identical; quiet line, no count |
| FR-17, FR-18 | panel Cancel (`onBack`, no request call) / Continue; filing always enabled |
| FR-19 | optional reason input, truncating validator |
| FR-20 | `record_filing` + `intake_request.nda_check` |
| FR-21 | `NdaCheckCard` on the request page; `GET /intake/requests/{id}/nda-check` |
| FR-22 | `request_nda_check` recomputes per call; no stored list replayed as the check |
| FR-23 | `filing` block; `listed` re-filtered per viewer |
| FR-24 | `visible_counterparty` note "N NDA in force" via `contract_status` + `canonical_contract_type` |
| FR-25, FR-26, FR-27, FR-28 | `accessible_contract_filter` + `contract:read` + org filter on every query; identical empty shape; audit/timeline carry no titles; GETs write nothing |
| FR-29, FR-30, FR-31, FR-32, FR-33 | `msas` list (in force only, MSA rule, same filters); MSA hint box after the NDA sections, never "NDA in force"; recorded as `kind:"msa"` |
| FR-34 | existing drafting path (verified) + AC-27 test |
| FR-35 | computed `type_needed`/`suggested_type`; contract page `.ctx` prompt + `ContractTypePicker` |
| FR-36 | `GET /contracts/types`, `PUT .../type` membership check, PATCH refusal (D5) |
| FR-37 | `GET /contracts/type-needed` (not closed, not archived) + `TypeNeededPanel` on `/contracts` |
| FR-39 | `contract.type_changed` audit (before/after, actor) + contract timeline row |
| FR-40 | `set_contract_type` touches only `contract_type`, `field_sources`, `updated_by` |

## Task split (proposal for /tasks)

| Task | Agent | Files | dependsOn | Wave |
|---|---|---|---|---|
| T001 Column + migration `0075_nda_check` | db-engineer | `app/intake/models.py`, `alembic/versions/0075_nda_check.py` | — | 1 |
| T002 [P] Frontend contract: types + client wrappers | frontend-dev | `src/lib/types.ts`, `src/lib/endpoints.ts` | — | 1 |
| T003 [P] Contract types: module, schemas, service, routes, activity row, tests | backend-dev | `app/contracts/contract_types.py`, `schemas.py`, `service.py`, `routes.py`, `app/intake/activity.py`, `tests/test_contract_types.py` | — | 1 |
| T004 NDA check: module, intake schemas/routes/service/screening, activity row, tests | backend-dev | `app/intake/nda_check.py`, `schemas.py`, `routes.py`, `service.py`, `screening.py`, `activity.py`, `tests/test_nda_check.py`, `tests/test_request_counterparty.py` | T001, T003 | 2 |
| T005 [P] Wizard panel + shared NDA components | frontend-dev | `src/lib/nda-check.ts`(+test), `intake/_nda-check.tsx`(+test), `intake/_agreement-forms.tsx`, `intake/_agreement-wizard.test.tsx` | T002 | 2 |
| T006 [P] Contract type picker, type-needed panel, contract page prompt | frontend-dev | `components/contract-type-picker.tsx`(+test), `contracts/_type-needed.tsx`(+test), `contracts/page.tsx`, `contracts/[id]/_clm-workspace.tsx`(+test) | T002 | 2 |
| T007 Request page card wiring | frontend-dev | `intake/_request-overview.tsx`(+test) | T005 | 3 |
| T008 Verification | qa-engineer | `specs/012-nda-in-force-check/verification.md` | T004, T006, T007 | 4 |

Backend gate: `ruff check .` → `alembic upgrade head` (one head `0075_nda_check`) → `pytest -q`; `make test-db`
after T001. New modules ⇒ `docker compose restart backend` before the live check. After T007 the orchestrator
regenerates the repo graph.

## Decisions (answered by the user, 2026-10-27)

- **DEC-1 — NDA on their paper reads "Type not recorded".** An NDA that became the contract from the
  counterparty's own document shows "Type not recorded", even when the requester ticked "Mutual" or "One-way" on
  the form. Kind and direction are shown only for NDAs drafted from our NDA template (frozen rule step 6, D4).
- **DEC-2 — The "type needed" list shows only contracts that are not closed and not archived** (spec FR-37
  amended the same day). `type_needed(...)` returns false for closed or archived contracts, so they are left out of
  `GET /contracts/type-needed` (also filtered in SQL) and the contract page shows them no prompt. Pinned by
  `test_closed_or_archived_blank_type_is_not_listed` and the AC-29 mapping.

- **DEC-3 — An NDA also counts as drafted from our template when its linked request answered "Our template"**
  (user decision 2026-10-27, after the live check). Contracts drafted before the `drafted_from_template` marker
  was added (mid-October) have no marker, so every dev NDA read "Type not recorded" although the request said
  Mutual on our paper. The kind rule (frozen rule step 6, D4) now accepts the marker OR `paper == "Our template"`
  on the linked request. "Their paper" and uploaded NDAs still read "Type not recorded" (DEC-1 unchanged). Task
  T010.

## Open questions

None.
