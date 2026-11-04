# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AEGIS — a contract lifecycle management (CLM) platform with an AI assistant.
Next.js frontend, FastAPI backend, Celery worker + beat, Postgres (pgvector) and Redis.

## Commands

Everything runs in Docker; the stack brings up its own Postgres and Redis.

```bash
make dev-build       # build + start everything (migrations run automatically)
make seed            # create the default admin — admin@example.com / local-dev-password
make logs            # tail all services
make migrate         # alembic upgrade head, on demand
make shell           # bash in the backend container
make nuke            # stop + DELETE all volumes (full reset)
```

Frontend <http://localhost:3000> · API <http://localhost:8000> (`/healthz`, `/docs`).
Source under `backend/` and `frontend/` is bind-mounted and hot-reloads.

`make prod` / `prod-build` use the base compose file only (built images, no bind
mounts); the default `dev` target auto-merges `docker-compose.override.yml`.

### Tests and lint

Run backend tests **inside the container** — they need Postgres, and the host
Python is usually a different version than the image. The dev tools are
installed as modules but their scripts aren't on the container's PATH, so use
`python -m`. `conftest.py` forces every `MOCK_*` on, so the dev container's
real API keys are never spent by a test run. Tests commit rows (many open their
own sessions or drive the API), so they run against a **separate database**,
`legal_clm_test`, never the dev one: `conftest.py` picks it via
`tests/_testdb.py` and refuses to start on any database whose name doesn't end in
`_test`. One-time setup: `make test-db` (creates it, runs the migrations, seeds
the dev admin); re-run it after adding a migration, and `make test-db-reset`
rebuilds it from scratch. `TEST_DATABASE_URL` overrides the target; on CI
(`CI=true`) the workflow's own service database is used unchanged.

```bash
docker compose exec backend python -m pytest -q                       # whole suite
docker compose exec backend python -m pytest tests/test_foo.py -q     # one file
docker compose exec backend python -m pytest tests/test_foo.py::test_bar -q
docker compose exec backend ruff check .                    # lint (line-length 100)
```

Frontend, from `frontend/`: `npm run test` (vitest), `npm run typecheck`
(`tsc --noEmit`), `npm run lint`.

CI (`.github/workflows/ci.yml`) runs exactly: `ruff check .` → `alembic upgrade
head` → `pytest -q`. Match it locally before pushing.

`.github/workflows/eval.yml` is manual-only and hits the real Claude API
(real cost). It seeds golden fixtures then runs `eval/run_eval.py`.

### AI quality harness

The obligation answer keys (`backend/eval/obligations/`, README there) move from draft to confirmed only by a lawyer: `--review-sheet`
writes the page they read and sign (git-ignored: it holds the contract text), and `--confirm KEY` records it and **needs a person at a
terminal** (the AI never confirms its own key; it asks for the lawyer's name, the key id and the fingerprint printed on the sheet they
signed). The confirmation is bound to the key's fingerprint, so editing the text, parties, dates, atoms or not-duties makes it a draft
again. `python -m eval.exam obligations --baseline NAME --runs 2` names a baseline only from
confirmed, unchanged keys, with a ceiling of at most $5, never over an existing name, and only if every key answered in both passes;
it writes `results/baseline-NAME/` (commit it) and `--check` re-scores it from its own files every time.


```bash
docker compose exec backend python eval/run_eval.py                 # retrieval + taxonomy (free, no LLM)
docker compose exec backend python eval/run_eval.py --faithfulness  # also generates answers (costs tokens)
```

### Backend reload caveat

uvicorn `--reload` misses **newly created** files on macOS. After adding a new
Python module, `docker compose restart backend` — edits to existing files
hot-reload fine.

## Architecture

### The document pipeline (the spine of the app)

Every upload path — web (`POST /contracts/upload`), the Word add-in, and
AI-drafted intake documents — funnels into **`create_contract_from_upload`**
(`backend/app/contract_files/service.py`). Read that one function before
changing anything upload-related; it is the orchestrator.

```
validate  →  store bytes  →  extract text  →  persist rows  →  queue AI jobs
```

