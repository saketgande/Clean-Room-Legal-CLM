# Implementation Plan: Live request summary and extra instructions on the agreement form

Feature ID: 013-live-request-summary
Spec: ./spec.md (APPROVED)
Created: 2026-10-27
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

The sentences and the smart-edit matching are **plain, deterministic browser code** (no AI, no job, no new
setting). The server only stores and serves what the filing sends, after checking it. Two JSON columns, one GET
endpoint, one optional key on the existing filing and draft payloads.

```
agreement_forms.json ──GET /intake/forms──► wizard (_agreement-forms.tsx)
                                              │  values (answers)
                                              ▼
                     src/lib/request-summary.ts  buildSummary(form, values, ctx) → SummaryLine[]   (FR-1..8)
                     src/lib/summary-edits.ts    checkLine / acceptOffer / declineOffer / reconcile /
                                                 finalize / parseDate / parseMoney / matchChoice  (FR-13..27)
                                              │  SummaryBoxState (edits, offers, instructions, accepted, pending)
              ┌───────────────────────────────┼───────────────────────────────┐
  POST/PUT /intake/drafts (existing)          POST /intake/requests (existing)
  + summary_box (stored as given)             + summary {text, instructions, accepted}
  → intake_draft.summary_box (NEW col)        → app/intake/request_summary.py record_summary(...)
                                                 (same txn as the filing; ONE audit + ONE timeline row,
                                                  only when instructions or accepted answer changes exist)
                                              → intake_request.filed_summary (NEW col)
GET /intake/requests/{id}/summary (NEW)  ──►  request_summary.read(...) after IntakeService.get_request
                                              (exactly the request's own visibility; writes nothing)
                                              → request page, "What was asked" card (_request-summary.tsx)
```

- Mirrors spec 012's shape: a sibling module `app/intake/request_summary.py` (like `nda_check.py`) holding the
  filing record + the read; thin handler in `app/intake/routes.py`; Pydantic models in `app/intake/schemas.py`;
  one call in `IntakeService.create_request` right after `record_filing`; one `EVENTS` row in `activity.py`.
- Request visibility reuses `IntakeService.get_request` (org + staff `intake:read`/admin, else own request) — the
  same gate as `/requests/{id}`, `/activity`, `/nda-check`. No new access rule (FR-39, AC-28, AC-31).
- The sentence wording lives next to the wizard's other per-question UI text (the `UI[k].uses` precedent), in
  `src/lib/request-summary.ts`, guarded by a test that reads `agreement_forms.json` exactly like
  `agreement-forms.test.ts` does (D1).
- No Celery task, no integration, no setting, no `ui.tsx` primitive, no new page route.

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | `backend/app/intake/models.py` (2 columns: `IntakeRequest.filed_summary`, `IntakeDraft.summary_box`), `backend/alembic/versions/0076_request_summary.py` (NEW). `backend/app/models.py`: untouched (no new model). |
| backend-dev | `backend/app/intake/request_summary.py` (NEW), `backend/app/intake/schemas.py`, `backend/app/intake/routes.py` (1 GET handler), `backend/app/intake/service.py` (`create_request`: one call), `backend/app/intake/drafts.py` (`serialize`/`save`: `summary_box`), `backend/app/intake/activity.py` (1 `EVENTS` row), `backend/tests/test_request_summary.py` (NEW), `backend/tests/test_intake_drafts.py` (2 tests added) |
| frontend-dev | `frontend/src/lib/types.ts`, `frontend/src/lib/endpoints.ts` (`intakeApi.requestSummary`), `frontend/src/lib/request-summary.ts` + `request-summary.test.ts` (NEW), `frontend/src/lib/summary-edits.ts` + `summary-edits.test.ts` (NEW), `frontend/src/app/(app)/intake/_summary-box.tsx` + `_summary-box.test.tsx` (NEW), `frontend/src/app/(app)/intake/_agreement-forms.tsx`, `frontend/src/app/(app)/intake/_agreement-wizard.test.tsx`, `frontend/src/app/(app)/intake/_request-summary.tsx` + `_request-summary.test.tsx` (NEW), `frontend/src/app/(app)/intake/_request-asked.tsx` (one optional prop), `frontend/src/app/(app)/intake/_request-overview.tsx` + `_request-overview.test.tsx` |
| qa-engineer | `specs/013-live-request-summary/verification.md` |

Shared-hotspot note:
- `src/lib/types.ts`, `src/lib/endpoints.ts`: T002 only.
- `backend/app/intake/schemas.py`, `routes.py`, `service.py`, `drafts.py`, `activity.py`: T003 only.
- `_agreement-forms.tsx` + `_agreement-wizard.test.tsx`: T005 only. `_request-overview.tsx`, `_request-asked.tsx`: T006 only.
- `app/models.py`, `app/jobs/tasks.py`, `src/components/ui.tsx`: untouched.
- Migration: T001 only (one revision on the single head `0075_nda_check`).
- Working tree is dirty with uncommitted 009–012 work (incl. `0075_nda_check.py`, `nda_check.py`, `_nda-check.tsx`);
  agents edit the working copy and must not revert or reformat those changes.

## Interface freeze (contract-first — fixed before implementation starts)

### API contract

All paths under `/api/v1`. The GET writes **nothing** (no audit row, no timeline row).

| Method | Path | Permission | Request | Response | Errors |
|---|---|---|---|---|---|
| POST | `/intake/requests` (existing) | unchanged: `intake:create` + screen `intake` ADD (`_INTAKE_ADD`) | `RequestCreate` + optional `summary: RequestSummaryFiling` | unchanged `RequestResponse` (201) | unchanged, **plus** 422 `"Your extra instructions are too long (over about 2,000 words). Shorten them, or attach them as a file instead."` when the instructions exceed the limit (only refusal this feature adds; checked by Pydantic before anything is saved) |
| GET | `/intake/requests/{request_id}/summary` (NEW) | `intake:create` + row gate `IntakeService.get_request` (staff `intake:read` / org admin: any request in the org; else only own) | none | 200 `RequestSummaryResponse` | 403 missing permission; 404 not found / other org / not visible to the viewer (identical to `/requests/{id}`, so no hint it exists) |
| POST | `/intake/drafts` (existing) | unchanged `intake:create` | `DraftSave` + optional `summary_box: object \| null` | `DraftResponse` + `summary_box` | unchanged |
| PUT | `/intake/drafts/{draft_id}` (existing) | unchanged `intake:create` + own-draft check | same as POST | same | unchanged |
| GET | `/intake/drafts` (existing) | unchanged | none | `DraftResponse[]` + `summary_box` each | unchanged |

There is deliberately **no** endpoint that edits, adds to or deletes extra instructions or the filed summary (FR-39,
AC-32). `PATCH /intake/requests/{id}` (`RequestUpdate`) has no field that reaches `filed_summary`.

Full shapes:

```json
// POST /api/v1/intake/requests — the one added key (everything else unchanged)
{
  "type_label": "New agreement · NDA",
  "field_values": { "request_form": "new_agreement", "agreement_type": "NDA", "nda_kind": "One-way", "...": "..." },
  "summary": {                                   // optional; sent by the agreement wizard only
    "text": "This is a one-way NDA between Aegis India Pvt Ltd and Initech Software Ltd, on our template.\nIt lasts 2 years and is under Indian law.",
                                                 // sentences built from the FINAL answers, one per line, no gap lines;
                                                 // null when the box could not be built (FR-9)
    "instructions": [
      { "text": "Please send a copy to procurement", "differs": [] },
      { "text": "It is under English law with arbitration in London",
        "differs": [ { "key": "governing_law", "label": "Governing law", "answer": "India" } ] }
    ],
    "accepted": [                                // offers the requester accepted (FR-17/20; audit only)
      { "key": "nda_kind", "label": "Mutual or one-way?", "old": "Mutual", "new": "One-way" }
    ]
  }
}
```

```json
// GET /api/v1/intake/requests/{request_id}/summary → 200
{
  "text": "This is a one-way NDA between …\nIt lasts 2 years and is under Indian law.",   // null when none was filed
  "at": "2026-10-27T09:12:00+00:00",          // filing time; null when nothing filed
  "by_name": "Maya Chen",                      // who wrote them (the filer); null when nothing filed
  "instructions": [ { "text": "…", "differs": [ { "key": "governing_law", "label": "Governing law", "answer": "India" } ] } ]
}
// A visible request filed before this feature, or by email/chat: {"text": null, "at": null, "by_name": null, "instructions": []}
```

```json
// POST/PUT /api/v1/intake/drafts — one added key; GET returns it as saved (null when never saved)
{ "form_key": "new_agreement", "title": "NDA — Initech", "values": { "...": "..." }, "parent_contract_id": null,
  "page_index": 3, "visited": 4,
  "summary_box": { "edits": {}, "offers": [], "instructions": [], "accepted": [], "pending": "" } }
```

#### Frozen server rules (`app/intake/request_summary.py`)

Constants: `WORD_LIMIT = 2000`, `CHAR_LIMIT = 20_000` (all instruction text together), `MAX_INSTRUCTIONS = 100`,
`TEXT_CUT = 20_000` (summary text), `ANSWER_CUT = 500`, `MAX_ACCEPTED = 50`, `MAX_DIFFERS = 10`.
`LIMIT_MESSAGE = "Your extra instructions are too long (over about 2,000 words). Shorten them, or attach them as a file instead."`

**Plain text** — `clean_text(s)`: drop every character in Unicode category `Cc` except `\n` and `\t` (this removes
NUL, which Postgres cannot store), normalise `\r\n`/`\r` to `\n`, strip ends. Applied to the summary text, every
instruction text, every `differs.answer`/`label`, every accepted `old`/`new` string.