1. **Validate before persisting.** MIME allowlist, magic-byte sniff
   (`validate_upload_mime` — the client's content-type is untrusted), chunked
   read with a size cap, optional ClamAV scan.
2. **Store.** `storage_service.save_bytes` — local volume or S3, chosen by
   `settings.storage_backend`. Once bytes are on disk, **any exception must
   delete them**; the orchestrator's `except` block does exactly this. Preserve
   that invariant.
3. **Extract text.** New uploads are read first by the Documents reader
   (`structure.read_with_documents_reader` → `app.documents.reader` parsers +
   `structure.build`): page furniture (running headers, page numbers,
   e-signature stamps) dropped, Word numbering and tracked insertions kept,
   and elements arrive pre-placed with a clause tree (`parent_id`, `level` =
   depth). When it returns None (scan, damaged or unsupported file, bad
   offsets) the old `extract_text` path runs unchanged; it scores quality and
   below 0.55 falls back to OCR via Reducto (the only provider; Databricks was
   removed 2026-09-29). Existing snapshots are never re-read — their offsets
   are what citations point into. Measured 2026-09-30 on 15 real contracts:
   0 offset errors, 819 clauses nested (0 before), 0.9% of words removed —
   all page furniture.
4. **Persist the row chain:** `StorageObject → Contract → ContractFile →
   ContractVersion → ContractTextSnapshot`, then the snapshot is split into
   `ContractDocumentElement` rows (headings, clauses, tables with char offsets).
5. **Queue jobs:** `metadata_extraction`, `clause_extraction`, `embeddings`.
   The contract then auto-advances INTAKE → REVIEW.

### Counterparty revisions (negotiation rounds)

`POST /contracts/{id}/counterparty-revision` saves their returned file as a new
version AND opens a `RevisionRound` against the version we sent
(`app/contract_files/revisions.py`): clauses matched with the Documents clause
matcher (`documents.reader.versions.carry_ids`, ignoring clause numbers so renumbering
isn't a change), each change classed against what WE changed in the version we
sent (changed / countered / reverted / ours / added / removed), and — from a
Word file — flagged `unmarked` when it isn't one of their tracked changes.
Decisions are per change; finishing either agrees (and completes the workflow's
waiting `counterparty` step) or writes our reply version (their text with our
wording back). Playbook findings/comments per change are looked up on read.
### Word editor (ONLYOFFICE) in the contract page

`app/contract_files/editor.py`. On when `ONLYOFFICE_URL` + `ONLYOFFICE_JWT_SECRET`
are set (dev compose sets both; production refuses the published dev secret).
The page gets a config signed with the editor's secret; the editor fetches the
file via a 1-hour link signed with OUR secret (`typ=editor_file`) and saves via
`/editor/contract-saved` — believed only if the editor signed the body AND our
`typ=editor_save` link names the user (access re-checked at save time); the
edited file is fetched only from `ONLYOFFICE_INTERNAL_URL` + the callback's
path (SSRF guard). A save becomes a normal new version (`add_version_from_upload`,
Documents reader, AI jobs). PDFs/generated versions open as the Word export of
their text. Editing during an open revision round makes that round's finish
409 (their version is no longer current) — log their latest file first.

Word text is read with the Documents reader's `_paragraph_text` (keeps tracked
insertions, `app/documents/reader/parsing/docx.py`). The reader is all that is
left of docstudio, the stand-alone editor prototype removed in AI-gateway
Phase 5; `app/documents/hierarchy.py` (AI placement of undecided clauses,
through the gateway) came with it but is not yet called from uploads.

### Drafting templates (our standard paper)

"Draft" on a request fills a template (`intake/drafting.render_document`) —
one per playbook: nda, msa, consultancy, sow, vendor, saas, dpa
(`app/drafting_templates`). The shipped text is `defaults/<key>.txt`, each
written to pass the playbook that reviews its contract type; an org's edits
are versions in `drafting_template_version` (newest wins, `body` NULL = back
to the default), made on the Templates page (`/templates`, Intelligence).
Drafted contracts are auto-reviewed by their playbook, so a template that
disagrees with its playbook gets our own paper redlined — change one, check it
against the other. Saves are refused unless every `{placeholder}` is one
`template_values` fills.

### Two clause representations — both are needed

- **`ContractDocumentElement`** (`contract_files`) — deterministic, structural,
  from the parser. *Every* block of the document in order, with char offsets.
  This is the complete-coverage backbone.
- **`ClauseExtraction`** (`contract_brain`) — LLM, semantic. Only the
  interesting clauses, classified by type, staleable.

The `clause_extraction` job labels the reader's segments (`clause_labeling`
skill, `app/ai/clause_segments.py`): the model gets each clause's number and a
300-char excerpt and answers segment id → clause type; the stored clause takes
the segment's own text and exact offsets. The old find-and-copy skill runs only
when there are no segments (a scan the reader couldn't read). Don't send the
full text back to the model to re-find clauses — that cut off at the output
limit on long contracts and stored nothing.

Keep the deterministic layer authoritative and the LLM layer an enrichment on
top. LLM extraction has real recall gaps, and a clause it silently misses would
otherwise not exist anywhere in the system.

### Offsets anchor to the snapshot

`char_start`/`char_end` on both clause tables, and the embedding metadata, are
offsets **into `ContractTextSnapshot.text`**. Re-running OCR with a different
model version produces different text and silently invalidates every stored
offset and citation. Treat the snapshot as a pinned artifact, not a cache.

### Jobs

`create_job` → `dispatch_job` → Celery. All handlers live in one dispatch chain
in `backend/app/jobs/tasks.py` (`_run_ai_job`), keyed on `job.job_type`.

Jobs take a `SELECT ... FOR UPDATE` lock and short-circuit if already
RUNNING/SUCCEEDED, so a redelivered broker message can't double-spend AI calls.
Idempotency keys are `{job_type}:{version_id}:{snapshot_id}`. Periodic work is
in `celery_app.conf.beat_schedule`.

**A failed job says why, in `backend/app/ai/outcomes.py`.** `classify(exc)` picks one outcome
(truncated · refused · unusable · transient · permanent · save_failed · internal) by exception
*type*, never by message text, and `failure_from_exception` builds the payload stored on
`job_run.metadata_json["failure"]`. `job.error_message` is that payload's plain sentence — never
`str(exc)`, because a failed insert prints the contract's own words (the stack stays in
`error_stack`). Every writer that fails a job writes both, and a job that succeeds, is re-queued
or starts RUNNING drops the old `failure`. "The AI service" wording is only for errors from the
AI provider (`integrations/claude.py` wraps a stalled or dropped stream so it counts); other
services read "a service this step depends on". A guard whose message is already a plain sentence
raises `PlainError`; one a retry cannot fix (a switched-off skill, a missing key) raises
`SetupError`. A failed model call is never replaced by a guessed answer. `python -m
eval.ai_failure_report --days 30` reads the ledger for the failure rate.