**Validation (schemas.py, never raises except for the limit — FR-9, FR-32):**
- `RequestSummaryFiling` `mode="before"` validators drop anything malformed instead of raising: a non-list
  `instructions`/`accepted`/`differs` → `[]`; non-dict items dropped; an instruction whose cleaned text is empty
  dropped; `differs` capped at `MAX_DIFFERS`; `accepted` capped at `MAX_ACCEPTED`; `text` non-string → `None`, cut
  at `TEXT_CUT`; `answer`/`old`/`new` strings cut at `ANSWER_CUT`; `old`/`new` that are lists keep only strings
  (≤ 20, each ≤ 200 chars); any other type → `None`.
- `model_validator(mode="after")`: `len(instructions) > MAX_INSTRUCTIONS` or
  `sum(len(i.text.split()) for i in instructions) > WORD_LIMIT` or `sum(len(i.text) ...) > CHAR_LIMIT` →
  `ValueError(LIMIT_MESSAGE)` → 422 before the handler runs (nothing saved). Exactly 2,000 words → accepted.

**Filing** — `record_summary(db, actor, request, summary: RequestSummaryFiling | None, field_values: dict | None,
http_request_id: str | None) -> None`, called in `IntakeService.create_request` immediately after
`record_filing(...)` and before `intake.created` is written, on the request's own session inside its own
transaction (no second session — CLAUDE.md audit-lock rule; commit stays with `create_request`):
1. Return (store nothing, write nothing) when `summary` is None or `field_values.get("request_form")` is not one
   of the nine forms (`form_fields(form)` empty). The duplicate-retry early return in `create_request` already sits
   before this, so a retry within 2 minutes stores nothing new (FR-37; the summary is not part of the duplicate
   comparison — confirms the spec's edge case).
2. Pure computation inside `try/except Exception` (log a warning and return on any error — the summary can never
   break a filing, FR-9):
   - `fields = {f.key: f for f in form_fields(form)}`.
   - Instructions: each `differs` entry whose `key` is not in `fields` is dropped; `label` is replaced by
     `fields[key].label` (the server's label is the one truth).
   - Accepted: keep an entry only when `key in fields`, `key in field_values`, and
     `_coerce_field(fields[key], entry.new) == field_values[key]` (coercion errors ⇒ drop). So an accepted change
     that was later changed again on the form, or any forged entry, never reaches the record.
3. When `text` is None and there are no instructions ⇒ store nothing. Otherwise
   `request.filed_summary = {"text": text, "at": utcnow().isoformat(), "by_user_id": actor.id, "instructions":
   [{"text", "differs": [{"key", "label", "answer"}]}]}`.
4. When instructions or accepted entries exist (and only then), in the same transaction:
   - `write_audit_log(db, action="intake.summary_filed", resource_type="intake_request", resource_id=request.id,
     org_id=actor.org_id, actor_user_id=actor.id, request_id=http_request_id, after={"instructions": n,
     "answers_changed": [{"key", "old", "new"}]})` — **no instruction text, no summary text** (spec audit rule).
     Who = `actor_user_id`, when = the audit row's own timestamp.
   - `write_timeline_event(db, org_id=actor.org_id, resource_type="intake_request", resource_id=request.id,
     event_type="intake.summary_filed", title=<below>, actor_user_id=actor.id, request_id=http_request_id,
     details={"instructions": n, "answers_changed": [{"key", "label", "old", "new"}]})`.
   - Title: n>0, m=0 → `"Filed with {n} extra instruction{s} for Legal"`; n>0, m>0 → that +
     `" · {m} answer{s} changed from the summary"`; n=0, m>0 → `"{m} answer{s} changed from the summary before filing"`.
   - `activity.EVENTS` gains `"intake.summary_filed": _s(None, "person")` (the coverage guard in
     `tests/test_request_activity.py` requires it).
- Typing, offers, declining, Save as draft and abandoning a form write **no** audit or timeline row (drafts.py
  writes none today and stays that way).

**Read** — `read(db, request) -> dict`: from `request.filed_summary` (None ⇒ the all-null shape);
`by_name = user.full_name or user.email` for `by_user_id` (None when the user row is gone). Only fields listed in
`RequestSummaryResponse` are returned (never `by_user_id`). No other module reads `filed_summary` (FR-31; pinned by a
static test).

**Drafts** — `drafts.save` sets `d.summary_box = payload.summary_box if isinstance(payload.summary_box, dict) else
None`; `serialize` adds `"summary_box": d.summary_box`. Drafts stay unvalidated by design (module docstring) and
private to their owner (`_own`). Nothing new is audited.

### Backend Pydantic models (`app/intake/schemas.py`)

```python
class SummaryDiffers(BaseModel):
    key: str = Field(max_length=60)
    label: str = ""                     # replaced by the form's label on filing
    answer: str = ""                    # the answer as the requester saw it ("India", "15 Nov 2026", "INR 50,00,000")

class ExtraInstruction(BaseModel):
    text: str
    differs: list[SummaryDiffers] = Field(default_factory=list)

class AcceptedChange(BaseModel):
    key: str = Field(max_length=60)
    label: str = ""
    old: str | list[str] | None = None
    new: str | list[str] | None = None

class RequestSummaryFiling(BaseModel):
    text: str | None = None
    instructions: list[ExtraInstruction] = Field(default_factory=list)
    accepted: list[AcceptedChange] = Field(default_factory=list)
    # before-validators clean (never raise); after-validator raises only LIMIT_MESSAGE (see frozen rules)

class RequestSummaryResponse(BaseModel):
    text: str | None
    at: datetime | None
    by_name: str | None
    instructions: list[ExtraInstruction]

class RequestCreate(BaseModel):        # existing — one optional field
    ...
    summary: RequestSummaryFiling | None = None

class DraftSave(BaseModel):            # existing — one optional field
    ...
    summary_box: dict | None = None

class DraftResponse(BaseModel):        # existing — one field
    ...
    summary_box: dict | None = None
```

Route (`app/intake/routes.py`, next to `/requests/{request_id}/nda-check`):
`@router.get("/requests/{request_id}/summary", response_model=RequestSummaryResponse)` with
`current_user=Depends(_CREATE)`; body: `r = intake_service.get_request(user=current_user, request_id=request_id)`
then `request_summary.read(intake_service.db, r)` (lazy import, as `nda_check`).

### Database schema

| Table | Column | Type | Constraints |
|---|---|---|---|
| `intake_request` | `filed_summary` | JSON | nullable; no default; no index (read only by id with its row) |
| `intake_draft` | `summary_box` | JSON | nullable; no default; no index |

Org scoping: both inherit their row's `org_id` (already indexed on both tables). `filed_summary` is read only
through `get_request` (org + visibility gate); `summary_box` only through `drafts._own` (org + owner).
Model lines:
- `app/intake/models.py`, `IntakeRequest`, after `nda_check`:
  `filed_summary = Column(JSON, nullable=True)  # the request in words + extra instructions as filed — see intake/request_summary.py`
- `IntakeDraft`, after `visited`:
  `summary_box = Column(JSON, nullable=True)  # the wizard's summary-box state (edits, offers, instructions) — spec 013`

`filed_summary` is **not** added to `serialize_request` (lists would carry up to 2,000 words per row and the spec
keeps the summary out of lists — served only by the per-request GET).

Stored shape (frozen): `{"text": str | null, "at": ISO-8601, "by_user_id": str, "instructions": [{"text": str,
"differs": [{"key": str, "label": str, "answer": str}]}]}`.

Migration: revision ID **`0076_request_summary`** (20 chars), `down_revision = "0075_nda_check"` (today's single
head). `upgrade`: `op.add_column("intake_request", sa.Column("filed_summary", sa.JSON(), nullable=True))` and
`op.add_column("intake_draft", sa.Column("summary_box", sa.JSON(), nullable=True))`. `downgrade`: drop both. After it
lands: `make test-db`; `alembic heads` shows only `0076_request_summary`.

### Frontend TypeScript models (added to src/lib/types.ts)

```ts
/** One answer an extra instruction disagrees with (FR-18/27). Rendered "This differs from your answer: {answer}". */
export interface SummaryDiffers {
  key: string;
  label: string;
  answer: string;                 // the current answer as text, or "no answer"
}
/** An extra instruction for Legal, in the requester's own words (FR-25..27). */
export interface ExtraInstruction {
  text: string;
  differs: SummaryDiffers[];      // [] → no note
}
/** An "Update the answer?" offer the requester accepted (FR-17/20) — for the filing record only. */
export interface AcceptedChange {
  key: string;
  label: string;
  old: string | string[] | null;
  new: string | string[] | null;
}
/** An open offer under a line of the editable box. */
export interface SummaryOffer {
  id: string;                     // `${line}:${keys.join("+")}`
  line: string;                   // SummaryLine.id it came from
  kind: "update" | "clear";
  keys: string[];                 // the questions it changes (money: ["value","currency"])
  label: string;                  // "Governing law"
  oldText: string;                // "India"
  newText: string;                // "England and Wales"
  patch: Record<string, FormValue>;   // applied with the wizard's own `set` on accept
}
/** What the requester typed over one generated line, and what checking it found. */
export interface SummaryEdit {
  text: string;                   // their line as typed ("" = deleted)
  base: string;                   // the generated line when they started editing
  check: { templateMatched: boolean; differs: SummaryDiffers[]; declined: string[] } | null;  // null = not checked yet
}
/** The box's whole state: kept in the wizard, saved in drafts (summary_box), finalised on filing. */
export interface SummaryBoxState {
  edits: Record<string, SummaryEdit>;   // by SummaryLine.id
  offers: SummaryOffer[];
  instructions: ExtraInstruction[];
  accepted: AcceptedChange[];
  pending: string;                      // the "Anything else for Legal?" box, not yet added
}
/** Sent inside POST /intake/requests as `summary`. */
export interface RequestSummaryFiling {
  text: string | null;
  instructions: ExtraInstruction[];
  accepted: AcceptedChange[];
}
/** GET /intake/requests/{id}/summary */
export interface RequestSummary {
  text: string | null;
  at: string | null;
  by_name: string | null;
  instructions: ExtraInstruction[];
}
// IntakeDraft gains (append to the existing interface):
//   summary_box?: SummaryBoxState | null;
```

### Endpoint client additions (src/lib/endpoints.ts)

- `intakeApi.requestSummary(id: string) → Promise<RequestSummary>` — `GET /intake/requests/${id}/summary`
  (placed after `requestNdaCheck`).
- `intakeApi.create`, `createDraft`, `updateDraft` keep their `Record<string, unknown>` signatures; the wizard adds
  `summary` / `summary_box` to the bodies.

### The sentence rules (frozen) — `src/lib/request-summary.ts`

**Where they live (D1):** frontend only, beside the wizard, keyed by question key — the same home as each
question's `uses` text in `_agreement-forms.tsx`. `agreement_forms.json` stays the only source of questions,
labels, options and "only when" rules; this file adds wording only. A guard test (below) fails if any question of
any form has no sentence, if a rule names a question or option the JSON lacks, or if a choice question's variants
miss an option — the equivalent of `agreement-forms.test.ts` for layout.

```ts
export interface SummaryContext {
  parentTitle: string | null;     // title of the picked parent contract (the wizard's `parent`), as the form shows it
}
export interface SentenceRule {
  id: string;                     // composite id, or the question key for a single-question ("solo") rule
  forms?: string[];               // default: every form that asks all of `keys`
  keys: string[];                 // slot order
  text?: string;                  // template; "{key}" = that answer's slot
  variants?: Record<string, string>;   // single choice question: option → whole sentence (no slots)
  rows?: { column: string; after?: string };   // single rows question: noun from field.item ?? ["deliverable","deliverables"]
  when?: RequestFormRule[];       // extra condition, same shape and meaning as a question's `show`
  phrases?: Record<string, Record<string, string>>;   // key → option → wording in THIS sentence (default: option label)
  readOnly?: boolean;             // shown, never editable (note_approvers)
  noGap?: boolean;                // never shown as a gap (advice_only: an unticked box is an answer)
}
export interface SummaryLine {
  id: string;                     // rule id
  kind: "sentence" | "gap";
  text: string;                   // the sentence, or "Counterparty: not chosen yet"
  keys: string[];
  name: string;                   // NAMES of its keys joined with " and "
  required: boolean;              // any of its keys is required (gap styling; FR-24)
  editable: boolean;              // sentence && !readOnly
}
export const SENTENCE_RULES: SentenceRule[];
export const NAMES: Record<string, string>;          // key, or "<form>.<key>" override → short name
export const SYNONYMS: Record<string, Record<string, string[]>>;   // key → option → extra spellings
export function buildSummary(form: RequestFormDef, def: { steps: { fields: string[] }[] },
  values: Record<string, FormValue>, ctx: SummaryContext): SummaryLine[];
export function summaryText(lines: SummaryLine[]): string;   // sentence lines only, "\n"-joined (what is filed)
export function writtenDate(iso: string): string;            // "2026-11-15" → "15 Nov 2026" (fixed English months)
export function moneyText(currency: FormValue | undefined, amount: FormValue | undefined): string;
                                                              // moved from the wizard's money(): "INR 50,00,000"
export function answerText(form: RequestFormDef, key: string, values: Record<string, FormValue>, ctx: SummaryContext): string;
                                                              // X in "This differs from your answer: X"; "no answer" when blank
```

**Building (deterministic):**
1. A question applies when `shown(field.show, values)` (from `lib/form-checks.ts`). Not applying ⇒ never mentioned
   (FR-5). "Answered" = not `blank` (same test as the wizard: empty string, `[]`, all-empty rows).
2. Slot text by kind: select → `phrases[key][option] ?? option`; multiselect → options in form order joined
   `"A, B and C"`; date → `writtenDate`; `value`/`new_value`/`renew_value` → `moneyText(values.currency || "INR", v)`;
   `fee_rate` → `moneyText(values.currency || "INR", v)`; number otherwise → digits with en-IN grouping;
   text/email/textarea → the answer trimmed, in full; `parent_contract_id` → `ctx.parentTitle ?? "the chosen
   agreement"`; `cancel_request_ref` → the ref as stored; `entity`/`counterparty`/`incoming_party` → the stored
   name. Rows → `"{n} {noun}{after}: {items}"`: noun singular for 1; items = the `column` cell of the first 3
   non-empty rows joined `"A, B and C"`, or `"A, B, C and {k} more"` when more than 3 (FR-7). Files are not answers
   in `agreement_forms.json` and are not in the box.
3. Line order: walk `def.steps[*].fields` in order, then `note_approvers`; for each key not yet placed, place the
   line that owns it. A **composite** rule (more than one key) owns its keys when its `when` holds, every key
   applies, and every key is answered; otherwise each key falls back to its **solo** rule (or its gap). Every key
   belongs to exactly one line. Companions `entity_id`, `counterparty_id`, `currency` are never lines of their own.
4. Gap line for an applying, unanswered key (unless `noGap`): `"{NAME}: not chosen yet"` for select,
   multiselect and register picks (`entity`, `counterparty`, `parent_contract_id`, `cancel_request_ref`,
   `incoming_party`); `"{NAME}: not given yet"` for everything else. `required` = the question's `required`.
   Gaps are UI hints only: not filed, not editable (FR-4).
5. `buildSummary` never throws on odd answers (wrong types read as unanswered). The wizard still wraps it in
   `try/catch` (FR-9).

**Shared rules** (every form that asks the key, unless a form-specific rule below replaces it):

| id | keys | template / variants | phrases |
|---|---|---|---|
| `needed_by` | needed_by | `Needed by {needed_by}.` | |
| `department` | department | `Requested by {department}.` | |
| `signer` | cp_signer_name, cp_signer_email | `Their signer is {cp_signer_name} ({cp_signer_email}).` | |
| `cp_signer_name` | cp_signer_name | `Their signer is {cp_signer_name}.` | |
| `cp_signer_email` | cp_signer_email | `Their signer's email is {cp_signer_email}.` | |
| `paper` | paper | `It is on {paper}.` | Our template→`our template`, Their paper→`their paper` |
| `entity` | entity | `Our side is {entity}.` | |
| `counterparty` | counterparty | `The other side is {counterparty}.` | |
| `start_date` | start_date | `It starts on {start_date}.` | |
| `term` | term | `It has {term}.` | Fixed end date→`a fixed end date`, Renews automatically→`automatic renewal`, No end date (evergreen)→`no end date (evergreen)` |
| `end_date` | end_date | `It ends on {end_date}.` | |
| `renewal_term` | renewal_term | `Each renewal lasts {renewal_term}.` | |
| `notice_days` | notice_days | `Notice to stop it renewing: {notice_days}.` | |
| `scope` | scope | `Scope: {scope}` | |
| `personal_data` | personal_data | Yes→`Personal data will be shared or processed.` · No→`No personal data will be shared or processed.` | |
| `gxp` | gxp | Yes→`It is GxP or other regulated quality work.` · No→`It is not GxP or other regulated quality work.` | |
| `reason` | reason | `Why: {reason}` | |
| `note_approvers` | note_approvers | `Note to approvers: {note_approvers}` (`readOnly` — edited in its own textarea, FR-38) | |

**New agreement** (`new_agreement`):

| id | keys | template / variants | phrases / notes |
|---|---|---|---|
| `deal_nda` (when agreement_type in [NDA]) | nda_kind, agreement_type, entity, counterparty, paper | `This is a {nda_kind} {agreement_type} between {entity} and {counterparty}, on {paper}.` | nda_kind: Mutual→`mutual`, One-way→`one-way`; agreement_type: NDA→`NDA`; paper as shared |
| `deal` (when agreement_type NOT in [NDA]) | agreement_type, entity, counterparty, paper | `This is {agreement_type} between {entity} and {counterparty}, on {paper}.` | agreement_type: NDA→`an NDA`, Services (MSA)→`a services agreement (MSA)`, Buying from a vendor→`a purchase from a vendor`, Software or SaaS→`a software or SaaS agreement`, Consultancy→`a consultancy agreement`, Selling to a customer→`a sale to a customer`, Something else→`another kind of agreement` |
| `agreement_type` | agreement_type | `This is {agreement_type}.` | phrases as `deal` |
| `nda_kind` | nda_kind | `It is a {nda_kind} NDA.` | as `deal_nda` |
| `purpose` | purpose | `Purpose: {purpose}` | |
| `nda_direction` | nda_direction | `Only {nda_direction} share information.` | We share→`we`, They share→`they` |
| `nda_term_law` | nda_term, governing_law | `It lasts {nda_term} and is under {governing_law} law.` | governing_law: India→`Indian`, England and Wales→`English`, Singapore→`Singapore`, New York→`New York`, Delaware→`Delaware` |
| `nda_term` | nda_term | `It lasts {nda_term}.` | |
| `governing_law` | governing_law | `It is under {governing_law} law.` | as `nda_term_law` |
| `value` (forms: new_agreement, regularize) | value | `The total value is {value}.` | slot covers value + currency |
| `advice_only` | advice_only | There are no separate deliverables (advice only)→`There are no separate deliverables (advice only).` | `noGap` |
| `consultancy_deliverables` | consultancy_deliverables | rows, column `name` | `6 deliverables: A, B, C and 3 more.` |
| `buying` | buying | `We are buying {buying}.` | Goods→`goods`, Services→`services`, Goods and services→`goods and services` |
| `hosting` | hosting | `Our data will be hosted in {hosting}.` | India→`India`, European Union→`the European Union`, United States→`the United States`, Several regions→`several regions` |
| `payment_terms` | payment_terms | `Payment is due within {payment_terms}.` | |
| `fee_basis` | fee_basis | `The consultant is paid {fee_basis}.` | Fixed fee→`a fixed fee`, Day rate→`a day rate`, Hourly rate→`an hourly rate` |
| `fee_rate` | fee_rate | `The rate is {fee_rate}.` | money slot in the form's currency |
| `key_individuals` | key_individuals | rows, column `name` | noun from `item` |
| `contact` | company_contact_name, company_contact_role | `Our contact is {company_contact_name} ({company_contact_role}).` | |
| `company_contact_name` | company_contact_name | `Our contact is {company_contact_name}.` | |
| `company_contact_role` | company_contact_role | `Our contact's role is {company_contact_role}.` | |
| `nonstandard` | nonstandard | `Non-standard terms asked for: {nonstandard}` | |

**Statement of Work** (`sow`): `parent_contract_id` → `It is under the master agreement “{parent_contract_id}”.` ·
`sow_title` → `The SoW is called “{sow_title}”.` · composite `services_window` (services_start, services_end) →
`Services run from {services_start} to {services_end}.`; solos `Services start on {services_start}.` /
`Services end on {services_end}.` · `value` (forms: sow) → `The total value of this SoW is {value}.` · `pricing` →
`Pricing is {pricing}.` (Fixed fee→`a fixed fee`, Time and materials→`time and materials`, Milestones→`by milestones`)
· `deliverable_rows` → rows, column `name` · plus shared signer, needed_by, department, scope, personal_data, gxp,
note_approvers.

**Data Processing Agreement** (`dpa`): `parent_contract_id` → `It goes with “{parent_contract_id}”.` · `our_role` →
`We are the {our_role}.` (Controller: …→`controller (we decide how the data is used)`, Processor: …→`processor (we act
on their instructions)`) · `processing_purpose` → `Why the data is processed: {processing_purpose}` · `data_types` →
`Personal data involved: {data_types}.` · `data_subjects` → `Whose data: {data_subjects}.` · `transfer` variants
Yes→`Data will leave India.` No→`Data will stay in India.` · `transfer_countries` → rows, column `country`, after
` outside India` (`2 countries outside India: France and Germany.`) · `subprocessors` variants Yes→`They will use
sub-processors.` No→`They will not use sub-processors.` · `subprocessor_rows` → rows, column `name` · plus shared
signer, paper, needed_by, note_approvers.

**Amendment** (`amendment`): `parent_contract_id` → `It amends “{parent_contract_id}”.` · `what_changes` → `What is
changing: {what_changes}.` · `new_value` → `The new total value is {new_value}.` · `new_end_date` → `The new end date
is {new_end_date}.` · `change_effective` → `The change takes effect on {change_effective}.` · shared needed_by,
reason, note_approvers.

**Renewal** (`renewal`): `parent_contract_id` → `It renews “{parent_contract_id}”.` · `renew_terms` → `It renews on
{renew_terms}.` (Same terms→`the same terms`, Changed terms→`changed terms`) · `renew_end` → `The new end date is
{renew_end}.` · `renew_value` → `The new total value is {renew_value}.` · shared needed_by, reason, note_approvers.

**Termination** (`termination`): `parent_contract_id` → `It ends “{parent_contract_id}”.` · `grounds` → `It is
ending {grounds}.` (For convenience→`for convenience`, Breach by the other side→`because of a breach by the other
side`, Mutual agreement→`by mutual agreement`, Not renewing at expiry→`by not renewing at expiry`) ·
`termination_date` → `The termination date is {termination_date}.` · `clause_breached` → `The clause breached is
{clause_breached}.` · `cure_served` variants Yes→`The cure period has run out.` No→`The cure period has not run out
yet.` · shared needed_by, reason, note_approvers.

**Novation** (`novation`): `parent_contract_id` → `It moves “{parent_contract_id}” to a new party.` · `transferring`
variants They are→`They are transferring out.` We are→`We are transferring out.` · `incoming_party` → `The new party
is {incoming_party}.` · `effective_date` → `It takes effect on {effective_date}.` · `consent` variants Yes→`The
outgoing party has agreed in writing.` Not yet→`The outgoing party has not agreed in writing yet.` · shared
needed_by, reason, note_approvers.

**Signed outside the system** (`regularize`): composite `parties_signed` (entity, counterparty) → `It is between
{entity} and {counterparty}.` (solos: shared `entity`, `counterparty`) · `date_signed` → `It was signed on
{date_signed}.` · `value` as new_agreement · `why_outside` → `Why it was signed outside the system: {why_outside}.`
· `prior_approval` → `Approved at the time: {prior_approval}.` · `signed_by_us` → `Signed for us by
{signed_by_us}.` · shared start_date, term, end_date, renewal_term, notice_days, note_approvers.

**Cancel a request** (`cancellation`): `cancel_request_ref` → `Cancel request {cancel_request_ref}.` ·
`cancel_reason` → `Reason: {cancel_reason}.` · `tell_cp` variants Yes→`The counterparty already has a draft.`
No→`The counterparty has no draft yet.` · shared note_approvers.

**Gap names** (`NAMES`; default = the label without a trailing "?"): agreement_type `Kind of agreement`, entity `Our
legal entity`, counterparty `Counterparty`, cp_signer_name `Their signer's name`, cp_signer_email `Their signer's
email`, paper `Whose paper`, purpose `Purpose`, department `Requesting department`, nda_kind `Mutual or one-way`,
nda_direction `Who shares information`, nda_term `How long it lasts`, notice_days `Notice to stop it renewing`,
consultancy_deliverables `Deliverables`, buying `What we are buying`, hosting `Where our data is hosted`, fee_basis
`How the consultant is paid`, key_individuals `Key individuals`, company_contact_name `Company contact's name`,
company_contact_role `Company contact's role`, nonstandard `Non-standard terms`, personal_data `Personal data`, gxp
`GxP or regulated work`, `sow.parent_contract_id` `Master agreement`, parent_contract_id (other forms) `Agreement`,
`sow.value` `Total value of this SoW`, processing_purpose `Why the data is processed`, data_types `Kinds of personal
data`, data_subjects `Whose data`, transfer `Data leaving India`, subprocessors `Sub-processors used`,
subprocessor_rows `Sub-processors`, what_changes `What is changing`, reason `Why`, renew_terms `Renew on`, grounds
`Why it is ending`, clause_breached `Clause breached`, cure_served `Cure period run out`, transferring `Who is
transferring out`, incoming_party `Who takes over`, consent `Outgoing party's written agreement`, why_outside `Why it
was signed outside the system`, prior_approval `Approved at the time`, signed_by_us `Who signed for us`,
cancel_request_ref `Request to cancel`, cancel_reason `Why cancel it`, tell_cp `Counterparty has a draft`. Every
other key uses its label as is (e.g. `Needed by`, `Governing law`, `Start date`).

### The smart-edit rules (frozen) — `src/lib/summary-edits.ts`

```ts
export const WORD_LIMIT = 2000;
export const WORD_WARN = 1800;
export const EMPTY_BOX: SummaryBoxState;   // { edits: {}, offers: [], instructions: [], accepted: [], pending: "" }
export interface CheckContext {
  form: RequestFormDef; def: { steps: { fields: string[] }[] };
  values: Record<string, FormValue>; lines: SummaryLine[]; ctx: SummaryContext;
  locale?: string;                         // default navigator.language; injected in tests
}
export function norm(s: string): string;
export function parseDate(text: string, locale?: string): string | null;            // ISO day or null
export function parseMoney(text: string): { amount: string; currency: string | null } | null;
export function matchChoice(field: RequestFormField, ruleId: string, text: string): string | null;  // option or null
export function editLine(box: SummaryBoxState, lineId: string, text: string, c: CheckContext): SummaryBoxState;
export function checkLine(box: SummaryBoxState, lineId: string, c: CheckContext): { box: SummaryBoxState; announce: string };
export function acceptOffer(box: SummaryBoxState, offerId: string, c: CheckContext):
  { box: SummaryBoxState; patch: Record<string, FormValue>; announce: string };
export function declineOffer(box: SummaryBoxState, offerId: string, c: CheckContext): { box: SummaryBoxState; announce: string };
export function undoEdits(box: SummaryBoxState): SummaryBoxState;                    // FR-13
export function reconcile(box: SummaryBoxState, c: CheckContext): SummaryBoxState;   // FR-23, FR-36
export function finalize(box: SummaryBoxState, c: CheckContext): SummaryBoxState;    // FR-34
export function addPending(box: SummaryBoxState): SummaryBoxState;
export function removeInstruction(box: SummaryBoxState, index: number): SummaryBoxState;
export function wordCount(box: SummaryBoxState): number;                            // instructions + pending
```

**Normalising** — `norm(s)`: NFKC; lower-case; remove `“ ” " ‘ ’`; collapse whitespace; trim; drop one trailing
`.` or `!`. Choice matching additionally drops a leading `a `, `an `, `the `.

**When a line is checked** — on `blur` of the line, or Enter in it (Enter never inserts a newline in a line). Never
on each keystroke. Typing only stores `edits[id] = {text, base: <generated line when the edit began>, check: null}`
(`editLine`). Checking never changes an answer; only `acceptOffer` returns a `patch`, applied by the wizard with its
own `set` — the same path as a form control, so routing, the NDA check and "only when" rules react (FR-17, FR-20).

**`checkLine` algorithm** for edit E on line L (rule R, current values):
0. `norm(E.text) == norm(L.text)` ⇒ drop the edit; nothing else.
1. **Deleted line** (`norm(E.text) == ""`): if `L.required` ⇒ drop the edit (line returns), announce
   `"{L.name} is required, so its line can't be removed. The line is back."` (FR-24). Else one offer
   `kind:"clear"`, keys = L's answered keys, `patch` each key → `""` (multiselect → `[]`), `newText: "no answer"`.
2. **Rows line** ⇒ no offer; `differs = [{key, label, answer: rows text}]`; `templateMatched:false` (an edit to a
   table summary is never applied: a table has several columns, D6).
3. **Variants line** (one choice key K): `norm(E.text)` equal to `norm(variant)` of another option ⇒ offer K → that
   option. Otherwise no offer; `differs = []` when `norm(E.text)` contains `norm(current variant)` (they only added
   words), else `[K]`.
4. **Template line**: build a regex from R's template with the current slot texts: fixed text escaped, every space
   → `\s+`, each slot → `(<escaped current slot text>|.+?)` (the current value is tried first, so a name containing
   "and" stays whole), anchored `^\s*…[.!]?\s*$`, case-insensitive. A single free-text-slot rule whose template is
   `"<Prefix>: {k}"` also matches E without the prefix.
   - **Match** (`templateMatched:true`): per slot key K with captured c — `norm(c) == norm(current slot)` ⇒
     unchanged; otherwise read c by K's kind (table below). Certain new value ⇒ offer; anything else ⇒ K in differs.
   - **No match** (`templateMatched:false`): per slot key K — the current slot text (or any spelling of the current
     option) still appears in E as whole words ⇒ unchanged; else select: exactly one OTHER option's spellings appear
     ⇒ offer; date / money: exactly one readable date / money expression appears ⇒ offer; every other kind ⇒ differs.
5. An offer is made only when the new value passes the question's own checks (`answerProblem(kind, value,
   notBefore, rulesFor(field, values))` from `lib/form-checks.ts`, plus `looksLikeEmail` for email) and differs from
   the current answer; otherwise K goes to differs (never offer an answer the form would refuse).
6. Offers for the line replace earlier offers for the same line. Result stored as `edits[id].check`.
7. **No offers at all** ⇒ the edit is resolved now (rule R below). Announce for offers: `"Aegis can update
   {label}: {oldText} → {newText}."` (one sentence per offer).

**Reading a changed slot** (only certain readings; anything else ⇒ differs):

| Kind | Certain when |
|---|---|
| select | `matchChoice`: `norm(c)` equals, for exactly one option, its label, its phrase in this rule, or a `SYNONYMS` spelling. Duration options (every option matches `^\d+ (year|years|day|days)$`: nda_term, renewal_term, notice_days, payment_terms) also read `"<n or word one..ten, thirty, forty-five, sixty, ninety> year(s)/yr(s)/month(s)/day(s)"`, months converted to years when divisible by 12; the option with the same length is the match. |
| multiselect | removing option spellings (longest first) and the separators `,` `;` `&` `and` leaves nothing; the set is non-empty when the question is required. |
| date | `parseDate(c, locale)` returns a day. |
| value / new_value / renew_value | `parseMoney(c)` returns an amount; patch `{[key]: amount}` plus `{currency}` when a currency is named and differs from the current one (one combined offer, `keys: [key, "currency"]`). No currency named ⇒ the current currency stays (shown in `newText`). |
| fee_rate | as money, but a named currency other than the form's ⇒ uncertain (the currency is shared with the total value). |
| number (other) | `parseMoney(c)` with no currency named. |
| text, email, textarea (free text) | the captured wording, tidied (`tidyCell`), non-empty; offer carries it as the new answer (FR-16). |
| register picks (entity, counterparty, parent_contract_id, cancel_request_ref, incoming_party) | **never** — always differs; no pick changes, nothing is created (FR-22). |
| rows | never (D6). |

**`SYNONYMS`** (frozen; label and phrase are always accepted too): agreement_type — NDA: nda, mnda, non-disclosure
agreement, non disclosure agreement, confidentiality agreement; Services (MSA): msa, services agreement, master
services agreement; Buying from a vendor: vendor agreement, purchase, purchase from a vendor; Software or SaaS:
software, saas, software agreement, saas agreement; Consultancy: consultancy agreement, consulting agreement,
consulting; Selling to a customer: customer agreement, sale to a customer · nda_kind — Mutual: two-way, two way,
bilateral; One-way: one way, unilateral · nda_direction — We share: we, us, our side; They share: they, them, the
other side, the counterparty · paper — Our template: our paper, our own paper, our standard paper; Their paper: their
template, their draft, their own paper · governing_law — India: indian; England and Wales: english, england, english
and welsh, uk; Singapore: singaporean; New York: ny · hosting — European Union: eu, europe; United States: us, usa,
america; Several regions: multiple regions · buying — Goods and services: both · pricing — Fixed fee: fixed price;
Time and materials: t&m · fee_basis — Day rate: daily rate; Hourly rate: hourly · our_role — Controller…:
controller; Processor…: processor · term — Fixed end date: fixed term; Renews automatically: auto-renewal, auto
renewal; No end date (evergreen): no end date, evergreen · renew_terms — Changed terms: new terms, different terms.
Any other choice question matches on label/phrase only.

**`parseMoney(text)`** — case-insensitive; an optional currency token before or after the number: INR (`inr`, `₹`,
`rs`, `rs.`, `rupees`), USD (`usd`, `$`, `us$`, `dollars`), EUR (`eur`, `€`, `euros`), GBP (`gbp`, `£`, `pounds`). The
number part must be one of: `^\d{1,3}(,\d{2,3})+(\.\d{1,2})?$` (Indian or Western grouping), `^\d+(\.\d{1,2})?$`, or
`^\d+(\.\d+)?\s*(lakh|lakhs|lac|lacs|l|crore|crores|cr|k|thousand|m|mn|million)$` (×1e5, ×1e7, ×1e3, ×1e6). Result
`> 0`, finite, ≤ 1e15, at most 2 decimals, returned as a plain digit string (`"5000000"`). Words ("fifty lakh"),
dotted or spaced grouping ("5.000.000", "5 000 000"), ranges and "approx." ⇒ null. Pinned cases: `"₹50 lakh"`,
`"50L"`, `"INR 50,00,000"`, `"5,000,000"`, `"5000000"` → 5000000; `"USD 60,000"` → 60000 USD; `"1.5 cr"` → 15000000;
`"fifty lakh"`, `"5.000.000"`, `"5-6 lakh"` → null.

**`parseDate(text, locale)`** — returns an ISO day or null:
- `yyyy-mm-dd` → that day.
- Written month, 4-digit year: `15 Nov 2026`, `15 November 2026`, `15th Nov 2026`, `Nov 15, 2026`, `November 15 2026`
  (English month names/3-letter abbreviations) → that day.
- Numeric `a/b/yyyy` (separators `/` `-` `.`): readings day-first (a=day) and month-first (a=month). Only one is a
  real day → that one. Both real and equal → that one. Both real and different → settled **only** when the browser's
  date order (`Intl.DateTimeFormat(locale).formatToParts`) is day-first — the order the app writes dates in — and then
  day-first is taken; otherwise unsettled ⇒ null (AC-9's "read two ways and cannot be settled").
- Two-digit or missing year, an impossible day, or two dates in the text ⇒ null.
- Pinned: `("1/12/2026","en-IN")` → 2026-12-01; `("1/12/2026","en-US")` → null; `("15/11/2026","en-US")` →
  2026-11-15; `("12/31/2026","en-IN")` → 2026-12-31; `("1/12/26", any)` → null.

**Resolving an edit** (when its line has no open offers left — after `checkLine` with no offers, or after the last
offer is accepted/declined/withdrawn):
- Dropped (nothing kept) when `templateMatched` and `differs` is empty and nothing was declined (every change is now
  in the answers), or when an accepted `clear` emptied the line.
- Otherwise kept as an instruction `{text: E.text, differs}` where `differs` = the uncertain keys + declined or
  withdrawn keys, each `{key, label: NAMES, answer: answerText(current values)}` (`"no answer"` when blank or no longer
  applying). A declined `clear` is kept as `{text: "Remove this line: “" + E.base + "”", differs: [those keys]}` (D7).
- The edit is removed from `edits`; the line shows the generated text again (the box always equals the answers;
  what they wrote is in the list). Announce `"Kept as an extra instruction for Legal."`.
- `acceptOffer` appends `{key, label, old, new}` per patched key to `accepted` (an existing entry for the same key
  keeps its first `old` and takes the new `new`); announce `"{label} updated to {newText}."`.
- `declineOffer` records the keys in `check.declined`; announce `"Your answer is kept. Your edit is kept as an extra
  instruction."` when it resolves.

**`reconcile`** (run by the wizard whenever `values` or `lines` change, and once after a draft loads — FR-23, FR-36):
an open offer is **withdrawn** when any of its keys no longer applies or its current answer no longer equals the
answer the offer started from (`oldText` vs `answerText`); an unchecked edit (`check: null`) whose `base` no longer
equals its line's current text is checked-and-declined at once; an edit whose line has disappeared is resolved. Each
then resolves as above (so "lasts 3 years" with a form change to 5 years becomes an instruction noting "5 years").

**`finalize`** (on Submit, before building the payload — FR-34): every unchecked edit is checked; every open offer is
treated as declined; non-empty `pending` is added as an instruction `{text, differs: []}`. No prompt, never blocks.

**`undoEdits`** (FR-13): clears `edits` and open `offers` (nothing kept from them — an explicit discard); leaves
`instructions` (each removable) and `accepted` (they are answers now; change them on the form).

**`wordCount`**: `text.trim().split(/\s+/).filter(Boolean).length` summed over `instructions[].text` and `pending`
(notes not counted) — the server's `len(text.split())`.

**Not matched, by design:** lines added under "Anything else for Legal?" are always instructions (FR-25); gap lines
and the note-to-approvers line are not editable.

### Frozen component contracts (frontend)

```ts
// src/app/(app)/intake/_summary-box.tsx
export function LiveSummary(p: {
  lines: SummaryLine[] | null;            // null = could not be built
  instructions: number;                   // count kept so far (shown on non-review steps)
}): JSX.Element;
export function SummaryEditor(p: {
  lines: SummaryLine[];
  box: SummaryBoxState;
  announce: string;
  onEdit: (lineId: string, text: string) => void;
  onCheck: (lineId: string) => void;
  onAccept: (offerId: string) => void;
  onDecline: (offerId: string) => void;
  onUndo: () => void;
}): JSX.Element;
export function ExtraInstructions(p: {
  items: ExtraInstruction[];
  pending: string;
  words: number;
  attachHint: string;                     // see wizard wiring
  onPending: (text: string) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
}): JSX.Element;
export function differsNote(d: SummaryDiffers[]): string | null;
// [] → null; one → "This differs from your answer: X"; several → "This differs from your answers: X; Y"

// src/app/(app)/intake/_request-summary.tsx
export const SUMMARY_CSS: string;         // .ro-scoped rules, CSS vars only (like ASKED_CSS)
export function RequestSummarySection(p: {
  status: "loading" | "error" | "ready";
  data: RequestSummary | undefined;
  onRetry: () => void;
}): JSX.Element | null;
```

## Component design

### Backend
- Models: `app/intake/models.py` — `IntakeRequest.filed_summary`, `IntakeDraft.summary_box` (T001).
- `app/intake/request_summary.py` (NEW, ~120 lines): constants, `clean_text`, `record_summary`, `read`. Imports
  `form_fields` from `agreement_forms`, `_coerce_field` from `app.intake.service` (lazy inside `record_summary` to
  avoid an import cycle), `write_audit_log`/`write_timeline_event` from `app.core.audit`.
- `app/intake/schemas.py`: the five models + `RequestCreate.summary`, `DraftSave.summary_box`,
  `DraftResponse.summary_box`; validators as frozen (the limit check calls into `request_summary` constants).
- `app/intake/routes.py`: `GET /requests/{request_id}/summary` (`_CREATE`, `get_request` first,
  `response_model=RequestSummaryResponse`).
- `app/intake/service.py` `create_request`: one line after `record_filing(...)`:
  `record_summary(db, actor, r, getattr(payload, "summary", None), field_values, request_id)` (lazy import). The
  duplicate-retry return stays before it. `description`, `field_values`, `type_label`, priority, triage, routing and
  drafting are untouched (FR-31, FR-33).
- `app/intake/drafts.py`: `serialize` + `save` carry `summary_box`.
- `app/intake/activity.py`: `"intake.summary_filed": _s(None, "person")`.
- Celery: none. Settings: none (no `validate_runtime_settings()` entry, no phase-10 fixture change).

### Frontend (Next.js page/component tree)

- `src/app/(app)/intake/page.tsx` → `AgreementWizard` (`_agreement-forms.tsx`, CHANGED):
  - `const ctx = { parentTitle: parent?.title ?? null }`;
    `const lines = useMemo(() => { try { return buildSummary(server, def, values, ctx); } catch { return null; } }, …)`.
  - `const [box, setBox] = useState<SummaryBoxState>(draft?.summary_box ?? EMPTY_BOX)`; `const [announce,
    setAnnounce] = useState("")`; `useEffect(() => setBox((b) => reconcile(b, c)), [lines])` (c = CheckContext with
    current values; also covers draft resume).
  - Handlers call the pure functions; `onAccept` → `const r = acceptOffer(box, id, c); setBox(r.box);
    set(r.patch); setAnnounce(r.announce)` and returns focus to the line's textbox.
  - `money()` is replaced by the imported `moneyText` (one rendering for the list and the box).
  - **Aside** (order): `NdaCheckPanel` (012, unchanged, first) → `LiveSummary` (every non-review step) → "What these
    answers do" (non-review) → "Where this request goes" (unchanged). On the review step the aside keeps the NDA check
    and routing panels; the box moves into the main card in its larger form.
  - **Small screens** (`< lg`, aside stacks under the card): a `lg:hidden` link at the top of the card body, "Read
    your request in words", `href="#request-in-words"` (the `LiveSummary` section id) (FR-10).
  - **Review step** (main card, order): Name this request → `SummaryEditor` → the existing answers `dl` + tables
    (FR-12, unchanged) → Note to approvers textarea (unchanged, FR-38) → `ExtraInstructions`.
  - `attachHint`: forms whose steps include `attachments` → `"attach them as a file under Supporting documents on the
    “{that step's title}” step"`; regularize and cancellation (no attachment question) → see Open question 1;
    default until answered: `"shorten them — this form has no place to attach a file"`.
  - `submit()`: after the existing step checks, `const fin = finalize(box, c); setBox(fin)`; if
    `wordCount(fin) > WORD_LIMIT` ⇒ `setNotice("Your extra instructions are {n} words, over the limit of about
    2,000. Shorten them, or {attachHint}.")` and stop (nothing cut, FR-32). Payload adds `summary: { text: lines ?
    summaryText(lines) : null, instructions: fin.instructions, accepted: fin.accepted }`. Within the limit, nothing
    about the box can stop filing.
  - `saveDraft()`: payload adds `summary_box: box`.
- `_summary-box.tsx` (NEW). Tailwind, `slate-*` + semantic tokens only; primitives `Button`, `Textarea`, `Badge`,
  `MessageBar` from `@/components/ui` (all exported today — verified); icons from `lucide-react`.
  - `LiveSummary`: `<section id="request-in-words" aria-labelledby="riw-h">` in the aside card style (`rounded-xl
    border border-slate-200 bg-slate-100 p-4 shadow-card`), `h3` "Your request in words". `<ul>` one `<li>` per line.
    Sentence `text-[13px] text-slate-800`. Required gap `text-danger` + visible text ` (required)`; optional gap
    `text-slate-500 italic` + ` (optional)` — told apart by words, not colour alone (FR-4). `lines === null` → one
    line "The summary could not be built. You can still file." Empty form → gaps only. `instructions > 0` → dim line
    "{n} extra instruction(s) for Legal — see the Review step." Footer: "Built from your answers. Updates as you
    answer." Not an aria-live region (it changes on every keystroke).
  - `SummaryEditor`: `<fieldset>` with legend "Your request in words — edit any line"; helper text "Aegis offers to
    update an answer when your words clearly change it. Nothing changes until you click." Each sentence line a
    `Textarea rows={1}` (auto-height), `aria-label="Line {n}: {name}"`, value `box.edits[id]?.text ?? line.text`,
    `onKeyDown` Enter → `onCheck`, `onBlur` → `onCheck`. Gap and read-only lines as plain text (note line hint "Edit it
    in Note to approvers below"). Under a line with open offers: `role="group" aria-label="Update the answer?"`
    (or "Clear the answer?"): "{label}: {oldText} → {newText}" + `Button size="sm"` "Update answer" / "Clear answer"
    and `Button size="sm" variant="ghost"` "Keep my answer"; the line's textarea gets `aria-describedby` the group.
    One `aria-live="polite" role="status"` element shows `announce` (offers announced when they appear, AC-30).
    `Button variant="ghost"` "Undo my edits" (disabled with no edits/offers).
  - `ExtraInstructions`: heading "Extra instructions for Legal"; `<ol aria-label="Extra instructions for Legal">`,
    each item: text (`whitespace-pre-line`), note from `differsNote` in `text-warning`, `Button size="sm"
    variant="ghost" aria-label="Remove instruction {n}"`. Then `Textarea` `aria-label="Anything else for Legal?"`
    placeholder "Anything the form has no question for, e.g. send a copy to procurement" + `Button` "Add". Counter
    "{words} of about 2,000 words". At ≥ `WORD_WARN`: `MessageBar intent="warning"` "About {2000 − words} words left
    before the limit." Over: `MessageBar intent="error"` "Over the limit of about 2,000 words. Shorten them, or
    {attachHint}." Empty list → "None yet. Edits you don't apply to an answer land here." Text never truncated.
- `src/app/(app)/intake/_request-overview.tsx` (CHANGED): `summaryQ = useQuery({ queryKey: ["intake-summary", id],
  queryFn: () => intakeApi.requestSummary(id) })`; passes `summary={<RequestSummarySection status=… data=…
  onRetry=… />}` to `WhatWasAsked`; appends `SUMMARY_CSS` to the page's `<style>` string.
- `_request-asked.tsx` (CHANGED): `WhatWasAsked` gains optional `summary?: ReactNode`, rendered right after
  `answers` (before "Needs info"). Nothing else changes.
- `_request-summary.tsx` (NEW): `RequestSummarySection` returns null while loading and when `text` is null and there
  are no instructions; error → "The request summary could not be loaded." + a `sel`-style Retry button. Ready:
  `.lbl` "Request in words · as filed", dim line "Built from the answers when the request was filed. The answers
  are what count." (FR-35), `.quote` with the text; then `.lbl` "Extra instructions for Legal ({n})" and an `<ol
  class="xi">` of items with `.xi-note` (warn colour) notes; dim "Written by {by_name} · {fmtDateTime(at)}". Clearly
  separate from the answers (FR-30). No edit controls anywhere (FR-39).
- New shared primitives in `src/components/ui.tsx`: none.
- Demo-mode behaviour: none — there is no `src/lib/demo.ts` in this checkout (as 012 found).

## Test strategy

### Backend pytest (`docker compose exec backend python -m pytest tests/test_request_summary.py tests/test_intake_drafts.py tests/test_request_activity.py -q`, against `legal_clm_test`)

`tests/test_request_summary.py` (T003; fixtures follow `tests/test_nda_check.py`: own org, patched
`permission_values`, filing through `client.post("/api/v1/intake/requests")`). Each test's docstring names the
failure mode.
- filing with 2 instructions + 1 accepted change that matches the filed answer → 201; `filed_summary` has the text,
  both instructions, `differs.label` = the form's label, `by_user_id`, `at`; exactly ONE `intake.summary_filed` audit
  row with `after == {"instructions": 2, "answers_changed": [{"key","old","new"}]}` and ONE timeline row with the
  frozen title; the instruction text appears nowhere in the audit row's JSON (AC-18, AC-29).
- accepted entry whose `new` ≠ filed answer, and one with an unknown key → dropped; with only those and no
  instructions → no audit row.
- text only, no instructions → text stored, `instructions == []`, no audit/timeline row; no `summary` → column null,
  nothing written (AC-18).
- 2,001 words → 422 with `LIMIT_MESSAGE`, no `intake_request` row; exactly 2,000 words → 201 (AC-22).
- malformed (`instructions: "x"`, `differs: 5`, `accepted: [1]`, NUL and control characters) → 201, cleaned
  (plain text edge case).
- duplicate: same payload filed twice within 2 minutes (second with an extra instruction) → same request id, one
  audit row, the first record unchanged (AC-26).
- `GET …/summary`: requester 200; staff with `intake:read` 200; another member without `intake:read` 404; another
  org's request 404; a request filed without a summary → the all-null shape; `audit_log`/`resource_timeline_event`
  counts unchanged by the GET (AC-20, AC-28, AC-31).
- `PATCH /intake/requests/{id}` with `field_values` leaves `filed_summary` byte-identical; the router has no
  non-GET route whose path ends in `/summary` (AC-32).
- FR-31 static guard: `filed_summary` appears under `backend/app/` only in `intake/models.py` and
  `intake/request_summary.py`; filing the same answers with and without instructions (two users) gives equal
  `description`, `field_values`, `type_label`, `priority` (AC-21, AC-23).
- 403 without `intake:create` on the GET.

`tests/test_intake_drafts.py` (T003): save with `summary_box` → list returns it; update replaces it; omitted → null
(AC-25 server half). `tests/test_request_activity.py`: unchanged; its guard passes only with the new `EVENTS` row.

### Frontend vitest (`npm run typecheck && npm run test` from `frontend/`)

- `lib/request-summary.test.ts` (T004): **guard** reading `agreement_forms.json` (same `readFileSync` as
  `agreement-forms.test.ts`) — for every form, every question key except companions has exactly one solo rule; every
  rule's keys/phrases/synonyms/variant options exist in that form; variants cover every option; rows rules name an
  existing column; no template contains `should|must|recommend|risk|allowed|advise|illegal` (FR-6). Golden lines
  for one filled fixture per form (nine). AC-1 (NDA + Mutual → "It is a mutual NDA."; full step-1 answers →
  `deal_nda`), AC-3 (gap text + required flag), AC-4 (nda_term absent for MSA, present after switching to NDA), AC-5
  (SOW six rows → "6 deliverables: A, B, C and 3 more."; "The total value of this SoW is INR 50,00,000."), AC-27 (note
  read as a sentence), empty form → gaps only, `summaryText` has no gap lines.
- `lib/summary-edits.test.ts` (T004): `parseMoney` and `parseDate` pinned tables; `matchChoice` synonyms and
  durations; AC-8 (English law → offer India → England and Wales, values untouched), AC-9 (dates by locale, "lasts 3
  years"), AC-10 (purpose rewording), AC-11 (new party → no offer, instruction noting "Initech Software Ltd"),
  AC-12 (term + law: two offers; term + "French law": one offer + note), AC-14 (decline → instruction), AC-15
  (counterparty never in a patch), AC-16 (reconcile after form change → instruction noting the new term), AC-17
  (optional delete → clear offer; required delete → line back, nothing kept), AC-7 (undo), AC-24 (finalize), AC-22
  (`wordCount`), withdrawn offer when its question stops applying.
- `intake/_summary-box.test.tsx` (T005): roles/labels (section, fieldset, line labels, offer groups, status region
  text), Enter and blur call `onCheck`, Update/Keep buttons call handlers, remove buttons, warning and over-limit bars,
  gap "(required)"/"(optional)" words, null lines → one-line note (AC-30).
- `intake/_agreement-wizard.test.tsx` (T005; mocks gain nothing new — `create`, `createDraft`, `updateDraft` already
  mocked): box in the aside on step 1 and updates on answer change, NDA panel still above it (AC-1, FR-10); small-
  screen link present; review step shows editor AND answer list (AC-6); accept an offer → `approvalPreview` called
  with the new answer (AC-13); submit body carries `summary` with text/instructions/accepted; open offer at submit →
  filed as instruction, no prompt (AC-24); `buildSummary` forced to throw (`vi.spyOn`) → submit still calls
  `create`, `summary.text === null` (AC-2); over limit → notice, `create` not called, list unchanged (AC-22);
  `saveDraft` sends `summary_box`; opening with a draft holding an instruction and an offer shows both (AC-25);
  note-to-approvers stays in `field_values.note_approvers`, not in instructions (AC-27).
- `intake/_request-summary.test.tsx` (T006): renders text, instructions, notes, writer/time; null when empty and while
  loading; error + Retry; no input or button other than Retry (AC-20, AC-32).
- `intake/_request-overview.test.tsx` (T006): mock `requestSummary`; section inside "What was asked".

### Acceptance verification (qa-engineer, T007)

| AC | Evidence |
|---|---|
| AC-1, AC-3, AC-4, AC-5 | `request-summary.test.ts` + browser on New agreement (NDA ↔ MSA) and SOW with six deliverables |
| AC-2 | wizard vitest (build throws) |
| AC-6, AC-7 | wizard vitest + browser review step |
| AC-8 … AC-17 | `summary-edits.test.ts` cases + browser walk-through of AC-8, AC-11, AC-12, AC-16, AC-17 |
| AC-13 | wizard vitest (preview re-called) + browser: accept NDA kind change, routing panel updates |
| AC-18, AC-29 | `test_request_summary` + `psql` of the filed row's `filed_summary` and its single audit row (no text) |
| AC-19 | wizard vitest + browser: remove one, file, `psql` shows it absent |
| AC-20, AC-28, AC-31 | backend GET tests + browser as requester, as Legal, and as a plain employee (404 page) |
| AC-21, AC-23 | static guard + equality test; browser: draft an NDA with instructions, contract text has none of them |
| AC-22 | backend limit tests + wizard vitest + browser paste of 2,100 words |
| AC-24, AC-26 | vitest + backend duplicate test; browser double-click Submit |
| AC-25 | draft round-trip test + browser save/resume |
| AC-27 | wizard vitest + browser: note visible on the approval card, not in instructions |
| AC-30 | `_summary-box` vitest roles + keyboard-only browser pass + VoiceOver spot check + 390 px wide screenshot |
| AC-32 | backend route/PATCH tests + request page has no edit control |
| (all UI) | light + dark screenshots of the aside box, editor, list and request section (no `bg-white`, no hex) |

## Risks & decisions

- **D1 — Sentence and matching rules in the browser (`src/lib/`), not in `agreement_forms.json` or the backend.**
  The JSON holds questions and the server validates answers from it; per-question wording already lives in the
  wizard (`UI[k].uses`), guarded by a test against the JSON — the new guard is the same idea. The server never needs
  to build sentences: FR-35 stores what the requester saw, and FR-33 keeps everything else on the answers. Rejected:
  a `say` key per question in the JSON (puts presentation into the validation file, still needs code for money/
  dates/rows, and the backend would load wording it never uses); a backend renderer (a second implementation to
  keep in step, and a network round-trip on every keystroke for a live box).
- **D2 — The editable box is one line per sentence, not one free textarea.** Attributing an edit to an answer is then
  exact (the line names its rule), which is what "certain" (FR-18) needs; it is also what a screen reader can
  navigate (FR-10). Rejected: a single textarea with a line diff (merges, splits and reorders make attribution a
  guess). New asks go in "Anything else for Legal?" below it.
- **D3 — Check on blur/Enter, never per keystroke;** the box always shows the answers, and a resolved edit moves to
  the instructions list. So the box and the answers never disagree on screen (spirit of FR-23), and nothing typed is
  lost.
- **D4 — Two JSON columns, no new table.** One record per request and one per draft, read only with their row.
  `filed_summary` is not in `serialize_request` (keeps up to 2,000 words out of every list payload and out of
  anything else that serialises a request); served by its own GET behind `get_request`.
- **D5 — The server trusts the summary text but checks what matters.** The text is the requester's own view of their
  own request and is labelled "built from the answers at filing time" (FR-35); a forged text can only mislead about
  the forger's own request, and the answers stay on screen above it. Accepted changes are kept only when they equal
  the filed answer; `differs` labels come from the form; everything is plain text, cut to size; the only refusal is
  the word limit.
- **D6 — Table summaries ("6 deliverables: …") never offer an update;** edits to them are always instructions.
  The spec allows "unless it names exactly one clear change"; with several columns per row, no change to a summary
  line is certain enough to apply, so we never guess.
- **D7 — A declined or ignored "Clear the answer?" is kept as `Remove this line: “…”`** with the differs note. A
  deletion has no words of its own; this quotes the line the requester removed so Legal still sees the ask (FR-21).
- **D8 — Numeric dates are read day-first only when the browser agrees** (or only one reading is a real day);
  otherwise not offered. That makes "15/11/2026" and, for Indian/UK users, "1/12/2026" certain, and leaves a US user's
  "1/12/2026" as an instruction rather than a wrong date (AC-9).
- **D9 — The note to approvers is read out but not editable in the box;** it is edited in its own textarea on the
  same step, so a declined edit can never turn the note into an instruction (FR-38).
- **D10 — Over the limit, Submit is refused in the browser with the plain message; the server's 422 is a backstop.**
  Nothing is cut. Within the limit nothing about the box can stop filing.
- **R1 — Concurrent double-submit.** The 2-minute duplicate check is not locked; two requests racing past it would
  both file (existing behaviour; the Submit button is disabled while busy). Extra instructions do not change this.
- **R2 — A retry within 2 minutes that adds a new instruction returns the first request without it** (the spec's
  edge case: instructions alone do not make two filings different). The requester can add it as a comment.
- **R3 — Synonym coverage is deliberately small.** An unlisted spelling never guesses; it becomes an instruction with
  a note (safe failure). The tables are frozen here and pinned by tests; widen them by plan change only.
- **R4 — Long free-text answers are shown in full** in the box; the box is never longer than the answers list it
  mirrors, except for tables, which are capped at three items (FR-7).
- **R5 — Dirty working tree** (009–012 uncommitted); build on it.

FR traceability:

| FR | Design component |
|---|---|
| FR-1 | `LiveSummary` in the wizard aside on every step; `buildSummary` |
| FR-2 | `request-summary.ts` pure rules; no API call, no AI |
| FR-3 | `lines` memoised on `values` |
| FR-4 | gap lines `not chosen yet` / `not given yet`, `(required)` vs `(optional)` |
| FR-5 | `shown(field.show, values)` before any line or gap |
| FR-6 | answer-only templates; banned-words guard test |
| FR-7 | rows rendering: count + first 3 + "and k more" |
| FR-8 | `moneyText` (INR default), `writtenDate` "15 Nov 2026", option labels/phrases |
| FR-9 | try/catch around build; filing payload unchanged except optional `summary`; `record_summary` never raises |
| FR-10 | aside order (NDA check first), `#request-in-words` link on small screens, a11y roles |
| FR-11 | `SummaryEditor` on the review step |
| FR-12 | existing answers `dl` + tables kept |
| FR-13 | `undoEdits` + "Undo my edits" |
| FR-14 | `matchChoice` + `SYNONYMS` + phrases |
| FR-15 | `parseDate`, `parseMoney`, duration matching; uncertain ⇒ no offer |
| FR-16 | free-text slot capture, prefix-optional single-slot rule |
| FR-17 | only `acceptOffer` returns a patch; applied by click |
| FR-18 | certainty rules; `differs` + `differsNote` |
| FR-19 | per-slot offers within one line |
| FR-20 | patch applied with the wizard's `set`; `reconcile`; offer removed |
| FR-21 | `declineOffer` + resolution rule |
| FR-22 | register slots never offered; no register call anywhere in the box |
| FR-23 | `reconcile` (withdraw + resolve with current answer) |
| FR-24 | deleted-line rule (clear offer / required line back) |
| FR-25 | "Anything else for Legal?" → instruction |
| FR-26 | resolution rule covers unmatched, declined, ignored |
| FR-27 | instruction text verbatim + `differs` note |
| FR-28 | `record_summary` → `filed_summary.instructions` with `by_user_id`, `at`; nothing stored when none |
| FR-29 | `ExtraInstructions` list with Remove |
| FR-30 | `RequestSummarySection` in "What was asked" |
| FR-31 | column read only by `request_summary.py`; static guard test |
| FR-32 | `WORD_LIMIT`/`WORD_WARN`, warning + refusal message, schema 422 backstop, nothing cut |
| FR-33 | drafting/routing/triage read `field_values` only; equality test |
| FR-34 | `finalize` |
| FR-35 | `summary.text` = `summaryText(lines)` from final answers; shown "as filed" |
| FR-36 | `intake_draft.summary_box`; `reconcile` on load |
| FR-37 | duplicate-retry return precedes `record_summary`; test |
| FR-38 | note line read-only in the box; note stays an answer; never in instructions |
| FR-39 | `get_request` gate on the GET; no mutation endpoint |

## Task split (proposal for /tasks)

| Task | Agent | Files | dependsOn | Wave |
|---|---|---|---|---|
| T001 Columns + migration `0076_request_summary` | db-engineer | `app/intake/models.py`, `alembic/versions/0076_request_summary.py` | — | 1 |
| T002 [P] Frontend contract: types + client wrapper | frontend-dev | `src/lib/types.ts`, `src/lib/endpoints.ts` | — | 1 |
| T003 Backend: module, schemas, route, filing call, drafts, activity row, tests | backend-dev | `app/intake/request_summary.py`, `schemas.py`, `routes.py`, `service.py`, `drafts.py`, `activity.py`, `tests/test_request_summary.py`, `tests/test_intake_drafts.py` | T001 | 2 |
| T004 [P] Sentence rules + smart-edit engine | frontend-dev | `src/lib/request-summary.ts`(+test), `src/lib/summary-edits.ts`(+test) | T002 | 2 |
| T005 Wizard box, editor, instructions + wiring | frontend-dev | `intake/_summary-box.tsx`(+test), `intake/_agreement-forms.tsx`, `intake/_agreement-wizard.test.tsx` | T004 | 3 |
| T006 [P] Request page section | frontend-dev | `intake/_request-summary.tsx`(+test), `intake/_request-asked.tsx`, `intake/_request-overview.tsx`(+test) | T002 | 2 |
| T007 Verification | qa-engineer | `specs/013-live-request-summary/verification.md` | T003, T005, T006 | 4 |

Backend gate: `ruff check .` → `alembic upgrade head` (one head `0076_request_summary`) → `pytest -q`; `make test-db`
after T001. New module ⇒ `docker compose restart backend` before the live check. After T006 the orchestrator
regenerates the repo graph.

## Decisions (answered by the user, 2026-10-27)

- **DEC-1 — The editable box is one line per sentence**, plus a box underneath for new lines, not one free textarea
  (D2 confirmed): exact attribution of an edit to its answer, and it works with screen readers.
- **DEC-2 — Over the word limit on "Signed outside the system" and "Cancel a request": just ask to shorten.** No
  file upload is added to those two forms; the other seven keep "attach it as a file under Supporting documents".

- **DEC-3 — DEC-1 reversed after the user saw it (2026-10-27): the editor is ONE small free-text box, not one
  field per line.** The "Anything else for Legal?" box and the extra-instructions list stay separate, as now.
  Frozen rules for the change (supersede D2 and the `SummaryEditor` line-per-sentence bullets):
  - `SummaryEditor` renders one `Textarea` (about 6 rows, grows with content up to a cap, then scrolls inside), labelled
    "Your request in words", prefilled with `summaryText(lines)` (one sentence per line). Gap lines and the read-only
    note-to-approvers line are not in the box; gaps stay listed under it as plain text, as now.
  - Checked on blur only (never per keystroke). The edited text is split into lines (blank lines dropped) and
    aligned to the generated sentence lines: an identical line matches its sentence; then, in order, a changed line
    pairs with the unmatched sentence it is most similar to (same rule template prefix / most shared words), only when
    that pairing is clear; each pair goes through the existing `checkLine` logic, so offers, certainty, "differs"
    notes, register picks, table lines and the read-only note behave exactly as before.
  - A generated sentence with no line left → the existing deleted-line rule ("Clear the answer?", required → put back).
  - A line that pairs with no sentence (new text, or too unclear to pair) → an extra instruction with no differs note
    (or with one, when it clearly names a slot of a sentence it couldn't pair). Never a guess.
  - Offers appear under the box, one group each ("{label}: {old} → {new}", Update answer / Keep my answer), with the
    one polite status region. While offers are open the box keeps exactly what was typed (it is never rewritten under
    the requester's cursor); once every offer is answered, the box is rebuilt from the answers and any typed words not
    applied to an answer are already in the instructions list, same outcome as D3.
  - `SummaryBoxState` keeps its shape (edits keyed by sentence id); "Undo my edits" restores the generated text.

- **DEC-4 — Two changes after the user tried the single box (2026-10-27).**
  - *Free-text answers pair by their fixed prefix.* An edited "Purpose: …" line (any sentence whose template has
    fixed words before its slot, including a one-word prefix such as "Purpose:", "Scope:", "Why:", "Reason:") pairs
    with that sentence when the typed line starts with that prefix and no other sentence has the same prefix; the slot
    then goes through the existing free-text rule (the edit becomes an "Update answer" offer). Found when the user's
    purpose edit became an extra instruction: the pairing rule only counted prefixes of two or more words.
  - *Submit waits for open offers* (spec FR-34/AC-24 changed): pressing Submit with offers open files nothing, shows
    "You have N changes waiting — update or keep each one, then submit." and moves focus to the first offer.
    `finalize` no longer turns open offers into instructions at Submit; it still runs for anything else it did.
  - The "Update answer" button stays (user kept it over applying certain edits automatically).

## Open questions

None.