**How often anything retries is written once, in `app/core/retry_policy.py`** (its docstring is the budget table;
`tests/test_claude_retry_policy.py` pins every number to the setting that produces it). The Claude client
(`_post_messages` in `integrations/claude.py`) tries up to 5 times when the provider says no before any answer
(HTTP 429/5xx/529), waiting the provider's `Retry-After` when it is 30 s or less, else 2·4·8·16 s with jitter, at most 45 s
of sleeping. A `Retry-After` that does not fit is not slept on: the error goes up carrying `failure.retry_after_s`.
Once the answer has started (any stream event), or a try ran 20 s or more, it is one retry and no more; no new try
begins after 240 s (a try already under way is not cut short). Only calls made inside a background job are patient (`run_ai_job` sets `retry_policy.patient`); any
other call gives up after 2 tries and 5 s, because a person or a held thread is waiting. Only the AI provider's own bad
moments are retried, never `SoftTimeLimitExceeded` (an ordinary Exception: a loop that retried it would start a second
paid call after the worker was told to stop). A stream that ends cleanly with no stop reason is treated as a dropped
connection. Settings: `claude_retry_tries`, `claude_retry_budget_seconds`, `claude_retry_inline_tries`,
`claude_retry_inline_budget_seconds`, `claude_retry_base_wait_seconds`, `claude_retry_deadline_seconds`;
`claude_max_retries` and the two `claude_retry_*_backoff_seconds` now serve only DocuSign, SendGrid, Resend and the exam's
batch calls. **A machine does not re-run a job that failed on a model call** (only a transport error, such as OCR or
e-signature, goes through Celery's `autoretry_for`, 3 re-runs): a person presses Retry, and `auto_retry` is false for the
soft limit and the daily budget. A job that hits Celery's soft time limit (`_mark_job_soft_limit`) is failed with a plain
reason (a tabular job's cells are failed with it), except `template_check`, whose re-run after the lease ends the check
as interrupted without a new paid review. Reclaim also closes every AI run still "running" past the lease
(`_close_orphan_skill_runs`), whatever job it belonged to; a chat answer's run only once that answer has ended.
Not done, on purpose: re-queuing a model-failed job by countdown (a review found it would strand jobs when the broker
publish fails, override Cancel, leave tabular reviews finalized wrong and make auto-review read "interrupted"),
retrying inside the chat stream, metering abandoned costly tries, the circuit breaker's removal.

**Did an Active contract's obligations really get extracted? Ask `extraction_state(db, contract)`**
(`app/obligations/extraction_audit.py`): no_text · done · none_found · running · failed · never · stale. It reads
`job_run` only (every `obligation_extraction` job whose `metadata_json.contract_version_id` is the contract's current
version, whatever the key suffix) and stores nothing. A succeeded job with no obligations is `none_found`, a valid
answer, never a failure; a job queued for 2 h or running past its lease counts as failed, and a queued job's age counts
from `updated_at`, because a retry re-queues the same row. The reason shown is the job's own plain `failure.reason`,
never `error_message`. The lifecycle row (`workflows/lifecycle_rows.py`) and the Retry button
(`POST /obligations/extract`: 409 when already read, no second job while one is in flight, restarts the job that ended
badly) both use it; Retry is hidden when the failure says `retryable=false`.

**Which obligations need a date? Ask `date_state(ob)`** (`app/obligations/dates.py`, pure, worked out on read, nothing
stored): dated · not_read (no deadline block: extracted before dates were counted) · no_fixed_date (an ongoing duty
whatever stopped its end being counted, an "if X happens" duty, or a date a person cleared) · waiting_event (the reason
starts with `EVENT_WAIT`, which `obligation_deadline` writes) · needs_person (any other reason). "Needs a date" =
needs_person + not_read, open obligations only; waiting and no-fixed-date are never alarms. The list, the tiles
(`GET /obligations/summary`) and the `?date_state=` filter all read `visible_obligations(user)`, so they cannot
disagree; the page reads `date_state` from the server and never matches reason text.

**Ask Aegis speaks from the same two answers** (`app/obligations/assistant.py`; the tools are in
`app/ai/tool_runtime.py`). `list_obligations` reads `visible_obligations` (a closed or archived contract's duties are
out, as on the page) and each row carries `date_state` + `date_note`; the result adds the portfolio's `dates` /
`needs_date` (over the whole set, not just the due window), `unread_contracts` (Active, accessible contracts whose
`extraction_states` is failed / never / no_text / stale / running) and one `summary` sentence telling the model that no
date is not "not due" and that an unread contract has no obligations *on file*, which is not "none". `my_attention_items`
adds one item per contract with obligations that need a date and one per contract not read (not ones being read now).
`extract_obligations` goes through `ObligationsService.extraction_job`, the same decision as Retry. The 409 refusals
(already read, cancelled a moment ago, no readable version) come back as `status: "not_started"` with a sentence, never
as an exception (the tool always runs after the user confirms, and a raise there ends the run FAILED with the model
never hearing why); an in-flight job is `already_running`; otherwise the job is committed BEFORE `dispatch_job` (a
worker that wins the race would strand it as "already running") and the answer is `queued`. `_replayed_call` does not
replay a succeeded `extract_obligations`, so a repeat is asked again. `needs_reading` checks every Active contract (in
batches), and the per-contract attention items are added after the signature items so the cut at 40 cannot drop
"Awaiting signature". The system prompt is untouched: the tool descriptions and the `summary` sentence carry the wording.

**A daily check makes sure every Active contract's obligations were read** (`app/obligations/extraction_sweep.py`,
beat `audit-obligation-extraction` at 07:58 UTC, off the */5 reclaim tick; thin task `audit_obligation_extraction`). It
acts only on `extraction_state`: `never` → starts ONE read (plain activation key, audit row + timeline event, commit before
`dispatch_job`, then commit again to persist the task id), at most 6 a day, never past half of the day's AI budget
(`cost_guard.tokens_left`; unknown = skip; each read already queued in the run counts as 120,000 tokens, since a queued read
has spent nothing yet), never while 3 of its own reads in 48 h failed and none finished, and only when
`obligation_audit_start_jobs` is on (**default off**: the dev stack has a real key; set it in production on purpose).
`failed` (once SETTLED: an hour after it was given up on, because Celery's autoretry, reclaim and the Jobs page's reaper
still work on the others), `no_text`, `stale` and a none-found read of poorly read text → the owner is told ONCE per
(contract, version) through `notifications/once.py::notify_once` (de-dup on user + event type + key; the ~21 other places
that write a `Notification` are left alone), at most 10 new notices to any one person a run (an owner, or an admin standing
in for an owner who left; one already sent earlier is not counted), and each admin gets one digest a day, only when
something happened. It never
re-runs a failed job and never replaces a stale contract's obligations. The first run is a BASELINE: it stores the existing
problems as `(contract, version)` pairs in `AdminSetting` `obligation_audit_baseline` (`is_secret`: it lists contract ids; pairs,
not times, which a retry or the reaper would move), tells admins once, and starts and sends nothing else; those problems stay red on the page and are
never pushed. A stored value that is not ours skips the run, it never re-baselines. Who is told is checked with
`_contract_visible_silently` (the other check writes an `access.denied` audit row for people who never tried to open the
contract); no recipient may see a title or reason for a contract they cannot open, and a notice quotes only the job's plain
`failure.reason`. The notice's last sentence follows what the row offers (Retry only when `retryable`, Re-read for stale).
Locks are `pg_try_advisory_xact_lock` in a key space of their own, never a lock on the Contract row (the audit chain's lock
is taken first by contract edits, so a row lock here could deadlock with the 07:50 close batch). `ExtractionState` gained
`failed_since` (always set: a lost or dead job has no finish time) and `cancelled`, and a snapshot with no text in it now
reads as `no_text` in both loaders and in `extraction_job` (the Retry endpoint and the assistant's tool refuse to pay for
it): "no text" is `has_text_clause()`, a database regex for one non-whitespace character, because a Python `.strip()` and
Postgres' `trim()` disagree about newlines and a blank scan is stored as one newline per page.

### Suggested changes for playbook issues

A playbook review (`execute_playbook_run`) records issues (`PlaybookDeviation`). For an
issue backed by a rule with usable document wording it also writes the tracked change
itself, tagged with the issue (`playbook_deviation_id` in the edit's `citation`). The rest
(no rule, or no wording to use) come out with no change, and
`backend/app/contract_files/issue_fixes.py` fills them in: job type `issue_fix_drafting`,
one AI request per issue (the assistant's edit engine, `tool_runtime._edit_contract`), one
after another, each saved as it finishes and tied to its issue. One request for all the
issues can't do that: its changes name no issue, so they show up in Review as loose cards.

It starts after a review that made redlines (`POST /playbooks/{id}/runs`, the chat's
`redline_against_playbook`, `auto_review_contract`; not for a `test_mode` run), or by hand
from the Review tab (`POST /contracts/{id}/edits/draft-issue-fixes`; `GET` the same path
for progress). `AUTO_DRAFT_ISSUE_FIXES` switches off only the automatic start;
`AUTO_DRAFT_MAX_ISSUES` caps one job. Running it again is safe (an issue that already has a
change is skipped), and one failing issue never stops the others. "Has a change" is
decided as the page decides it (`frontend/src/lib/suggestions.ts`, `editsForIssue`), and
`fix_instruction` in `issue_fixes.py` is the sentence the page's own button sends
(`devInstruction` in `_clm-workspace.tsx`): change each pair together. In dev the worker restarts itself when
`app/` changes (watchfiles); the API needs `docker compose restart backend` for a new module
(see the reload caveat above). A production deploy restarts both.

### Retrieval (Contract Brain / Ask Aegis)

`hybrid_sources` (`backend/app/contract_brain/retrieval.py`) is the **single**
retrieval used by both `/search` and `/ask`, so an answer's sources can never
diverge from what Find shows. Four lenses: pgvector cosine over
`ContractEmbedding`, Postgres FTS over `ClauseExtraction`, FTS over raw text,
and the knowledge graph. A cross-encoder reranker over-fetches then narrows.

Vector search is restricted to `contract_version_id ==
Contract.current_authoritative_version_id` so stale versions are never
retrieved. `resolve_scope_contract_ids` gates scope (portfolio / single
contract) and enforces access. There are no Matters (removed 2026-09-29,
migration 0062): contracts aren't grouped, walls are contract-scoped, and
access comes only from owner/creator, admin, pending approver or a grant.

Embeddings: `BAAI/bge-small-en-v1.5`, 384-dim, local via fastembed. Switching
`embedding_provider` to voyage means 1024 dims — a pgvector column migration
plus a full re-embed, since the column dimension is fixed. If fastembed is
unavailable the code **raises** rather than writing meaningless random vectors,
unless `allow_mock_embeddings` is explicitly on.

`contextual_chunking` prepends `[Contract: {title}]` to the **embedded** text
only — never to the stored `chunk_text`, which is what gets quoted back as
citations. Changing chunking or this flag leaves a mixed corpus; run
`backfill_embeddings` to re-embed.

### AI skills

Skills are declared in `backend/app/ai/registry.py` and executed by
`AIController.run_job_skill` (`app/ai/controller.py`). Prompts are versioned in
`app/ai/prompt_versions.py`; outputs are Pydantic schemas in `app/ai/schemas.py`.
Each skill has a feature flag. Add a skill by registering a spec, not by
calling the Claude client directly.

`backend/app/integrations/claude.py` is the only place that talks to Anthropic.
Mock integrations default OFF in code and are switched ON via `MOCK_*` env for
local dev and tests.

### Cross-cutting model conventions

Models compose mixins from `app/core/database.py`: `TableNameMixin` (derives
`__tablename__`), `IdMixin` (uuid string PK), `OrgScopedMixin` (`org_id` on
nearly everything — **keep scoping every query by it**, see below),
`ActorTrackedMixin`, `TimestampMixin`, `SoftDeleteMixin` (includes `legal_hold`).

**This app is single-tenant.** `org_id` is dormant scaffolding, not a live
tenant boundary. `create_first_admin` (`app/auth/service.py`) selects
`Organization` with no filter and returns 409 once one is `setup_complete`, and
`register_user` finds *the* org the same way — there is no code path that
creates a second one, and no request carries a tenant selector.
`app/intake/models.py` says the same thing in its own docstring.

Keep writing `org_id` and keep filtering on it anyway: it costs nothing, it is
what would make multi-tenancy possible later, and the access code
(`accessible_contract_filter`, ethical walls, clearance) filters alongside it
for real. Just don't reason as though it isolates tenants today — it doesn't.

Intake has **no sanctions or conflict screening** (removed 2026-09-28: it was
name-only against one US list and blocked nothing). `app/intake/screening.py`
now computes only the counterparty relationship note; the `screening` column
and `intake_screening` job kept their names. Sanctions checks are expected to
happen in the client's own screening tool.

State-changing operations write both `write_audit_log` (immutable compliance
record) and `write_timeline_event` (user-visible history) from
`app/core/audit.py`. Follow that pattern for new mutations.

`write_audit_log` takes an app-wide **advisory lock held until the transaction
commits**. Never write an audit row on a *second* session inline while the
request's own session may already hold it — that request then waits on itself
and every writer in the app freezes (happened 2026-09-28; `core/authz.py`
now writes off-thread). Don't hold an audit write open across slow work (AI
calls commit first). Sessions carry `idle_in_transaction_session_timeout`
(`db_idle_transaction_timeout_ms`, 6 min) as the backstop.

Every new model must be imported in `backend/app/models.py` or Alembic
autogenerate and `create_all` will not see its table.

### Intake triage: form vs free text

A request filed on one of the nine agreement forms (`field_values.request_form`)
is **decided by the form** (`triage_agent.form_triage`): category from the form,
the requester's priority, `needs_info` false, the workflow from "Used for" or the
criteria match, and it auto-starts. The model only writes `ai_triage.read` (the
`intake_form_read` agent: mismatches with the form, bespoke asks, negotiation
points) — advisory, never re-routes or holds. Free text (email, Teams, chat,
notices) keeps full model triage. The owner is never the requester
(`pick_from_pool(exclude_user_id=...)`).

The form's value (in `default_currency`, INR — the forms have no currency
field), dates and parties reach the contract through `apply_request_facts`,
passed as `on_created` to `create_contract_from_upload` so they are on the row
before the AI jobs read it. `field_sources` precedence: user > form > ai.



`IntakeTeam` (Admin → Teams, `app/intake/teams.py`) is the ONLY group of people
that does work: it owns new requests (expertise match, else the one
`is_default_intake` team), does workflow steps (`step.config.team_id`), and
approves (`ApprovalRequest.approver_team_id`, any one or all members). Approver
groups, "pools", the builder's fixed department list and the token→group maps
were merged into it (migration 0056). A workflow step names a team by id — never
add a name/role lookup in front of it. Roles are permissions only; Authority
limits the value someone may approve or sign.

### Workflow steps sit under lifecycle stages

Every workflow step carries `stage` (intake · drafting · review · approval ·
signature · active · closed — the `ContractLifecycleStage` values). Rules live in
`app/workflows/stages.py`: draft/approval/signature steps have a fixed stage,
counterparty is drafting or review, steps follow lifecycle order, parallel steps
share a stage. `_clean_steps` fills missing stages and 422s a bad order. The
lifecycle view (`frontend/src/components/lifecycle-view.tsx`, on the request
overview and the contract page) lists steps under each stage; `currentStage`
there says where a request/contract is (live run's current step → contract
stage → Intake/Closed).

The engine moves the contract: a Drafting/Review step starting brings it forward
to that stage (`_enter_step_stage`), Approval/Signature steps move it in their own
branches, signing reaches Active. A rejected approval (or a signature pulled back)
**sends the workflow back** to the step's `return_to`, else the last Review step
that ran (`_send_back_after_rejection`), recording a `kind="return"` comment the
lifecycle bar shows; it only stops the run when there's no earlier Review/Drafting
step. **Approvals start only from a workflow's Approval step** — the manual
submit route, the intake submit route and the assistant's `submit_for_approval`
tool were deleted (2026-09-29).

Stages that aren't mostly workflow steps get their rows from their own tables —
request events (Intake), signers (Signature), obligations + renewal (Active),
expiry (Closed) — via `app/workflows/lifecycle_rows.py` (`GET /workflows/lifecycle`).
**The last step of every starter workflow is "Activate"** (an agent-less `ai_task`, wording in `workflows/stages.py`
`ACTIVATE_STEP_*`; it was "Activate & track" / "start obligation tracking" until migration 0078). It checks nothing:
signing queues the reading of the obligations and the Active stage's row above says how it went, so a step called
"track" showing a green Done over a red "Obligation extraction failed" was a lie. In the Active stage the engine writes a
note on it that is true of the contract then (read on their own / not Active yet, nothing read / no contract linked):
"Signed outside the system" has no signature step and reaches it with a contract that is not Active. Migration 0078
rewrites the name and (only when still the old sentence) the instructions in `workflow.steps`, the pinned
`workflow_run.steps` snapshots and `workflow_step_run.step_name`; nothing keys off the name. A step runs no yield or wait on the
extraction (a provider outage would park every request signed that day, and the request's SLA would blame the requester).
**Every contract gets a workflow:** one uploaded straight into the CLM has no
request, so `start_flow_for_contract` files one (`file_request_for_contract`,
source `upload`) and starts it; a `clm_draft` step on a run that already has a
contract is skipped. Approval authority (Admin → Authority) is shown, not just
enforced: `authority_limits` / `approval_authority_note` feed Teams members and
each run's approval steps (`authority_note`); nothing shows while no policy exists.

**Workflows sit under an agreement type** (`criteria.used_for`: `[{"form":
"<form key>", "agreement_type": <New-agreement kind or null>}]`) and carry
**"Chosen when" conditions** (`criteria.conditions`: `{field, op: is|is_not|
under|at_least|between, value[, value2][, currency]}` — `field: "value"` is the
request's money figure). A **form request only ever gets a workflow of its type
whose conditions all hold** (`select_flow` / `_pick_used_for`): a kind-specific
workflow beats a whole-form one, then more conditions, then `eval_order`. A
blank answer or a value in another currency never meets a condition. If none
fits, the request gets **no** workflow and waits for a person to pick (the
request page shows a picker) — no word match, no catch-all. Word criteria
(`match_type`/`match_keyword`) now only serve untyped workflows for requests
without a form (email, chat). `_clean_conditions` refuses conditions on
questions the form never asks; two enabled workflows with the same type AND
the same conditions are a 409. The list page groups workflows by type; the
builder's settings panel edits type + conditions (`frontend/src/lib/workflow-types.ts`).
The starter workflow for every agreement type lives in
`app/workflows/contract_library.py` (added by "Seed defaults"; value tiers in
INR are starting points). `test_contract_workflow_library.py` checks every
form/kind routes to exactly one of them — keep it passing when you change a tier.

**There are no request types** (removed 2026-09-29, migration 0058). The nine
agreement forms in `app/intake/agreement_forms.json` (mirrored by
`AGREEMENT_FORMS` in the frontend) are the only request forms: a request names
its form in `field_values.request_form`, filing validates against
`form_fields(key)`, and non-form requests (email, chat, the general form) carry
only a `type_label` from `agents.BUILTIN_EXTRA_TYPES`. Don't reintroduce a
type table or `request_type_id`.

The forms' questions (labels, options, required, `show` rules — a question
applies only when every rule matches) live ONLY in `agreement_forms.json`; the
wizard (`frontend/.../intake/_agreement-forms.tsx`) loads them from
`GET /intake/forms` and adds just layout (which step, which control, what each
answer drives). Filing drops answers to questions that don't apply. Change a
question in the JSON; `agreement-forms.test.ts` fails if a question has no step.
A finished amendment / renewal / termination / novation workflow writes its
change onto the parent contract (`apply_change_request`). Word criteria (`match_type`/`match_keyword`) are only the
fallback for requests no workflow is set up for, and neither they nor the triage
AI may override a "Used for" pick (`flow_used_for`, `used_for_suggestion`).

### Routes and permissions

`app/main.py` registers ~36 routers under an API prefix. Route handlers are thin;
the work lives in the sibling `service.py`. Endpoints authorize with
`Depends(require_permission("resource:action"))` (`app/core/deps.py`).

Note: `has_permission()` may be locally patched to always return `True` in a
working checkout — verify before assuming RBAC is exercised in dev.

### Naming history (older notes and code comments may be stale)

- Engine flows were renamed to **workflows** — API `/workflows`, module
  `app/workflows/`, tables `workflow*`.
- The Prompt Library's "workflows" are **`prompt_library`** — API
  `/prompt-library`, frontend route `/prompts`, tables `prompt*`.

Some test filenames still say `flows` (e.g. `test_flows_ai_task_agents.py`)
while importing from `app.workflows.service`.

### Frontend

Next.js App Router under `frontend/src/app/(app)/`. One shared sidebar
(`AegisRail`) for every route — never add a per-screen sidebar. Theming uses the
`slate-*` scale in `globals.css`; **never use `bg-white` or hardcoded colors**,
they break dark mode.

## Migrations

Revisions live in `backend/alembic/versions/`. Feature branches have produced
multiple heads before — check `alembic heads` after merging, and add a merge
revision rather than editing existing ones. (The count used to be written here
and was wrong within a few commits; `alembic heads` is the answer that stays
true.)

## Conventions worth matching

Comments in this codebase explain *why*, and tests carry a docstring naming the
failure mode they guard rather than restating the assertion. Follow both.

`ponytail:` comments mark deliberate simplifications with a known ceiling and an
upgrade path — read one before "improving" the code it sits on.
