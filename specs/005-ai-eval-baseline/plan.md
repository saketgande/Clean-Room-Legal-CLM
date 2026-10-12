# Implementation Plan: AI Evaluation Baseline ("the exam")

Feature ID: 005-ai-eval-baseline
Spec: ./spec.md (must be APPROVED)
Created: 2026-10-06
Revised: 2026-10-06 — owner decisions D-11..D-14 (right-sized exam, batch delivery FR-41,
1% error rule, notice period), model-aware pricing, Reducto price in the private config
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

The exam is a **manual, owner-only command-line tool** (`python -m eval.exam …`, run inside
the backend container) in a new package `backend/eval/exam/`. It is internal tooling, not an
app feature: nothing under `backend/app/` imports it, there is no route, no page, no Celery
task and no schedule (FR-31). It drives **today's unchanged app code** (the same functions
the upload pipeline, the job chain, Contract Brain ask, playbook review and intake triage
use) against a **separate throwaway database**, and only measures.

```
 owner shell ──► python -m eval.exam <cmd>
                   │  1. env.isolate()  (BEFORE any app.core.database import)
                   │     settings.database_url  -> legal_clm_eval  (default engine = exam DB)
                   │     settings.claude_api_key -> EVAL_CLAUDE_API_KEY from eval/private/exam.env
                   │       (refuse if unset or equal to the real key)
                   │     storage_root -> backend/eval/.work/storage ; email/DocuSign/TM mocks forced on
                   │     DISABLE_RBAC off ; daily token cap off ; Sentry/Voyage/Cohere off ; Celery -> memory://
                   │  2. owner re-authentication (EVAL_OWNER_EMAIL + AEGIS password) for gated commands
                   │  3. estimate (expected, list price, worst case; batch wait up to 24 h)
                   │     -> interactive per-run go-ahead + ceiling (paid commands)
                   ▼
   ┌──────────── exam process ─────────────────────────────────────────────────────┐
   │ evaldb: DROP/CREATE legal_clm_eval, alembic upgrade head, bootstrap org,       │
   │         library + exam playbooks, workflow snapshot, ingest documents (free)   │
   │ runner, two dependency phases:                                                │
   │   phase 1 = clause labelling + metadata (+ routing triage)  → brain ingestion │
   │   phase 2 = playbook review, Contract Brain answers (Q&A, portfolio)          │
   │   each phase, public + constructed units:  CAPTURE (run app code, record the   │
   │   request at the Claude boundary, abort) → ONE Message Batch → poll → REPLAY   │
   │   (re-run the same app code, the stored batch result is returned at the        │
   │   boundary).  Owner-contract units: normal synchronous calls, never batched.  │
   │ claude.exam_meter hook = the single paid-call choke point: ceiling, capture,  │
   │   replay                                                                       │
   └──────────────┬───────────────────────────────────────────────┬────────────────┘
      exam DB (legal_clm_eval):                   live DB (legal_clm) — ONLY:
      contracts, snapshots, elements,             • a_i_call_log rows copied with eval_run_id
      embeddings, clauses, jobs, ledger             (+ batched) set, metering columns only [FR-34]
      rows (full), skill runs                     • audit_log rows  eval.*  [FR-39]
                                                  (+ reads: owner User, Organization id,
                                                   Workflow list, eval.* audit rows)
      backend/eval/private/  (git/docker-ignored): exam.env, owner files, frozen text, OCR
      cache, sheets, owner keys, routing keys, per-run results.json / detail.md / state.json
      backend/eval/exam/content/ (committed): public + constructed items/keys, playbooks
      backend/eval/exam/reports/ (committed): baseline / comparison reports, numbers only
```

Touched existing modules (all small, all inert for the running app):

| File | Change | Why |
|---|---|---|
| `app/core/models.py` | `AICallLog.eval_run_id` (nullable, indexed), `AICallLog.batched` (bool, default false) | tag exam traffic (FR-34); price batch rows at 50% (FR-41) |
| `alembic/versions/0071_ai_call_log_eval_run.py` | add both columns + index | — |
| `app/integrations/claude.py` | `exam_meter` hook (None in the app) at the single paid-call choke point; `complete_json()` (structured outputs); four thin Message Batches methods | ceiling (FR-32), batch capture/replay (FR-41); pre-fill/drafting with claude-sonnet-5-5, which rejects forced tool use and non-default temperature (FR-16, FR-21) |
| `app/ai/gateway/ledger.py` | `AICallContext.eval_run_id` (default None) written by `record_call` | tag the exam's direct calls |
| `app/analytics/routes.py` | `traffic=real\|exam` filter; model-aware rate table; `_cost` → public `token_cost(..., batched=)`; groups by `batched` | FR-34, FR-41; owner's price fix; one price table reused by the exam |
| `frontend/src/app/(app)/ai-usage/page.tsx` (+ types/endpoints) | Real usage / Exam traffic toggle | FR-34 "viewable on its own", A-3 |
| `.gitignore`, `backend/.dockerignore` | ignore private/generated exam data | FR-36 |

The exam's own values (`EVAL_CLAUDE_API_KEY`, `EVAL_OWNER_EMAIL`, `REDUCTO_USD_PER_CREDIT`)
are **not** app settings: they live in the git- and docker-ignored
`backend/eval/private/exam.env`, read only by the exam process through a small
`ExamSettings(BaseSettings)` in `eval/exam/env.py` (decision 20). No `app/core/config.py`
change.

Convention templates: the exam package follows the existing `backend/eval/` tooling location
(branch `ai-redesign/step-0` layout, ported selectively); the process bootstrap mirrors
`backend/tests/conftest.py` (environment pinned before app import, Celery pointed at
`memory://`); ingestion reuses `app/contract_files/service.py` and the `app/jobs/tasks.py`
dispatch chain's handler functions; playbooks reuse `app/playbooks/library.py`.

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | `backend/app/core/models.py` (AICallLog `eval_run_id` + `batched` only), `backend/alembic/versions/0071_ai_call_log_eval_run.py`. (No `backend/app/models.py` change: no new model.) |
| backend-dev | **Platform (existing files):** `backend/app/integrations/claude.py`, `backend/app/ai/gateway/ledger.py`, `backend/app/analytics/routes.py`, `.gitignore` (repo root), `backend/.dockerignore`. **Exam package (new):** `backend/eval/exam/__init__.py`, `backend/eval/exam/__main__.py`, `backend/eval/exam/env.py`, `backend/eval/exam/content.py`, `backend/eval/exam/build.py`, `backend/eval/exam/owner.py`, `backend/eval/exam/routing.py`, `backend/eval/exam/sheets.py`, `backend/eval/exam/evaldb.py`, `backend/eval/exam/runner.py`, `backend/eval/exam/score.py`, `backend/eval/exam/report.py`, `backend/eval/exam/README.md`, `backend/eval/exam/content/**` (generated by `build`, committed: `datasets.json`, `documents.json`, `sample.json`, `manifest.json`, `items/review.json`, `items/extraction.json`, `items/qa.json`, `items/portfolio.json`, `playbooks/nda_checklist.json`, `playbooks/cuad_checklist.json`, `playbooks/cp_msa.json`, `playbooks/cp_dpa.json`), `backend/eval/exam/reports/README.md`. **History:** `backend/eval/results/superseded/` (the 7 ported branch result files) + `backend/eval/results/superseded/README.md`. **Tests:** `backend/tests/test_exam_content.py`, `backend/tests/test_exam_score.py`, `backend/tests/test_exam_compare.py`, `backend/tests/test_exam_sheets.py`, `backend/tests/test_exam_gate.py`, `backend/tests/test_exam_claude_client.py`, `backend/tests/test_exam_batch.py`, `backend/tests/test_exam_ledger.py`, `backend/tests/test_exam_isolation.py`, `backend/tests/exam_isolation_driver.py` (subprocess driver, not collected), `backend/tests/exam_fake_anthropic.py` (shared fake Anthropic client, not collected), `backend/tests/test_analytics_ai_usage_traffic.py`. |
| frontend-dev | `frontend/src/app/(app)/ai-usage/page.tsx`, `frontend/src/lib/endpoints.ts` (`aiUsageApi.summary` only), `frontend/src/lib/types.ts` (`AiUsageTraffic`, `AiUsageSummary.traffic` only) |
| qa-engineer | `specs/005-ai-eval-baseline/verification.md` |

Not touched (by design): `backend/app/models.py`, `backend/app/core/config.py`,
`backend/app/jobs/tasks.py` (no Celery task — FR-31 forbids automatic runs),
`frontend/src/components/ui.tsx` (no new primitive), `backend/eval/run_eval.py` /
`seed_golden_fixtures.py` / `golden.json` / `audit_pipeline.py` (the old harness stays as it
is; the exam does not use it), drafting templates and playbooks (read only),
`.github/workflows/*` (no CI trigger).

Shared-hotspot note (serialize these):
- `backend/app/core/models.py` — db-engineer task only.
- `backend/app/integrations/claude.py` + `backend/app/ai/gateway/ledger.py` — one backend task.
- `backend/app/analytics/routes.py` — one backend task (depends on the migration task).
- `frontend/src/lib/types.ts`, `frontend/src/lib/endpoints.ts` — one frontend task.
- `.gitignore` + `backend/.dockerignore` — one backend task, **wave 1, before any real
  contract is placed on disk** (FR-36).
- `backend/eval/exam/content.py` — one task in wave 1; every other exam module imports its
  frozen models/paths and must not edit it.

Suggested waves (final split is `/tasks`' job): W1 = migration · ignore files ·
claude/ledger (+ `exam_fake_anthropic.py`) · content.py · superseded port · frontend.
W2 = analytics · build · env · sheets · score. W3 = owner · routing · evaldb · report.
W4 = runner (incl. batch). W5 = `__main__` + isolation integration test.

## Interface freeze (contract-first — fixed before implementation starts)

### API contract

One HTTP change. No new endpoint (there is deliberately no in-app exam page — spec "Out of
scope").

| Method | Path | Permission | Request | Response (200) | Errors |
|---|---|---|---|---|---|
| GET | /api/v1/analytics/ai-usage?days=30&traffic=real | `admin_panel:access` (unchanged) | query `days` int 1..365 (unchanged), **`traffic`: `"real"` (default) \| `"exam"`** | existing `AiUsageSummary` + `"traffic"` | 401, 403, 422 (bad `traffic`/`days`) |

```json
// GET /api/v1/analytics/ai-usage?days=30&traffic=exam  — response (only new key shown in full)
{
  "currency": "USD",
  "window_days": 30,
  "traffic": "exam",                       // NEW: echoes the filter
  "totals": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0,
             "cache_write_tokens": 0, "total_tokens": 0, "cost": 0.0, "cost_per_call": 0.0},
  "by_operation": [/* unchanged shape: one entry per (prompt_key, model) */],
  "by_category": [/* unchanged shape */],
  "daily": [/* unchanged shape */],
  "rates": [{"model_family": "claude-sonnet-5-5", "input_per_m": 2.0, "output_per_m": 10.0}
            /* same shape; now lists the model-specific rows first, then family fallbacks */]
}
```

Semantics: every query keeps `AICallLog.org_id == current_user.org_id`. `traffic=real` adds
`AICallLog.eval_run_id IS NULL`; `traffic=exam` adds `AICallLog.eval_run_id IS NOT NULL`.
The default is `real`, so every existing caller's real-usage figures exclude exam traffic
from the moment the column exists (FR-34). Costs: each `(prompt_key, model, batched)` group
is priced with `token_cost(..., batched=…)` and folded into the existing one-entry-per
`(prompt_key, model)` shape, so the response shape is unchanged. Read-only: no audit row.

Rate table (`_RATES`, USD per million tokens, longest matching prefix wins, so dated
snapshots resolve to their model):

| Prefix | Input | Output |
|---|---|---|
| `claude-opus-5-5` | 4.00 | 20.00 |
| `claude-sonnet-5-5` | 2.00 | 10.00 |
| `claude-sonnet-4-6` | 3.00 | 15.00 |
| `claude-haiku-4-5` | 1.00 | 5.00 |
| `claude-opus` (older) | 5.00 | 25.00 |
| `claude-sonnet` (older) | 3.00 | 15.00 |
| `claude-haiku` (older) | 1.00 | 5.00 |
| `claude-fable` | 10.00 | 50.00 |
| anything else | 3.00 | 15.00 |

`token_cost(prompt_tokens, completion_tokens, model, cache_read=0, cache_write=0, *,
batched=False)` = `(in·rate_in + out·rate_out + cache_read·rate_in·0.1 +
cache_write·rate_in·1.25) / 1e6`, then `× 0.5` when `batched` (the provider's batch
discount applies to every token type, multipliers stack).

Audit actions written by mutations: none for the HTTP change. All exam audit actions are
listed under "Audit trail" below.

#### CLI contract (the exam's "API")

Run inside the backend container from `/app` (= `backend/`):
`docker compose exec backend python -m eval.exam <command> …` (TTY required for gated
commands; `-T` sessions are refused). Exit codes: `0` done, `2` refused (`ExamRefused`:
message printed, nothing paid), `1` error.

| Command | Gate | Paid? | Arguments | Effect / output |
|---|---|---|---|---|
| `build` | none | no | `[--only public\|constructed]` | Builds public + constructed content from `eval/contracts/raw/` downloads, the 7 drafting templates and the playbook library. Writes `eval/exam/content/*.json` (committed) and documents under `eval/contracts/` (ignored). Prints per-set counts, past-25k counts, plants by severity, controls, scan twins, missing datasets (reported, never substituted). Deterministic. |
| `status` | none | no | — | Prints content fingerprint, datasets present/missing, owner files (n/14, kinds), OCR cache (n/needed), frozen texts, owner keys (confirmed / corrected / unmarked / out of date), routing (requests, picks, snapshot fresh/out of date), runs from audit (id, kind, mode, delivery, outcome, cost, failed-call rate, flags), runs **waiting on a batch** (resume/abandon hint), current baseline. |
| `owner-import` | owner | no | `--from DIR --owner EMAIL` | Opens **only** the 14 allowlisted names under `DIR` (never lists `DIR`), copies them to `eval/private/owner/files/`, records sha256 / pages / kind (`text`, `scan`, `stamp_only_scan`), freezes the text of every file the app reads without OCR. Audit `eval.owner_files_imported`. Used as `docker compose run --rm -v "$HOME/Downloads:/owner_downloads:ro" backend python -m eval.exam owner-import --from /owner_downloads --owner EMAIL`. |
| `ocr` | owner | **yes (OCR)** | `--owner EMAIL` | One-time OCR (provider already in use, Reducto) of every exam file that needs it and is not cached: the 11 owner scans (stamp-only scans forced) + any constructed scan the app path would OCR. Estimate (files, pages, credits, USD when `REDUCTO_USD_PER_CREDIT` is set — else a "price not set — set REDUCTO_USD_PER_CREDIT in eval/private/exam.env" warning and the ceiling is asked **in credits**) → go-ahead + ceiling → OCR → cache `eval/private/ocr/<sha256>.json` → freeze owner scan texts. Audit `eval.spend_approved\|refused`, one `eval.real_contract_sent` per owner file, `eval.run_finished`. |
| `prefill` | owner | **yes (AI, sync)** | `--owner EMAIL` | Refuses unless all 14 texts are frozen and `PREFILL_MODEL != settings.claude_model`. Estimate → go-ahead + ceiling → one `complete_json` call per owner file with **claude-sonnet-5-5** (never batched: real contracts), quote check, writes `eval/private/sheets/facts-<sheet_id>.xlsx`. Audit `eval.spend_approved\|refused`, `eval.real_contract_sent` ×14, `eval.run_finished`. |
| `draft-routing` | owner | **yes (AI, sync)** | `--owner EMAIL` | Snapshots today's enabled workflows from the live DB, drafts ~50 free-text requests with **claude-sonnet-5-5** (≥1 per workflow), writes `eval/private/routing/requests.json` + `eval/private/sheets/routing-<sheet_id>.xlsx`. Same audit pattern (no `real_contract_sent`: no client text). |
| `sheet` | owner | no | `facts [--reconfirm] \| routing --owner EMAIL` | Regenerates a sheet without paying: `facts --reconfirm` = only out-of-date owner keys, suggestion = previously confirmed value, quote re-verified against the new frozen text; `routing` = new workflow snapshot, previous pick as suggestion. |
| `load-sheet` | owner | no | `PATH --owner EMAIL` | `PATH` must be under `eval/private/sheets/`. Validates the whole sheet (round-trip + marks); on any problem prints every problem and loads **nothing**; else writes keys. Audit `eval.labels_loaded`. |
| `run` | owner | **yes (AI)** | `--mode sample\|full [--delivery batch\|sync] [--change NAME --target review\|extraction\|qa\|routing\|portfolio] --owner EMAIL` · or `--resume RUN_ID --owner EMAIL` · or `--abandon RUN_ID --owner EMAIL` | The exam run (see runner). `--delivery` defaults to `batch` (FR-41: public + constructed units batched, owner-contract units synchronous); `sync` sends everything normally. Estimate → go-ahead + ceiling → rebuild exam DB → phases → `eval/private/runs/<run_id>/results.json` + `detail.md`, summary printed. `--resume` continues a run whose process stopped (polls its pending batch, replays, carries on) under the ceiling already approved; `--abandon` cancels a pending batch and finishes the run `incomplete`. Audit `eval.spend_approved\|refused`, `eval.run_resumed`, `eval.run_finished`. `--change`/`--target` must be given together and are recorded in `eval.spend_approved` before any paid call. |
| `baseline` | owner | no | `set NAME RUN_A RUN_B [--replace] --owner EMAIL` | Validates the pair (FR-27 incl. the 1% rule and same delivery), writes `eval/exam/reports/baseline-NAME.md` + `.json` (numbers only). Refuses if a current baseline exists and `--replace` is absent. Audit `eval.baseline_designated`. |
| `compare` | none | no | `RUN_A RUN_B [--baseline NAME]` | Validates the change pair, refuses different exam content and a different delivery mode, prints and writes `eval/exam/reports/compare-<change>-<YYYYMMDD>.md` + `.json` with the per-measure verdict. Read-only on the live DB. |

Go-ahead dialogue (all paid commands, inside the same invocation, after owner
re-authentication): the estimate is printed — calls per call type split **batch / sync**,
expected USD at the price actually paid (batch ×0.5) and at list price, the **worst case**
(every answer at its `max_tokens`; the minimum ceiling that guarantees a complete run), OCR
files/pages/credits/USD on separate lines, model under test, delivery mode, and, for batch
delivery, "Batch results usually arrive within an hour and can take up to 24 hours per
phase (two phases)". Then `Type the first 8 characters of run <run_id> to approve, or press
Enter to refuse:` and, on a match, `Spending ceiling for this run in USD:` (credits for OCR
without a price; a number > 0). Anything else = refusal. There is **no** `--yes`/`--approve`
flag and no stored approval, so a go-ahead cannot outlive the run it was typed for
(`--resume` continues the same run under its recorded ceiling; it never starts a new one).

#### Python interfaces between exam modules (frozen; parallel agents code against these)

```python
# eval/exam/content.py  — paths, allowlist, models, loaders, fingerprints (stdlib + pydantic only)
EXAM_DIR: Path            # backend/eval/exam
CONTENT_DIR: Path         # EXAM_DIR / "content"           (committed)
REPORTS_DIR: Path         # EXAM_DIR / "reports"           (committed, numbers only)
DOCS_DIR: Path            # backend/eval/contracts         (ignored; generated + raw downloads)
RAW_DIR: Path             # DOCS_DIR / "raw"
PRIVATE_DIR: Path         # backend/eval/private           (ignored)
WORK_DIR: Path            # backend/eval/.work             (ignored; exam storage root)
CUTOFF = 25_000
PREFILL_MODEL = "claude-sonnet-5-5"
ROUTING_DRAFT_MODEL = "claude-sonnet-5-5"
OWNER_FILES: tuple[str, ...] = (   # FR-15 — exactly these, in this order -> owner-01..owner-14
    "Employment Agreement.pdf", "Franklin Madison - MSA - 1-23-19.pdf", "INFRAHEDGE-MSA.pdf",
    "IT Services Agreement - ABC_signed.pdf", "MSA - Hearst.pdf", "MSA.Pdf", "MSA_1.Pdf",
    "MSA_2020_6_000676.Pdf", "MSA_2021_11_000942.Pdf", "MSA_AAA PHARMACEUTICALS.pdf",
    "MSA_Signed.pdf", "Mutual NDA — Northwind Robotics Inc..docx", "NDA Form.pdf",
    "Patent_License_Agreement 1.pdf")
class OwnerFileRefused(Exception): ...
def owner_file_path(src_dir: Path, name: str) -> Path   # raises OwnerFileRefused for any name not in
    # OWNER_FILES BEFORE touching the filesystem; tries the NFC then the NFD spelling of the SAME name;
    # never lists src_dir.

class SourceCategory(StrEnum): PUBLIC = "public"; CONSTRUCTED = "constructed"; OWNER = "owner"
class Task(StrEnum): REVIEW = "review"; EXTRACTION = "extraction"; QA = "qa"; PORTFOLIO = "portfolio"; ROUTING = "routing"
Severity = Literal["low", "medium", "high", "critical"]
FieldName = Literal["parties", "effective_date", "expiration_date", "end_date_or_term",
                    "governing_law", "value", "renewal_term", "notice_period", "liability_cap",
                    "termination_for_convenience"]
# notice_period (D-14) = the notice needed to end the contract or to stop it renewing — one field.

class DatasetFile(BaseModel): path: str; sha256: str | None          # path relative to RAW_DIR
class Dataset(BaseModel):
    key: Literal["cuad", "contractnli", "legalbench_rag", "commonpaper"]
    name: str; version: str; licence: str; url: str
    files: list[DatasetFile]; available: bool                         # False => set reported missing

class ExamDocument(BaseModel):
    doc_id: str                       # cuad-001 | cnli-01 | cp-msa-01 | cp-dpa-01 | ae-<type>-01 |
                                      # ae-long-01 | scan-<doc_id> | pf-01 | owner-01 | owner-01-asread
    category: SourceCategory
    dataset: str | None               # Dataset.key for public documents
    file: str                         # relative to DOCS_DIR, or to PRIVATE_DIR/"owner/files" for owner
    mime_type: str                    # text/plain | application/pdf | ...wordprocessingml.document
    file_sha256: str
    title: str                        # what an upload of this file would be titled (file stem)
    uploader: Literal["runner", "portfolio"] = "runner"
    text_mode: Literal["app", "forced_ocr"] = "app"     # forced_ocr only for stamp-only owner main docs
    twin_of: str | None = None                          # scan twin -> text original
    as_read_today_of: str | None = None                 # owner-NN-asread -> owner-NN
    ai_steps: list[Literal["metadata", "clauses", "brain"]]   # embeddings always run (free)

class PlantedChange(BaseModel):
    rule: str                         # PlaybookRule.clause_type the change breaks
    status: Literal["deviation", "missing"]
    original_text: str | None         # text before the change
    changed_text: str | None          # text after the change (None for deletions)
    anchor_text: str                  # >= 60 chars immediately before the change point (locates deletions)
    char_start: int                   # offset of the change point in the generated text
    severity: Severity                # copied from the platform rule at build time, never by an AI
    severity_source: str              # "<library playbook name> / <clause_type>"

class ReviewRuleKey(BaseModel):
    rule: str
    status: Literal["compliant", "deviation", "missing"]
    evidence_quote: str | None = None
    severity: Severity | None = None  # set only for constructed plants
    planted: PlantedChange | None = None

class ReviewItem(BaseModel):
    item_id: str                      # rv-<doc_id>
    doc_id: str; category: SourceCategory; source: str   # dataset name+version, or "constructed"
    playbook: str                     # exam playbook key (nda_checklist|cuad_checklist|cp_msa|cp_dpa)
                                      # or library playbook name ("Master Services Agreement Playbook", …)
    control: bool = False             # unchanged control document
    rules: list[ReviewRuleKey]

class FieldKey(BaseModel):
    field: FieldName
    kind: Literal["parties", "date", "date_or_term", "jurisdiction", "amount", "passage"]
    value: str | list[str] | None     # None = "not stated in the document"; parties = list
    quote: str | None                 # exact supporting text (None when not stated)

class ExtractionItem(BaseModel):
    item_id: str                      # ex-<doc_id>
    doc_id: str; category: SourceCategory; source: str
    fields: list[FieldKey]

class QAItem(BaseModel):
    item_id: str                      # qa-001
    doc_id: str; category: SourceCategory; source: str   # "LegalBench-RAG <version> / cuad"
    question: str; answer: str; passages: list[str]       # exact answer passages
    # build asserts (doc_id, question) unique, so no two items send an identical request

class PortfolioQuestion(BaseModel):
    item_id: str                      # pq-01
    question: str; fact: str          # fact key used to compute the list (documentation)
    expected_doc_ids: list[str]       # exact correct list (full portfolio)

class RoutingRequest(BaseModel):     # private: eval/private/routing/requests.json
    item_id: str                      # rt-01
    channel: Literal["email", "chat", "notice"]
    subject: str; body: str
    drafted_for_workflow_id: str; drafting_model: str     # inputs only, never the key

class OwnerFactKey(BaseModel):       # private: eval/private/keys/owner_facts.json
    doc_id: str; field: FieldName
    status: Literal["confirmed", "corrected", "unmarked"]
    value: str | list[str] | None     # final value (None = not stated); ignored when unmarked
    quote: str | None; char_start: int | None; char_end: int | None
    suggested_value: str | list[str] | None; suggested_quote: str | None
    suggested_by: str                 # model id (PREFILL_MODEL) or "previous key"
    file_sha256: str; frozen_text_sha256: str; sheet_sha256: str; loaded_at: datetime

class RoutingKey(BaseModel):         # private: eval/private/keys/routing.json
    item_id: str
    status: Literal["confirmed", "corrected", "unmarked"]
    workflow_id: str | None           # None = "no workflow — a person should route it"
    workflow_fingerprint: str; sheet_sha256: str; loaded_at: datetime

class ExamContent(BaseModel):
    datasets: list[Dataset]; documents: list[ExamDocument]
    review: list[ReviewItem]; extraction: list[ExtractionItem]; qa: list[QAItem]
    portfolio: list[PortfolioQuestion]; routing: list[RoutingRequest]
    owner_keys: list[OwnerFactKey]; routing_keys: list[RoutingKey]
    playbooks: dict[str, dict]        # exam playbook key -> playbook JSON
    sample: dict[str, list[str]]      # fixed sample subset: Task value -> item ids, + "portfolio_docs"
    workflow_snapshot: dict | None
def load_content() -> ExamContent                 # committed content + private files when present
def content_fingerprint(content: ExamContent) -> str   # sha256 over canonical JSON of: committed
    # content files, every document's file_sha256, owner frozen_text_sha256s, owner + routing
    # keys, routing requests, workflow snapshot fingerprint, sample subset
def app_code_sha256() -> str                      # sha256 over sorted (path, bytes) of backend/app/**/*.py

# eval/exam/env.py  — imports only stdlib, pydantic-settings, eval.exam.content and
# app.core.config at module level (eval/exam/__init__.py is empty)
EVAL_DB_NAME = "legal_clm_eval"
class ExamSettings(BaseSettings):                 # read ONLY by the exam process
    model_config = SettingsConfigDict(env_file=PRIVATE_DIR / "exam.env", env_prefix="EVAL_",
                                      extra="ignore")
    claude_api_key: str | None = None             # EVAL_CLAUDE_API_KEY — dedicated, never the real key
    owner_email: str | None = None                # EVAL_OWNER_EMAIL — the one exam owner
    reducto_usd_per_credit: float | None = Field(None, validation_alias="REDUCTO_USD_PER_CREDIT")
class ExamRefused(Exception): ...                 # -> exit code 2, message printed
@dataclass(frozen=True)
class LiveDB: url: str; sessions: sessionmaker; org_id: str
@dataclass(frozen=True)
class Owner: user_id: str; email: str
def isolate(*, paid: bool, eval_db_name: str = EVAL_DB_NAME) -> LiveDB
def authenticate_owner(live: LiveDB, email: str, *, command: str,
                       getpass: Callable[[str], str]) -> Owner        # audits refusals
def audit(live: LiveDB, *, action: str, actor: Owner | None, resource_type: str,
          resource_id: str | None, after: dict | None = None, metadata: dict | None = None) -> None
@contextmanager
def exam_lock(live: LiveDB) -> Iterator[None]     # pg_try_advisory_lock on a dedicated live connection

# eval/exam/runner.py (shared by owner.py / routing.py for every paid command)
@dataclass
class Estimate:
    delivery: Literal["batch", "sync"]
    calls: int; batch_calls: int; sync_calls: int
    usd: float                        # expected, at the price actually paid (batch x0.5)
    list_usd: float                   # expected, every call at list price
    worst_case_usd: float             # every answer at max_tokens; min ceiling for a complete run
    by_call_type: dict[str, dict]     # {"<prompt_key>": {"calls": int, "batch": int, "usd": float}}
    ocr_files: int = 0; ocr_pages: int = 0; ocr_credits: int = 0
    ocr_usd: float | None = None      # None when REDUCTO_USD_PER_CREDIT is not set
class CeilingReached(Exception): ...
class Deferred(BaseException):        # capture mode: the request is recorded for the batch and the
    custom_id: str                    # app operation is aborted (BaseException ON PURPOSE, decision 22)
class BatchCallFailed(Exception): ... # replay: errored / expired / canceled / prompt changed since capture
def custom_id(payload: dict) -> str   # sha256 hex (64 chars) of json.dumps(payload, sort_keys=True,
                                      # separators=(",", ":"), ensure_ascii=False) — the Batches custom_id
def worst_cost(payload: dict, *, batched: bool) -> float   # input = chars/3 tokens, output = max_tokens
class Meter:                          # installed as app.integrations.claude.exam_meter
    def __init__(self, ceiling_usd: float, *, spent_usd: float = 0.0): ...
    mode: Literal["live", "capture", "replay"]
    captured: dict[str, dict]         # custom_id -> payload            (capture mode)
    results: dict[str, dict]          # custom_id -> batch result dict  (replay mode)
    batch_id_of: dict[str, str]       # custom_id -> batch id           (replay mode)
    spent_usd: float; reserved_usd: float; stopped: bool
    def before(self, payload: dict) -> tuple[dict, str] | None
        # live: if spent+reserved+worst_cost(payload) > ceiling -> stopped=True, raise CeilingReached;
        #       else reserve and return None (the real request is made)
        # capture: captured[custom_id]=payload; raise Deferred(custom_id)
        # replay: result "succeeded" -> return (result["message"], f"{batch_id}:{custom_id}");
        #         anything else, or custom_id unknown -> raise BatchCallFailed
    def after(self, payload: dict, response: dict | None) -> None
        # live only: release the reservation; add token_cost(usage, model) of a real response
    def admit(self, payloads: list[dict]) -> list[dict]     # batch admission in planned order, by worst case
    def settle_batch(self, admitted: list[dict], results: dict[str, dict]) -> None   # actual x0.5
@dataclass
class PaidRun:
    run_id: str; kind: Literal["exam", "ocr", "prefill", "draft_routing"]
    owner: Owner; live: LiveDB; estimate: Estimate
    ceiling_usd: float | None; ceiling_credits: int | None   # credits only for OCR without a price
    meter: Meter | None
def approve_paid_run(*, kind: str, live: LiveDB, owner: Owner, estimate: Estimate,
                     ask: Callable[[str], str], extra: dict | None = None) -> PaidRun | None
    # prints estimate, asks, writes eval.spend_approved / eval.spend_refused; None = refused
def finish_paid_run(run: PaidRun, *, outcome: str, reason: str | None, actual: dict,
                    results_sha256: str | None, summary: dict | None) -> None   # eval.run_finished
async def execute_exam_run(*, live: LiveDB, owner: Owner, mode: Literal["sample", "full"],
                           delivery: Literal["batch", "sync"], change: str | None,
                           target: str | None, ask: Callable[[str], str],
                           eval_db_name: str = EVAL_DB_NAME) -> dict   # returns results.json dict
async def resume_exam_run(*, live: LiveDB, owner: Owner, run_id: str) -> dict
async def abandon_exam_run(*, live: LiveDB, owner: Owner, run_id: str) -> None
def mirror_values(row: AICallLog, *, live_org_id: str, owner_id: str) -> dict   # pure: the live copy
def stamp_and_mirror(*, run_id: str, exam_sessions: sessionmaker, live: LiveDB,
                     owner: Owner) -> int                                   # rows copied
def reconcile_crashed_runs(live: LiveDB, *, exam_sessions: sessionmaker | None) -> list[str]
    # skips runs whose state.json shows a submitted, not yet replayed batch (they are waiting);
    # exam_sessions None = the exam DB is gone; cost then comes from already-mirrored rows

# eval/exam/owner.py
class FrozenOCR:                      # injected as ContractFilesService(reducto=...)
    provider = "reducto"
    def __init__(self, *, fill: Callable[..., Awaitable[OCRResult]] | None = None,
                 on_send: Callable[[str, OCRResult], None] | None = None): ...
    async def extract_text(self, *, filename: str, mime_type: str, content: bytes) -> OCRResult
    # cached sha256 -> cached result (free); uncached and fill is None -> raises OCRNotFrozen
def verify_quote(quote: str, text: str) -> tuple[int, int] | None   # word-for-word, whitespace-insensitive
async def read_as_app(content: bytes, *, mime_type: str, filename: str, ocr: FrozenOCR) -> _ExtractedText

# eval/exam/sheets.py
class SheetRejected(Exception): problems: list[str]
def write_facts_sheet(rows: list[dict], *, path: Path) -> str            # returns sheet_id
def write_routing_sheet(rows: list[dict], *, snapshot: dict, path: Path) -> str
def load_sheet(path: Path) -> tuple[str, list[OwnerFactKey] | list[RoutingKey]]   # (kind, keys) or SheetRejected

# eval/exam/evaldb.py
@dataclass(frozen=True)
class ExamActors: org_id: str; runner: User; portfolio: User
@dataclass(frozen=True)
class IngestedDoc: doc_id: str; contract_id: str; version_id: str; snapshot_id: str
                   text_len: int; method: str; elements: int
def reset(live: LiveDB, eval_db_name: str) -> None        # refuses unless name startswith "legal_clm_eval"
def migrate() -> None                                     # alembic upgrade head on the exam DB
def bootstrap(db: Session) -> ExamActors
def load_playbooks(db: Session, content: ExamContent, actors: ExamActors) -> dict[str, str]  # key -> playbook_id
def load_workflows(db: Session, snapshot: dict, actors: ExamActors) -> None
async def ingest_document(db: Session, doc: ExamDocument, *, actors: ExamActors, ocr: FrozenOCR) -> IngestedDoc

# eval/exam/score.py   (pure; no DB, no network)
def score_run(results: dict, content: ExamContent) -> dict   # -> "scores" object (schema below)
def score_pair(a: dict, b: dict, content: ExamContent) -> dict
def verdict(baseline_pair: dict, change_pair: dict, *, target: str | None) -> dict

# eval/exam/report.py
def write_detail(results: dict, scores: dict, path: Path) -> None          # private, may quote text
def write_public_report(obj: dict, *, stem: str) -> Path                   # REPORTS_DIR, numbers only
def check_pair(a_finished: dict, b_finished: dict) -> list[str]            # FR-27 problems ([] = ok)
```

#### `claude.py` hook and batch methods (frozen)

```python
# Set only by the AI exam's own process (backend/eval/exam, specs/005-ai-eval-baseline);
# always None in the app. Duck-typed: .before(payload) and .after(payload, response).
exam_meter = None

# ClaudeClient._post_messages, before reserve_tokens and outside the retry wrapper:
if exam_meter is not None:
    replayed = exam_meter.before(json_payload)        # may raise (ceiling / Deferred / BatchCallFailed)
    if replayed is not None:
        return replayed                               # (response_json, "<batch_id>:<custom_id>")
# … the existing request …; in its finally, after settle_tokens:
if exam_meter is not None:
    exam_meter.after(json_payload, response_json)     # None when the request failed
# stream_with_tools: same before/after; a non-None return from before() raises RuntimeError
# (the exam never streams; a replay must never be silently ignored).

# Message Batches — used only by the exam; exam key via the usual _anthropic_client();
# retrieve/results/cancel wrapped in resilient_call("claude"), create is NOT retried (a lost
# reply could otherwise create and bill a second batch); all four raise in mock mode.
async def create_batch(self, requests: list[dict]) -> str          # [{"custom_id", "params"}] -> batch id
async def batch_status(self, batch_id: str) -> dict                # MessageBatch.to_dict()
async def batch_results(self, batch_id: str) -> dict[str, dict]    # custom_id -> result.to_dict()
async def cancel_batch(self, batch_id: str) -> None
```

`params` of each batch request is the exact `json_payload` the app built (model,
`max_tokens`, `temperature`, `system` with its cache markers, `messages`, `tools`,
`tool_choice`) — "same model and same request settings" (FR-41). `_post_messages` payloads
never carry `stream`, so they are valid non-streaming batch params as they are.

#### Item ↔ AI-call linkage (frozen; how the runner and scorer agree which calls belong to which item)

The app builds its own `AICallContext`; the runner steers it through existing parameters only:

| Step (unit) | How it is called | Ledger field that identifies it |
|---|---|---|
| clause labelling `cl-<doc_id>` / metadata `md-<doc_id>` (phase 1) | `ai_controller.run_job_skill(db, job=JobRun(...), …)` with a `create_job` row (never dispatched) | `a_i_call_log.job_id` → `(doc_id, step)` |
| routing `rt-NN` (phase 1) | `triage(db, IntakeRequest(id=uuid5(EXAM_NS, item_id), …))` (never added to the session) | `resource_id == uuid5(...)` |
| review `rv-<doc_id>` (phase 2) | `ai_controller.run_structured_skill(…, request_id=item_id)` | `request_id == item_id` |
| Q&A `qa-NNN`, portfolio `pq-NN` (phase 2) | `ask_contract_brain(…, request=<state.request_id=item_id>)` | `request_id == item_id` |
| pre-fill / drafting (direct) | `claude_client.complete_json` + `record_call(ctx=AICallContext(…, eval_run_id=run_id))` | `eval_run_id` set at write |

Batch requests are keyed by `custom_id(payload)`; the replayed ledger row carries
`provider_request_id = "<batch_id>:<custom_id>"`, which is how `stamp_and_mirror` sets
`batched = true`. Every unit makes exactly one AI call in today's code (or none, e.g.
`aggregate_answer`, a "Used for" triage); a unit that asks for a second call during replay
finds no stored result and fails "unexpected extra AI call" (`ponytail:` comment — add a
capture loop when a batched path ever makes two calls).

#### File formats

**`eval/private/runs/<run_id>/results.json`** (private; producer = runner, consumers = score/report;
written once, then `chmod 0444`, sha256 recorded in `eval.run_finished`):

```json
{
  "schema": "aegis-exam-results/1",
  "run_id": "uuid", "kind": "exam", "mode": "full",
  "delivery": "batch",                         // batch | sync  (FR-41)
  "change": null, "target_area": null,
  "started_at": "ISO-8601", "finished_at": "ISO-8601",
  "outcome": "complete",                       // complete | incomplete | stopped
  "incomplete_reason": null,                   // ceiling_reached | crashed | interrupted | abandoned
  "contains_errors": false,
  "failed_call_rate": 0.0,                     // failed AI calls / AI calls (D-13)
  "versions": {
    "app_code_sha256": "hex", "git_sha": null,  // git_sha from EXAM_GIT_SHA env if given
    "model_under_test": "claude-sonnet-4-6",
    "models": ["claude-sonnet-4-6"],           // distinct a_i_call_log.model of the run
    "prompts": [{"prompt_key": "playbook_review", "version": "x", "hash": "hex"}],
    "content_fingerprint": "hex"
  },
  "estimate": {"delivery": "batch", "calls": 0, "batch_calls": 0, "sync_calls": 0, "usd": 0.0,
               "list_usd": 0.0, "worst_case_usd": 0.0, "by_call_type": {},
               "ocr_files": 0, "ocr_pages": 0, "ocr_credits": 0, "ocr_usd": 0.0},
  "ceiling_usd": 25.0,
  "approved_by": {"user_id": "uuid", "email": "owner@…"},
  "excluded": {"owner_keys_out_of_date": ["owner-03:governing_law"], "owner_rows_unmarked": 0,
               "owner_docs_without_keys": [], "routing_unpicked": 0, "routing_out_of_date": false},
  "batches": [{"phase": 1, "batch_id": "msgbatch_…", "retry_of": null, "requests": 0,
               "admitted": 0, "succeeded": 0, "errored": 0, "expired": 0, "canceled": 0,
               "submitted_at": "ISO-8601", "ended_at": "ISO-8601", "usd": 0.0}],
  "documents": [{"doc_id": "cuad-001", "contract_id": "uuid", "text_len": 61234,
                 "method": "native_text", "elements": 212, "status": "ok", "failure": null}],
  "items": [{
    "item_id": "rv-cuad-001", "task": "review", "doc_id": "cuad-001",
    "status": "scored",                        // scored | failed | not_attempted
    "failure": null,                           // {"call_id": "...", "reason": "..."} | {"reason": "upstream: …"}
    "call_ids": ["uuid"],
    "prediction": {}                           // task-specific, below
  }],
  "calls": [{
    "call_id": "uuid", "item_ids": ["rv-cuad-001"], "doc_id": "cuad-001",
    "call_type": "playbook_review",            // = prompt_key
    "batched": true,
    "model": "claude-sonnet-4-6", "status": "succeeded", "stop_reason": "tool_use",
    "prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
    "latency_ms": null,                        // null for batched calls (see phase timings)
    "cost_usd": 0.0,
    "chars_seen": 25000, "chars_total": 61234,  // review/extraction calls only, else null
    "quotes": [{"text": "…", "word_for_word": true, "align_score": 100.0}]
  }],
  "phase_wall_time_s": {"ingest": 0.0, "phase_1": 0.0, "phase_2": 0.0},
  "wall_time_s": 0.0
}
```

`prediction` by task:
- review: `{"flags": [{"rule": "limitation_of_liability", "status": "deviation", "severity": "high", "quote": "…"|null, "attributed_by": "rule_index|clause_type|text|none"}]}`
- extraction: `{"fields": {"parties": ["…"], "effective_date": "YYYY-MM-DD"|null, "expiration_date": …, "end_date_or_term": "YYYY-MM-DD"|null, "governing_law": "…"|null, "value": {"amount": "2500000.00", "currency": "INR"}|null, "renewal_term": "<clause text>"|null, "notice_period": …, "liability_cap": …, "termination_for_convenience": …}}`
- qa: `{"answer": "…", "answer_mode": "rag|database_count", "citations": ["quote", …], "sources": [{"bucket": "semantic|clauses|text|graph", "text": "…", "contract_id": "uuid"|null}], "context_chars": 0}`
- portfolio: `{"answer": "…", "named_doc_ids": ["pf-07"], "citations": ["…"], "context_chars": 0}`
- routing: `{"workflow_id": "uuid"|null, "needs_human": false, "source": "llm|used_for|deterministic", "degraded": false}`

**`eval/private/runs/<run_id>/state.json`** (private; the resumability record; rewritten
atomically — write temp file, rename — at every transition):

```json
{
  "run_id": "uuid", "mode": "full", "delivery": "batch", "ceiling_usd": 25.0,
  "phase": 1,                                  // 1 | 2 | 3 (= done)
  "step": "captured",                          // ingested | captured | submitting | submitted |
                                               // retry_submitted | replayed | submit_failed
  "units": {"cl-cuad-001": "captured", "md-owner-01": "done", "rv-cuad-001": "pending"},
                                               // pending | captured | done | failed | not_attempted
  "batches": [{"phase": 1, "batch_id": "msgbatch_…", "retry_of": null, "submitted_at": "ISO-8601",
               "requests": {"<custom_id>": {"unit_id": "cl-cuad-001", "worst_usd": 0.0}}}]
}
```

**Committed report JSON** (`eval/exam/reports/*.json`, numbers + ids + rule/field/workflow
names only — never quotes, never owner file names):

```json
{
  "schema": "aegis-exam-report/1",
  "kind": "baseline",                          // baseline | comparison
  "label": "baseline-2026-10", "generated_at": "ISO-8601",
  "content_fingerprint": "hex", "scorer_sha256": "hex", "delivery": "batch",
  "runs": [{"run_id": "uuid", "mode": "full", "delivery": "batch", "outcome": "complete",
            "contains_errors": false, "failed_call_rate": 0.0,
            "versions": {"app_code_sha256": "hex", "models": [], "prompts_sha256": "hex"},
            "cost_usd": 0.0, "estimate_usd": 0.0, "wall_time_s": 0.0}],
  "measures": [{
    "key": "review_f1", "label": "Review F1", "explanation": "one plain-language line",
    "direction": "higher",                     // higher | lower
    "role": "headline_quality",                // headline_quality | report_only | cost_time
    "n": 260,
    "values": {"run_a": 0.81, "run_b": 0.83, "average": 0.82, "noise_band": 0.02},
    "splits": {"public": {"n": 0, "average": 0.0, "noise_band": 0.0}, "constructed": {}, "owner": {},
               "before_25k": {}, "past_25k": {}}
  }],
  "missed_high_critical": [{"item_id": "rv-ae-msa-02", "rule": "limitation_of_liability", "severity": "high", "runs_missed": ["a", "b"]}],
  "counts": {"review_docs": {}, "extraction_items": {}, "qa_questions": {}, "past_25k": {},
             "routing_workflows_covered": 0, "routing_requests_per_workflow": {}, "failed_calls": 0,
             "owner_prefill_changed_rate": 0.0, "owner_rows_unmarked": 0},
  "history": [{"file": "superseded/baseline-sample-2026-09-28.json", "label": "earlier, superseded"}],
  "comparison": null                           // comparison reports: see verdict below
}
```

Verdict object (`"comparison"` in comparison reports):
`{"baseline": "NAME", "change": "NAME", "delivery": "batch", "target_area": "review",
"main_measure": "review_f1", "verdict": "pass|fail|no_verdict",
"no_verdict_reason": null|"target not recorded before runs"|"main measure not measured",
"per_measure": [{"key": "…", "baseline_average": 0.0, "change_average": 0.0, "delta": 0.0,
"noise_band": 0.0, "result": "improved|worsened|within_band|not_measured", "decides": true}],
"failed_measures": ["quote_validity"], "report_only": [{"key": "coverage_avg", …}],
"cost_time": [{"key": "cost_usd", "baseline": 0.0, "change": 0.0, "difference": 0.0}]}`

**Spreadsheets** (XLSX via `openpyxl`, already a dependency; written to and loaded only from
`eval/private/sheets/`):

Facts sheet, tab `Facts` (one row per owner document × field = 14 × 6 = 84 rows):

| Col | Header | Content | Owner edits? |
|---|---|---|---|
| A | `row_key` | `sha256(sheet_id + doc_id + field)[:16]` (hidden) | no (locked) |
| B | `contract` | owner file name | no |
| C | `question` | plain question, e.g. "Which law governs this contract?"; notice period = "How much notice is needed to end this contract or stop it renewing?" (D-14) | no |
| D | `suggested_answer` | pre-filled value (parties joined with ` \| `), or `not stated in the document` | no |
| E | `supporting_sentence` | exact quote found in the frozen text, or `no supporting sentence found` | no |
| F | `where` | `page N, characters a–b` (page from the snapshot `page_map`; `characters a–b` alone if none) | no |
| G | `your_mark` | data-validation list `correct, wrong` (only `wrong` for unsupported rows); blank = can't tell | **yes** |
| H | `correct_value_if_wrong` | free text; parties separated by ` \| `; `not stated in the document` allowed | **yes** |
| I | `notes` | ignored by the loader | yes |

Tab `README` = instructions; hidden tab `meta` = `sheet_id`, kind, generated_at, row count,
`frozen_manifest_sha256`. The expected rows are also saved privately as
`eval/private/sheets/<sheet_id>.expected.json`.

Routing sheet, tab `Routing` (one row per drafted request): `row_key`, `channel`,
`request` (subject + body), `suggested_workflow` (the workflow the request was drafted for,
shown as a suggestion — same style as FR-18), `your_mark` (`correct, wrong`, blank),
`correct_workflow_if_wrong` (data-validation list of the snapshot's workflow names +
`(no workflow — a person should route it)`), `notes`. `meta` adds `workflow_fingerprint`.

Load rules (`load_sheet`), **all-or-nothing**: reject the whole file (load nothing, list
every problem) when — `meta.sheet_id` unknown; any expected `row_key` missing, any extra or
duplicate `row_key`; any locked cell (B–F) differs from the expected row; `your_mark` not in
{`correct`, `wrong`, blank}; `correct` on a `no supporting sentence found` row (FR-17);
`wrong` with an empty correction; a routing correction that is not a snapshot workflow name.
Accepted rows: `correct` → key = suggested value + quote (status `confirmed`); `wrong` →
key = owner's value (status `corrected`; quote = `verify_quote` of the owner value if it
appears, else null); blank → status `unmarked`, excluded from scoring and counted (FR-19).

**Frozen OCR cache** `eval/private/ocr/<file_sha256>.json`:
`{"file_sha256", "provider": "reducto", "created_at", "run_id", "text", "quality_score",
"elements": [...], "blocks": [...], "num_pages", "credits", "cost_usd": null|float}`.

**Owner manifest** `eval/private/owner/manifest.json`:
`[{"doc_id": "owner-01", "name": "<OWNER_FILES[0]>", "file_sha256", "mime_type", "pages",
"kind": "text|scan|stamp_only_scan", "frozen_text_sha256": null|"hex", "frozen_method",
"imported_at"}]`; frozen texts at `eval/private/owner/texts/<file_sha256>.txt`.

**Workflow snapshot** `eval/private/routing/workflows.json`:
`{"fingerprint": "hex", "taken_at", "workflows": [{"id", "name", "description", "enabled",
"is_builtin", "eval_order", "version", "criteria", "steps"}]}`; fingerprint = sha256 of the
sorted `(id, name, enabled, version, criteria, steps)` of enabled workflows.

**Private exam config** `eval/private/exam.env` (owner-created, never committed or baked):
`EVAL_CLAUDE_API_KEY=…`, `EVAL_OWNER_EMAIL=…`, `REDUCTO_USD_PER_CREDIT=…` (optional).

#### Audit trail (live DB, `write_audit_log`, `org_id` = live org, `actor_user_id` = owner)

Each written on its own short live session and committed immediately — never across an AI
or OCR call, never while another live session of the process is open (CLAUDE.md audit-lock
rule). `resource_type` / `resource_id` as listed.

| Action | resource_type / id | `after` / `metadata` (no passwords, no keys, ever) |
|---|---|---|
| `eval.access_refused` | `eval_run` / null | `{command, claimed_email, reason}` (actor = matched user id or null) |
| `eval.owner_files_imported` | `eval_owner_file` / null | `{files: [{doc_id, name, file_sha256, kind, pages}]}` |
| `eval.spend_approved` | `eval_run` / run_id | `{kind, mode, delivery, change, target_area, main_measure, estimate (incl. list_usd, worst_case_usd), ceiling_usd \| ceiling_credits, approver: {user_id, email}, versions_at_start: {app_code_sha256, model_under_test, content_fingerprint}}` |
| `eval.spend_refused` | `eval_run` / run_id | `{kind, mode, delivery, estimate, refused_by}` |
| `eval.run_resumed` | `eval_run` / run_id | `{resumed_by, phase, step}` |
| `eval.real_contract_sent` | `eval_owner_file` / null | `{run_id, doc_id, name, file_sha256, destination: "reducto" \| "anthropic:<model>", purpose: "ocr" \| "prefill", pages, credits, tokens, cost_usd \| null}` |
| `eval.labels_loaded` | `eval_labels` / sheet_id | `{kind: "facts" \| "routing", sheet_sha256, counts, rows: [{row_key, doc_id\|item_id, field, suggested_value, suggested_quote, mark, final_value}]}` |
| `eval.run_finished` | `eval_run` / run_id | **the immutable run record**: `{kind, mode, delivery, change, target_area, started_by, approved_by, started_at, finished_at, versions, estimate, ceiling_usd, outcome, incomplete_reason, contains_errors, failed_call_rate, actual: {usd, list_usd, by_call_type, ocr_usd, ocr_credits, calls, failed_calls}, batches: [{phase, batch_id, counts}], results_sha256, headline: {key: value}}` |
| `eval.baseline_designated` | `eval_baseline` / null | `{name, runs: [a, b], delivery, replaced: {name, runs} \| null, report: "baselines/…"}` |

### Database schema

No new table. Two columns on the existing ledger table, in both databases (the exam DB is
built by the same `alembic upgrade head`).

| Table | Column | Type | Constraints |
|---|---|---|---|
| `a_i_call_log` | `eval_run_id` | `varchar(36)` | nullable, no FK (the run record lives in `audit_log`), index `ix_a_i_call_log_eval_run_id`. NULL = real usage; set = exam traffic of that run. |
| `a_i_call_log` | `batched` | `boolean` | `NOT NULL`, `server_default false` (model: `default=False, server_default=sa.false()`). True only for exam rows answered through the Message Batches API; priced at 50%. |

Model (`app/core/models.py`, `AICallLog`): `eval_run_id = Column(String(36), index=True,
nullable=True)` and `batched = Column(Boolean, nullable=False, default=False,
server_default=false())`, with a comment naming FR-34 / FR-41. Org scoping: unchanged — the
columns live on an `OrgScopedMixin` table; mirrored exam rows carry the live org id.

Migration: revision ID `0071_ai_call_log_eval_run` (25 chars) on current single head
`0070_ai_call_log_cache_tokens` (verified with `alembic heads`; not yet written or applied,
so both columns go in this one revision).
`upgrade`: `op.add_column("a_i_call_log", sa.Column("eval_run_id", sa.String(36), nullable=True))`;
`op.create_index("ix_a_i_call_log_eval_run_id", "a_i_call_log", ["eval_run_id"])`;
`op.add_column("a_i_call_log", sa.Column("batched", sa.Boolean(), nullable=False,
server_default=sa.false()))` (constant default: metadata-only on Postgres 11+, existing rows
read false). `downgrade`: drop `batched`, drop the index, drop `eval_run_id`. No backfill, no
pgvector change.

Mirrored live row (written by `stamp_and_mirror`, `INSERT … ON CONFLICT (id) DO NOTHING`):
same `id`, `created_at`, `provider`, `model`, `model_config_hash`, `prompt_key`,
`prompt_version`, `prompt_hash`, `output_schema_name`, token columns (incl. cache tokens),
`latency_ms`, `status`, `validation_status`, `error_class`, `stop_reason`,
`provider_request_id`, `eval_run_id`, `batched`; `org_id` = live org;
`created_by_user_id` / `updated_by_user_id` = owner; **NULL** for `input_payload`,
`validated_output`, `raw_ai_output`, `validation_error`, `request_id`, `job_id`,
`skill_run_id`, `session_id`, `assistant_run_id`, `tool_call_id`, `resource_id` (they hold
exam-DB ids or contract text); `resource_type` kept. Stamping in the exam DB:
`UPDATE a_i_call_log SET eval_run_id = :run, batched = (provider_request_id LIKE 'msgbatch%')
WHERE eval_run_id IS NULL` (only this run writes the exam DB).

### Frontend TypeScript models (added to src/lib/types.ts)

```ts
// AI usage & cost — exam traffic split (specs/005-ai-eval-baseline/plan.md)
export type AiUsageTraffic = "real" | "exam";

export interface AiUsageSummary {
  currency: string;
  window_days: number;
  traffic: AiUsageTraffic;            // NEW — echoes the filter the figures were computed with
  totals: {
    calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost: number;
    cost_per_call: number;
  };
  by_operation: AiUsageOperation[];
  by_category: { category: string; calls: number; cost: number }[];
  daily: { date: ISODate; calls: number; cost: number }[];
  rates: { model_family: string; input_per_m: number; output_per_m: number }[];
}
```

(Only `AiUsageTraffic` and the `traffic` field are new; the rest is the existing interface,
unchanged.)

### Endpoint client additions (src/lib/endpoints.ts)

- `aiUsageApi.summary(days = 30, traffic: AiUsageTraffic = "real") → Promise<AiUsageSummary>` —
  maps to `GET /analytics/ai-usage${qs({ days, traffic })}`. Existing calls `summary(days)`
  keep working (default `real`).

## Component design

### Backend

**Models:** `app/core/models.py` — `AICallLog.eval_run_id`, `AICallLog.batched` (above). No
other model.

**Schemas:** none in `app/` (the analytics route returns a dict, as today). Exam schemas are
the pydantic models in `eval/exam/content.py` (frozen above).

**Platform changes (inert for the running app):**

- `app/integrations/claude.py` — the `exam_meter` hook and the four batch methods exactly as
  frozen above (mock mode returns before `_post_messages`, so tests and dev mocks never touch
  the hook), plus:
  - `async def complete_json(self, *, org_id: str | None, system_prompt: str, user_prompt: str,
    json_schema: dict, max_tokens: int, model: str) -> ClaudeProviderResponse` — payload
    `{"model", "max_tokens", "system": _cached_system(system_prompt), "messages": [user],
    "output_config": {"format": {"type": "json_schema", "schema": json_schema}}}`: **no
    `temperature`, no `tools`, no `tool_choice`** (claude-sonnet-5-5 returns 400 on forced
    tool use and non-default temperature). Goes through `_post_messages` (retries, cost
    guard, meter). Mock mode returns one text block `"{}"`, `stop_reason="end_turn"`. Callers
    parse the text block as JSON.
- `app/ai/gateway/ledger.py` — `AICallContext.eval_run_id: str | None = None`;
  `record_call` writes `eval_run_id=ctx.eval_run_id`. App code never sets it.
- `app/analytics/routes.py` — `traffic: Literal["real", "exam"] = Query("real")` on
  `ai_usage`, applied to both queries (`base` tuple), `"traffic"` echoed; `_RATES` becomes the
  model-aware table above (most specific prefix first); `_cost` → public
  `token_cost(..., batched=False)`; both aggregate queries also `group_by(AICallLog.batched)`
  and price each group with its flag, folding into the existing per-`(prompt_key, model)` and
  per-day entries. Module docstring updated (prices per model, batch half price, the exam
  reuses `token_cost`). Permission unchanged (`admin_panel:access`); org filter kept.
- `app/core/config.py` — **unchanged** (decision 20).
- `.gitignore` (root): restore the rule lost under the dangling comment at line 53
  (`backend/docstudio_out/`, its original target — the comment's `make phase1` no longer
  exists, so reword it to "Documents reader report output carries full contract text"), and
  add, with a comment naming specs/005 and FR-36:
  `backend/eval/private/`, `backend/eval/contracts/`, `backend/eval/.work/`,
  `backend/eval/**/*.xlsx`.
- `backend/.dockerignore`: add `eval/private/`, `eval/contracts/`, `eval/.work/` so a
  `make prod-build` on the owner's machine can never bake client contracts or the exam key
  into an image.

**Exam package `backend/eval/exam/` (new):**

- `env.py` — `isolate(paid, eval_db_name)`:
  1. Refuse unless `"app.core.database" not in sys.modules` (nothing has built the live
     engine yet) and `settings.environment` ∈ {local, development, dev, test}.
  2. Capture `live_url = settings.database_url`, `real_key = settings.claude_api_key`;
     load `ExamSettings()`.
  3. Paid commands: refuse if `ExamSettings.claude_api_key` is empty or equals `real_key`
     (FR-34 — no fallback, ever); refuse if `settings.mock_claude` or
     `settings.allow_mock_embeddings` is on; refuse if `settings.embedding_provider !=
     "local"` or `settings.rerank_provider not in {"local", "none"}` (FR-35: client text may
     go only to Anthropic and Reducto, so Voyage/Cohere are refused rather than silently
     swapped).
  4. Mutate the `settings` singleton in place: `database_url` → same server, database
     `eval_db_name`; `claude_api_key` → `ExamSettings.claude_api_key` (paid commands; `None`
     otherwise, so a free command cannot make a paid call by accident); `storage_root` →
     `WORK_DIR/"storage"`, `storage_backend` → `"local"`; `disable_rbac` → False (the dev
     container exports it true; with it on every user is an admin and portfolio scoping would
     see every exam contract); `claude_daily_token_cap_per_org` → 0 (the exam's ceiling is the
     control; the per-org cap would 429 mid-run); `sentry_dsn`, `voyage_api_key`,
     `cohere_api_key` → None; `mock_resend`, `mock_sendgrid`, `mock_docusign`, `mock_signa`,
     `mock_tmsearch`, `mock_serper` → True (FR-38: no outbound email/notification).
  5. Import `app.models`, `app.core.database`; assert `engine.url.database == eval_db_name`
     and `!= live database name`, else refuse. Assert `app.integrations.claude._anthropic_clients`
     is empty (no client was built with the real key).
  6. `celery_app.conf.update(broker_url="memory://", result_backend="cache+memory://")` —
     same backstop as `tests/conftest.py`; the exam never dispatches, this only guarantees
     a missed call can't reach the live worker.
  7. Build the live engine (`pool_size=2, max_overflow=0`, same idle-transaction timeout)
     and resolve `org_id` = the one `Organization` (single-tenant, as `register_user` does).

  `authenticate_owner` — refuses (and audits `eval.access_refused`) when
  `ExamSettings.owner_email` is unset, the claimed email differs from it
  (case-insensitive), the live `User` is missing / not `ACTIVE` / in another org, or
  `app.core.security.verify_password(getpass(...), user.hashed_password)` fails. Roles and
  permissions are deliberately not consulted: an admin who is not the owner is refused
  (FR-31). `exam_lock` — `pg_try_advisory_lock` (fixed key distinct from the audit-chain
  key) on a dedicated autocommit live connection held for the command; refuses "another exam
  command is running". `audit` — open, `write_audit_log`, commit, close.

- `content.py` — the frozen models/paths/allowlist/fingerprints above. Pure.

- `build.py` — port of the branch's `build_datasets.py` (same downloads, same seeded RNG
  discipline, deterministic), right-sized per D-11 (sizes and costs in the table below):
  - **Datasets registry** (`datasets.json`): CUAD v1 (The Atticus Project, CC BY 4.0),
    ContractNLI v1.0 (Koreeda & Manning, CC BY 4.0), LegalBench-RAG (ZeroEntropy, MIT;
    built on CC BY 4.0 sources; version = archive sha + date), Common Paper CSA / DPA /
    Mutual NDA (CC BY 4.0; version = the template version printed in the file). Raw file
    sha256 recorded. A missing download marks the dataset `available: false` and its set is
    omitted and reported as missing (spec edge case), never substituted.
  - **CUAD set (FR-4a): 40 contracts longer than 25,000 characters**, chosen
    deterministically by the most labelled passages starting past 25k, then title. Document
    text = the LegalBench-RAG corpus copy of the same CUAD contract (its Q&A spans index into
    it); CUAD labels are located in it (answer_start, else exact search); any label that
    cannot be located is dropped and counted. Two uses on the same documents: *review*
    against `cuad_checklist` (8 lawyer-labelled CUAD categories whose presence is flagged:
    Uncapped Liability, Non-Compete, Exclusivity, Most Favored Nation, Liquidated Damages,
    Irrevocable Or Perpetual License, Covenant Not To Sue, Joint IP Ownership — labelled span
    present ⇒ `deviation` with that span as evidence, else `compliant`) and *extraction*
    (fields `parties`, `effective_date`, `expiration_date`, `governing_law`, `renewal_term`,
    `notice_period` ← "Notice Period To Terminate Renewal", `liability_cap` ← "Cap On
    Liability", `termination_for_convenience`). Field keys are normalised at build and stored
    with their quote: parties = a **list** of organisation names (answers that
    `normalize_org_name` accepts, role words like "Company"/"Distributor" dropped, deduped);
    dates parsed (a year is required, else the field is dropped as unscorable and counted);
    governing law = jurisdiction parsed from the passage then `normalize_jurisdiction`.
  - **ContractNLI (FR-4b): 10 NDAs × 17 hypotheses** (`nda_checklist`; 4 longest + 6 seeded),
    verdicts Entailment→compliant, Contradiction→deviation, NotMentioned→missing.
  - **LegalBench-RAG (FR-4c): 100 questions** from its CUAD subset **whose document is one of
    the 40 CUAD documents** (shared ingestion), past-25k passages first, at most 3 per
    document, 1–3 snippets from one file, ≤1,500 passage chars (branch filters), unique
    `(doc, question)`.
  - **Constructed review set (FR-5..8): 52 documents**:
    - Common Paper CSA **11** (2 controls) and DPA **7** (1 control) with the branch's planted
      changes, reviewed against `cp_msa` / `cp_dpa` (branch playbooks ported). Each exam
      playbook rule carries `severity_from: {"playbook": <library name>, "clause_type": <rule>}`;
      the build copies that platform rule's `risk_level` into the rule (`risk_level`, which
      the review prompt sees) and into every `PlantedChange.severity`. Frozen mapping:

      | exam rule | severity_from (`app/playbooks/library.py`) | resolves to |
      |---|---|---|
      | cp_msa.limitation_of_liability | Master Services Agreement Playbook / limitation_of_liability | high |
      | cp_msa.governing_law | Customer Agreement Playbook / governing_law | medium |
      | cp_msa.renewal_notice | Software / SaaS Agreement Playbook / renewal | medium |
      | cp_msa.termination_for_breach | Master Services Agreement Playbook / termination | medium |
      | cp_msa.confidentiality | Master Services Agreement Playbook / confidentiality | medium |
      | cp_msa.ip_indemnity | Master Services Agreement Playbook / indemnification | high |
      | cp_msa.payment_terms | Master Services Agreement Playbook / payment_terms | medium |
      | cp_msa.return_of_data | Data Processing Agreement Playbook / return_or_deletion | medium |
      | cp_msa.assignment | Vendor Agreement Playbook / subcontracting | medium |
      | cp_msa.service_warranty | Vendor Agreement Playbook / warranties | medium |
      | cp_dpa.breach_notification | Data Processing Agreement Playbook / breach_notification | critical |
      | cp_dpa.subprocessor_notice | Data Processing Agreement Playbook / sub_processors | high |
      | cp_dpa.subprocessor_liability | Data Processing Agreement Playbook / sub_processors | high |
      | cp_dpa.audit_rights | Data Processing Agreement Playbook / audit | medium |
      | cp_dpa.deletion_at_expiry | Data Processing Agreement Playbook / return_or_deletion | medium |
      | cp_dpa.inquiry_notice | Data Processing Agreement Playbook / data_subject_rights | medium |

      A rule whose referenced platform rule is missing or has no `risk_level` gets no plant,
      and the build prints it (spec edge case).
    - AEGIS templates, **Indian variants (FR-6): 24** — for each of msa, consultancy, vendor,
      saas, sow, dpa: 1 control + 3 planted documents, rendered with
      `app.intake.drafting.render_document` (the app's own filler) using Indian values:
      governing law "the laws of India, and the courts at <Mumbai|New Delhi|Bengaluru> shall
      have exclusive jurisdiction", INR values, Indian-style party names ("… Private
      Limited", "… Pvt. Ltd.", "M/s …") and a registered-office recital with an Indian
      address inserted after the opening paragraph. Reviewed against the matching **library**
      playbook (by name); severity = that rule's `risk_level`. Plant catalogue per type
      (committed in `build.py`): `(rule clause_type, status, find, replace | None)`; the build
      asserts `find` occurs exactly once in the rendered text. Each planted document gets 2–4
      plants on distinct rules (seeded RNG); every rule of the playbook is planted in at least
      one document of its type where the template has that clause.
    - **Long documents (FR-7): 4** (Indian) — an AEGIS MSA (×3) or SaaS (×1) with Schedule 1
      (the SoW template) and Schedule 2 (the DPA template) appended (>45,000 chars), with
      plants placed in the schedules that break the **MSA/SaaS playbook's** own rules
      (payment_terms, data_protection, service_levels…), so they start past 25k. Build
      asserts ≥ 10 plants with `char_start > 25_000` across the constructed set (long
      documents + Common Paper body plants) and prints the count.
    - **Scan twins (FR-8): 6** (≥1 long, ≥2 Common Paper, ≥2 AEGIS) re-rendered with PyMuPDF
      (already a dependency): text laid out on A4 pages → each page rasterised (150 dpi) → a
      new PDF whose pages are those images plus one real text line per page,
      `DocuSign Envelope ID: <seeded hex>`, as the only text layer. `twin_of` points at the
      text original; the twin has no answer record of its own (it shares the original's).
    - Extraction keys for every constructed document (parties, effective_date,
      governing_law, value; plus expiration_date / renewal facts where the template states
      them), quote = the sentence the generator wrote.
  - **Portfolio (FR-9): 40 contracts, 8 questions.** The 24 AEGIS review documents + the 4
    long documents (`uploader: "portfolio"`; the runner, an admin, still reviews them) + 12
    extra AEGIS documents (`pf-01..12`, incl. NDA-template ones and non-Indian governing
    laws), facts drawn with a seeded RNG through `render_document` fields: governing law ∈
    {India·Mumbai, India·New Delhi, India·Bengaluru, England and Wales, Singapore},
    auto-renewal + notice days (vendor/saas/sow, the templates with `{agreement_term}`), end
    date, INR value, counterparty (one counterparty reused in exactly 3 documents). 8
    questions, e.g. "Which contracts renew automatically?", "Which contracts are governed by
    the laws of England and Wales?", "Which contracts expire in 2027?", "Which contracts have
    a total value above INR 1,00,00,000?", "Which contracts need at least 90 days' notice to
    stop them renewing?", "Which contracts are with <counterparty>?". Each key = the exact list
    whose rendered text states the fact (asserted present). Questions are cheap (≈$0.02 each
    in batch); the portfolio's cost is ingesting its contracts, which reuse cuts by ≈40%.
  - **Sample subset (FR-30)** `sample.json`: review 8 documents (3 CUAD incl. ≥2 with
    past-25k labels, 1 ContractNLI, 1 AEGIS control, 1 Indian AEGIS planted + its scan twin,
    1 long); extraction on those + 2 owner documents (1 normal, 1 stamp-only main + its
    `asread` twin); qa 10 questions on the 3 sampled CUAD documents (≥4 past 25k); portfolio
    2 questions over a fixed 6-document sub-portfolio (`portfolio_docs`: the 3 sampled AEGIS
    documents + 3 extra; keys restricted to it); routing 10 requests.

- `owner.py`
  - `owner-import`: for each `OWNER_FILES` name, `owner_file_path()` → copy bytes →
    sha256, page count (PyMuPDF), `kind`: `scan` when every page carries an image covering
    ≥50% of its area and the native text averages <200 chars/page; `stamp_only_scan` when a
    `scan` is one the app would **not** OCR (probe below says no OCR); else `text`. Freeze
    every file the app reads without OCR: `read_as_app` with a probing `FrozenOCR` → frozen
    text + `frozen_text_sha256`. Prints the kinds (expected: 11 scans, 2 stamp-only).
  - `read_as_app` = `ContractFilesService(None, reducto=ocr)._resolve_extracted_text(...)`
    — the app's own reader → `extract_text` → OCR-when-`needs_ocr` decision, unchanged. A
    result whose `method` ends with `_ocr_failed` means "the app wanted OCR" (probe) or, in a
    run, "OCR not frozen" → the document fails loudly (the app path itself swallows the
    provider error).
  - `ocr`: plan = owner scans (stamp-only: forced) + constructed scans whose probe says the
    app would OCR, minus cached; estimate = pages (one credit per page assumed) ×
    `ExamSettings.reducto_usd_per_credit` when set; when unset the estimate shows
    files/pages/credits with the "price not set — set REDUCTO_USD_PER_CREDIT in
    eval/private/exam.env" warning, the ceiling is asked and enforced **in credits**, and
    costs are reported as credits. OCR spend is metered from the actual `credits` Reducto
    returns; the ceiling is checked before each file. Main frozen text of a stamp-only scan =
    the forced OCR text; its `owner-NN-asread` twin reads the file as the app does today
    (FR-23).
  - `prefill`: refuse if `PREFILL_MODEL == settings.claude_model` (also compared with any
    `-YYYYMMDD` suffix removed) — FR-16/AC-16. One synchronous `complete_json` call per
    document (real contracts are never batched — FR-41) with the **whole** frozen text
    (pre-fill is not the system under test) and a JSON schema (`additionalProperties: false`)
    asking for `answers[{field ∈ {parties, effective_date, end_date_or_term, governing_law,
    notice_period, renewal_term}, value, quote}]`, the prompt defining notice period as "the
    notice needed to end the contract or to stop it renewing" (D-14). Ledger row via
    `record_call(sessions=live.sessions, ctx=AICallContext(org_id=live.org_id,
    user_id=owner.user_id, eval_run_id=run_id), feature=<local AIFeature "exam_prefill">, …)`
    — local objects, never registered in the app's feature registry. Then `verify_quote`
    against the frozen text (word for word, whitespace/line breaks ignored, case-sensitive)
    → location or "no supporting sentence found" (FR-17) → facts sheet. One
    `eval.real_contract_sent` per document. Expected cost ≈ $0.65 (≈14 × 15k in + 1.5k out at
    $2/$10 per million).

- `routing.py` — `snapshot_workflows(live)` (enabled `Workflow` rows of the live org,
  read-only); `draft_requests`: one synchronous `complete_json` call per workflow with
  **claude-sonnet-5-5** (`ROUTING_DRAFT_MODEL`), 2 requests for the first 18 workflows by
  `eval_order`, 1 for the rest (= 50 for 32 workflows), channel rotated email/chat/notice,
  40–120 words, Indian business context, never naming the workflow; ledger rows as for
  pre-fill (`exam_routing_draft`). Expected ≈ $0.30 once; kept synchronous (a one-time
  content-creation step, not an exam call on public/constructed content; batching would save
  ≈$0.15 at the cost of a 24-hour wait). `routing_fresh(live, snapshot) -> bool` compares
  the live fingerprint with the snapshot's (AC-21).

- `sheets.py` — XLSX writer/loader per the frozen format; worksheet protection on B–F
  (accident guard only; the loader is the real check); data-validation lists for marks and
  workflow names.

- `evaldb.py`
  - `reset`: on a live-server autocommit connection: `DROP DATABASE IF EXISTS <name> WITH
    (FORCE)`, `CREATE DATABASE <name>`; refuses unless `<name>` starts with
    `legal_clm_eval` and differs from the live database name.
  - `migrate`: `alembic.command.upgrade(Config("alembic.ini"), "head")` (env.py reads the
    already-swapped `settings.database_url`; migrations create `vector`/`pg_trgm`).
  - `bootstrap`: `create_first_admin` (reuse: org, roles, default teams, org access) with
    `exam-runner@example.com` + random password → runner (admin); portfolio user
    `exam-portfolio@example.com`, role `member`, clearance default, `ACTIVE` (non-admin, so
    `resolve_scope_contract_ids(scope="portfolio")` returns exactly the documents it
    uploaded — the app's real access code does the scoping).
  - `load_playbooks`: `seed_playbook_library(db, org_id, actor_id)` (reuse) + the 4 exam
    playbooks as published `Playbook`/`PlaybookVersion`/`PlaybookRule` rows (rule order
    pinned by increasing `created_at`).
  - `load_workflows`: copy the snapshot's workflows with their ids (owner picks reference
    them); never started.
  - `ingest_document` — the upload pipeline's steps without its side effects:
    `storage_service.save_bytes` (exam storage root) → `read_as_app(...)` (or, for
    `text_mode="forced_ocr"`, an `_ExtractedText` built from the OCR cache exactly as the
    OCR branch of `_resolve_extracted_text` builds it, `page_map_from_elements` included) →
    `ContractFilesService(db, reducto=ocr)._persist_intake_records(user=uploader, stored,
    mime_type, title=doc.title, counterparty_name=None, contract_type=None, extracted)`
    (rows, snapshot, document elements) → `contract.lifecycle_stage = REVIEW` set directly
    (the outcome of `_advance_upload_to_review` without `transition_contract_stage`'s
    stage-entry triggers, which dispatch jobs and advance workflows) → commit →
    `generate_embeddings_for_snapshot` → commit. **Not called:** `create_contract_from_upload`,
    `ingest_upload`, `_dispatch_initial_jobs`, `dispatch_job`, `transition_contract_stage`,
    `start_flow*`, `create_request`, any notify function. For owner main documents the
    resulting text sha256 must equal the manifest's `frozen_text_sha256`, else that
    document's keys are out of date (FR-20).

- `runner.py` — `execute_exam_run` (holds `exam_lock` throughout):
  1. `reconcile_crashed_runs` (approved runs with no `eval.run_finished` and no pending batch
     → stamp/mirror any leftover rows, write `run_finished` outcome `incomplete`, reason
     `crashed`).
  2. `load_content()`; exclusions: owner keys whose file or frozen text changed (out of
     date), unmarked rows, owner documents without keys, unpicked routing requests, routing
     out of date when `routing_fresh` is False (routing measure not scored). Missing OCR for
     a planned scan → refuse before asking for money ("run `ocr` first").
  3. Plan units for the mode (full = everything; sample = `sample.json`). Same ordered unit
     list every time for the same content (FR-1). Owner-document units are `sync`; with
     `delivery="batch"` every other unit is `batch`, with `delivery="sync"` all are `sync`.
  4. Estimate: per planned call, expected input/output tokens per call type from the
     planning table below (input scaled by the document's real length), priced with
     `token_cost` at the model under test (`batched=True` for batch units); worst case =
     `worst_cost` (input chars/3, output = the skill's `max_tokens`). Once a complete run of the
     same mode and delivery exists, its mean actual cost per call type replaces the planning
     figures ("calibrated" shown).
  5. `approve_paid_run` (estimate printed; go-ahead + ceiling; audit). Write `state.json`.
     Install `claude.exam_meter = Meter(ceiling)`.
  6. `reset` → `migrate` → `bootstrap` → `load_playbooks` → `load_workflows` → ingest every
     planned document (free steps).
  7. **Phase 1** — per document, clause labelling (`clause_skill_input` → `run_job_skill`)
     then metadata (`run_job_skill`, `contract_metadata_extraction`) as listed in `ai_steps`
     (clauses first: clause labelling does not change any input of the metadata prompt, so
     the metadata request is the same whether the two run in sequence or are captured
     together — exactly the app's situation, where both jobs start from the same upload
     state); routing triage: `triage(db, IntakeRequest(id=uuid5(EXAM_NS, item_id), org_id,
     type_label="Other", subject, description=body, field_values={}, priority="Medium",
     source=channel))` via `asyncio.to_thread`, never added to the session. Then, free:
     `ingest_contract_brain` for documents with `"brain"`.
  8. **Phase 2** — review: rules = `PlaybooksService(db)._auto_rule_payloads(...)` of the
     item's playbook; `ai_controller.run_structured_skill(db, skill_name="playbook_review",
     org_id, created_by_user_id=runner.id, input_payload={contract_id, contract_version_id,
     playbook_id, playbook_version_id, rules}, request_id=item_id, resource_type="contract",
     resource_id=contract_id)` — the same call `auto_review_contract` and the run route make;
     deviations mapped with `ai_deviations_to_evaluated`; attribution: rule by index → else
     clause_type via `canonical_clause_type` equality → else the quote matches a planted
     `changed_text` (rapidfuzz partial ratio ≥ 90) → else unattributed. Status = `missing`
     when the deviation has no `original_text`, else `deviation`. Q&A:
     `ask_contract_brain(BrainAskRequest(question, query_scope="contract", contract_id),
     request=<state.request_id=item_id>, db, current_user=runner)` — the route function
     itself (`aggregate_answer` → `resolve_scope_contract_ids` → `hybrid_sources` →
     `sources_to_context` → `contract_brain_answer` → `ground_answer`); the context is
     whatever the app's search returned, never replaced (FR-24, FR-29 defect 1);
     `context_chars` recorded and empty contexts counted. Portfolio: same call as the
     portfolio user with `query_scope="portfolio"`; `named_doc_ids` = portfolio documents
     whose counterparty (`normalize_org_name`) or title appears in the displayed answer.
     Extraction predictions are read after phase 1 from the `Contract` row
     (counterparty_name split into parties on `;` `,` ` and ` ` & ` after joining ", Inc."-
     style suffixes, effective/expiration dates, jurisdiction, value_amount + currency) and
     `ClauseExtraction` rows (renewal_term → {renewal, term_and_termination}; notice_period →
     {renewal, term_and_termination, notices}; liability_cap → {limitation_of_liability};
     termination_for_convenience → {term_and_termination}).
  9. **How each phase is delivered.** `asyncio.Semaphore(4)`, **one session per unit**, sync
     app code via `asyncio.to_thread`.
     - `sync` units: `meter.mode = "live"`; every call is a normal request checked against the
       ceiling by worst-case reservation (`before`) and settled to its real cost (`after`).
     - `batch` units (FR-41):
       a. **Capture**: `meter.mode = "capture"`; run the phase's batch units. Each runs the
          app code up to its one AI call, where the hook records the request and raises
          `Deferred`; the unit wrapper catches it, rolls back and closes its session. Units
          that finish without an AI call are `done` (their writes are real and kept).
          Leftovers of an aborted unit: an `AISkillRun` row left `running` and its
          `ai.skill_started` timeline event, in the exam DB only; no ledger row (the gateway
          only records `Exception`s), no fallback output.
       b. **Admission**: `meter.admit(...)` keeps requests in planned unit order while
          `spent + reserved + worst_cost(batched=True) ≤ ceiling`; the rest are
          `not_attempted` (run becomes `incomplete`, reason `ceiling_reached`).
       c. **Submit** one batch for the phase (`create_batch`, ≤ 100,000 requests / 256 MB —
          far above the exam's ~400). `state.json` is written `submitting` before and
          `submitted` + batch id after; a create error (no automatic retry) sets
          `submit_failed` and stops the process with a message.
       d. While the batch runs: `meter.mode = "live"` and the phase's `sync` (owner) units
          run normally.
       e. **Poll** `batch_status` every 60 s, printing request counts, until
          `processing_status == "ended"` (the provider ends a batch at 24 h, expiring what is
          left). Fetch `batch_results`. Results `errored` with a transient error type
          (`api_error`, `overloaded_error`, `rate_limit_error`) are resubmitted **once** as a
          retry batch under the same admission rule (mirrors the synchronous path's retries);
          `expired`, `canceled` and other errors are final.
       f. **Replay**: `meter.mode = "replay"` with `results` / `batch_id_of`; re-run every
          captured unit with a fresh session (clause before metadata per document). The hook
          returns the stored message for exactly the same request, the app persists the
          answer and the gateway writes the ledger row (tokens from the batch message,
          `provider_request_id = "<batch_id>:<custom_id>"`). A request that is not in the
          results, was not answered (`errored`/`expired`/`canceled`), or whose payload
          changed since capture raises `BatchCallFailed` → the app's normal failure path →
          `FAILED` ledger row → the item is a failed call (FR-40). A replayed answer is
          never given to a different request: the custom id is the request's hash.
       g. `meter.settle_batch`: release the worst-case reservations, add the actual cost of
          answered requests (`token_cost(..., batched=True)`).
  10. After every unit (both modes): `stamp_and_mirror` (stamps `eval_run_id` and `batched`
      in the exam DB, copies new rows to live). `state.json` updated per unit. If
      `meter.stopped`, launch nothing more; in-flight units finish (their refused calls fail).
  11. Item status from the ledger, not from exceptions (the app swallows many into
      fallbacks): an item is `failed` if any of its calls — or of its document's upstream
      phase-1 calls — has `status` ≠ `succeeded`, a skill run used its fallback, a playbook
      review produced no AI output, triage returned `degraded`, or an exception escaped;
      `not_attempted` if never started or not admitted. `contains_errors` = any failed call;
      `failed_call_rate` = failed AI calls ÷ AI calls (ledger rows + unanswered batch
      requests); outcome `incomplete` when anything is `not_attempted`, the ceiling was
      reached, or the process was interrupted (FR-40).
  12. Per call: cost (`token_cost`, batch flag), quotes (citations / `original_text` from the
      exam DB ledger's `validated_output`) checked word for word against the document's
      snapshot text (portfolio: any portfolio text) and with `align_citation_to_source`
      (≥85, the app's own check, second number); coverage for metadata / review /
      clause_extraction calls = `len(build_contract_context(...).text)` recomputed with the
      same inputs (deterministic) ÷ snapshot length, for `clause_labeling` = total excerpt
      chars of its segments ÷ snapshot length (FR-10). Time: per-call latency for sync calls;
      per-phase batch turnaround (submitted → ended) for batch calls.
  13. Write `results.json` (0444) + `detail.md` (private), print the summary, `finish_paid_run`
      (`eval.run_finished` with `results_sha256`), unset `exam_meter`.

  `resume_exam_run` (owner-gated, `run --resume`): refuses if the run already has
  `eval.run_finished`, has no `state.json`, or the exam DB no longer exists (then: `--abandon`).
  Audits `eval.run_resumed`; rebuilds the meter from the approved ceiling and the run's
  mirrored spend + outstanding batch reservations; continues from `state.json`: `submitted`
  → poll/replay; `captured` (nothing paid yet for the phase) → capture again (free) and
  submit; `submit_failed` → asks "A batch for phase N may have been created without a reply —
  check the Anthropic console. Submit again? [y/N]"; units already `done` are never re-run.
  `abandon_exam_run` (`run --abandon`): `cancel_batch` for a pending batch, stamp/mirror,
  `eval.run_finished` outcome `incomplete`, reason `abandoned`.

- `score.py` (pure; re-run at report time on stored predictions, so a baseline and a
  change are always scored by the same scorer):
  - Review (FR-22): unit = (document, rule). flagged = `deviation|missing`. TP/FP/FN →
    precision, recall, F1 (micro). Unattributed findings = false alarms. Every flag on a
    control document = false alarm (AC-5). `missed_high_critical` lists FN plants with
    severity high/critical by item and rule. NDA 3-way verdict accuracy reported for the
    ContractNLI set. Past-25k bucket by the key's evidence/plant position, located in the
    run's snapshot text with `align_citation_to_source` (score ≥ 90; else "position unknown",
    counted). Scan twins and their originals are reported side by side and **excluded from
    the headline** (FR-8).
  - Extraction (FR-23): per field accuracy; headline `extraction_accuracy` = mean of the
    per-field accuracies (macro). Normalisation: parties as sets of `normalize_org_name`
    keys, order-free, each key party one scored unit (extra predicted parties reported as
    "extra", not counted against accuracy); dates via `dateutil` (year required); `date_or_term`:
    a date key compares to `expiration_date`, a duration key ("3 years") compares to
    `effective_date + duration`; jurisdiction via `normalize_jurisdiction`; amounts parsed
    from "INR 2,500,000.00", "₹25,00,000", "Rs. 25 lakh", "2 crore", "USD 5,000"
    (lakh ×10⁵, crore ×10⁷) to Decimal + currency; passage fields: correct when an app
    clause of the mapped types overlaps the key quote (containment or ≥60% shared words of
    the shorter, the branch's `_overlaps`); "not stated" key ⇒ correct only for an empty
    prediction, except on renewal_term / notice_period / termination_for_convenience, where
    it is counted and not scored (spec D-16). Before/past 25k by the key quote's position. `owner-NN-asread` documents are
    scored on their own line and never in the main figures.
  - Q&A (FR-24): `search_found` = any source text overlaps any key passage;
    `answer_cited` = any citation quote overlaps any key passage (headline
    `qa_evidence_found`); both split before/past 25k by passage position.
  - Portfolio (FR-9): per question found / missed / wrongly included vs
    `expected_doc_ids` (sample mode: restricted to `portfolio_docs`); headline
    `portfolio_completeness` = micro F1 over all questions (completeness without rewarding
    naming everything).
  - Routing (FR-25): share of keyed requests whose `workflow_id` equals the owner's pick
    (`null` pick matches `null`).
  - Label-free: `quote_validity` (word-for-word share, invalid quotes listed in the private
    detail only) + `quote_alignment_85`; `truncation_rate` = calls with `stop_reason ==
    "max_tokens"` ÷ calls, per call type and pooled; `coverage_avg` and per call; cost,
    tokens, latency / batch turnaround per call type and total (totals are sums of the
    parts — AC-14).
  - Pair (FR-13/27): per measure run A, run B, average, noise band = |A − B|; `consistency`
    = share of items scored in both runs with the same normalised prediction (review: flagged
    rule set; extraction: normalised field values; qa: (search_found, answer_cited); portfolio:
    named set; routing: workflow id).
  - Verdict (FR-28): main measure by target — review→`review_f1`, extraction→
    `extraction_accuracy`, qa→`qa_evidence_found`, routing→`routing_accuracy`,
    portfolio→`portfolio_completeness`. For each **headline quality** measure, `delta =
    (change_avg − baseline_avg) × (+1 if higher-is-better else −1)`, `band` = the
    baseline's noise band. PASS iff main `delta > band` AND no other headline quality
    measure has `delta < −band`. Report-only measures and cost/time are shown (change,
    baseline, difference) and never decide (D-10, D-12). No target recorded at run start (in
    both change runs' `eval.spend_approved`) ⇒ `no_verdict`. A measure with n = 0 on either
    side ⇒ `not_measured` (does not fail; if it is the main measure ⇒ `no_verdict`). Routing
    out of date in either pair ⇒ routing `not_measured`.

  **Headline quality measures (decide pass/fail):** `review_f1`, `extraction_accuracy`,
  `qa_evidence_found`, `portfolio_completeness`, `routing_accuracy`, `quote_validity`,
  `truncation_rate` (lower is better). **Report-only (D-12; reported with n and band, never
  decide):** `coverage_avg`, `consistency`, precision/recall, NDA verdict accuracy,
  before/past-25k splits, per-field accuracy, stamp-only "as read today" line, scan-twin
  side-by-side, `qa_search_found`, `quote_alignment_85`, owner prefill-changed rate, failed
  calls. **Cost/time (D-10):** `cost_usd`, list-price cost, tokens, latency, batch
  turnaround, wall time.

- `report.py` — `detail.md` (private: every measure, every failed call, invalid quotes,
  missed findings with quotes, per-call coverage), public reports (numbers only; owner
  documents appear only as `owner-NN`; a guard refuses to write a public report containing
  any 8-word sequence from an owner frozen text), delivery mode in every header, sample runs
  labelled "SAMPLE — not comparable to the baseline", superseded history section (FR-29).
  `check_pair` (FR-27, D-13, FR-41): both `eval.run_finished` rows exist, both `kind=exam`,
  `mode=full`, `outcome=complete`, identical `delivery`, `app_code_sha256`, `models`,
  `prompts`, `content_fingerprint`, each `failed_call_rate ≤ 0.01` (refused above 1%; the
  "contains errors" flag shown when 0 < rate ≤ 1%), and each `results.json` sha256 matches
  its audit row (tamper check). `compare` also refuses when the change pair's `delivery`
  differs from the baseline's (AC-41).

- `__main__.py` — argparse → command functions. First statements: parse args, then
  `from eval.exam.env import isolate` and `live = isolate(paid=…)`; only then import the
  other exam modules. Gated commands require `sys.stdin.isatty()`, call
  `authenticate_owner(…, getpass=getpass.getpass)` and pass `ask=input`.
- `README.md` — operator guide: setup (`backend/eval/private/exam.env` with
  `EVAL_CLAUDE_API_KEY`, `EVAL_OWNER_EMAIL`, optional `REDUCTO_USD_PER_CREDIT` — never in
  `backend/.env`), dataset downloads (branch README instructions), command order (`build` →
  `owner-import` → `ocr` → `prefill` → fill sheet → `load-sheet` → `draft-routing` → fill
  sheet → `load-sheet` → `run --mode sample` → 2 × `run --mode full` → `baseline set` →
  later: 2 × `run --mode full --change X --target Y` → `compare`), batch waits and
  `--resume`/`--abandon`, licences and credits, what is private vs committed.
- `backend/eval/results/superseded/` — the 7 branch result files unchanged + `README.md`:
  "earlier, superseded — not a baseline", listing the three defects (empty Q&A search
  context; parties scored as one string against un-normalised keys; routing on 13 old
  workflows with AI-written labels) and how this exam removes each (FR-29).

**Router:** none new (the analytics route only gains a query parameter and the price fix).
**Celery:** none — no task, no beat entry. The exam is a foreground CLI process (FR-31).
**Settings:** none on the app's `Settings` class, so no `validate_runtime_settings()` entry
and no change to `test_phase10_security_hardening.py` (decision 20). No new `MOCK_*` flag
and no new integration: the exam uses the existing Claude (`claude.py`) and Reducto
(`reducto.py`) clients.

#### Exam size, cost and sensitivity (D-11)

Planning cost per call (model under test claude-sonnet-4-6, $3 / $15 per million; tokens
from the live ledger's averages — metadata 3.5k/1.0k, clause labelling 8.5k/1.4k, review
7.3k/3.0k, Brain answer 5.6k/1.3k, triage 3.3k/0.45k — with input scaled to document length
and review output to the rule count):

| Call type | Expected in / out tokens | List $ / call | Batch $ / call | Worst case $ / call (list) |
|---|---|---|---|---|
| metadata, long doc (≥25k chars) | 7.5k / 1.0k | 0.038 | 0.019 | 0.061 |
| metadata, template doc / NDA / scan twin | 6.0k / 1.0k · 4.0k / 1.0k · 1.8k / 0.6k | 0.033 · 0.027 · 0.014 | half | 0.06 |
| clause labelling, long / template | 10k / 1.6k · 6k / 1.2k | 0.054 · 0.036 | half | 0.13 |
| review: CUAD checklist / NDA / constructed / scan twin | 9k/0.8k · 5k/1.5k · 8k/1.6k · 3k/1.5k | 0.039 · 0.038 · 0.048 · 0.035 | half | 0.156 |
| Brain answer (Q&A, portfolio) | 5.6k / 1.3k | 0.036 | 0.018 | 0.069 |
| intake triage | 3.3k / 0.45k | 0.017 | 0.008 | 0.027 |
| owner metadata + clauses (sync, never batched) | — | ≈0.098 per document | — | 0.22 per document |

Full run, per set:

| Set | Size | AI calls | Delivery | List price | Price paid (batch ×0.5) |
|---|---|---|---|---|---|
| CUAD long contracts — review + extraction + Q&A host (FR-4a) | 40 contracts | 120 (metadata, clauses, review) | batch | $5.22 | $2.61 |
| LegalBench-RAG Q&A (FR-4c) | 100 questions on the 40 CUAD docs | 100 | batch | $3.60 | $1.80 |
| ContractNLI NDA verdicts (FR-4b) | 10 NDAs × 17 = 170 verdicts | 20 | batch | $0.65 | $0.33 |
| Constructed review (FR-5..8) | 52 docs: Common Paper 18 (3 controls), AEGIS Indian 24 (6 controls), long 4, scan twins 6 | 104 (metadata, review) | batch | $4.12 | $2.06 |
| Portfolio (FR-9) | 40 contracts (28 reused + 12 extra), 8 questions | 60 | batch | $2.20 | $1.10 |
| Routing (FR-21) | 50 requests | 50 | batch | $0.85 | $0.43 |
| Owner-confirmed (FR-15, FR-23) | 14 contracts + 2 "as read today" | 32 | **sync** | $1.43 | $1.43 |
| **Total** | | **486** (454 batch, 32 sync) | | **$18.07** | **≈ $9.75** |

Worst case (every answer at `max_tokens`, the guaranteed-completion ceiling): ≈ $24.50 for a
full batch run (phase 2 is admitted against phase 1's *actual* spend, so in practice a
ceiling of ≈ $18 completes). Sample run (56 calls: CUAD 3 docs, NDA 1, constructed 4, owner
2 + 1 `asread`, Q&A 10, portfolio 6 docs + 2 questions, routing 10): ≈ **$1.93 list,
≈ $1.08 in batch delivery**, worst case ≈ $3. One-time steps outside runs: pre-fill
≈ $0.65 (claude-sonnet-5-5, $2/$10), routing drafting ≈ $0.30, OCR = Reducto credits for the
11 scans' pages (price set by the owner). Time: a full batch run is two batch waits —
usually about 2 hours end to end, at most 2 × 24 hours.

Expected noise band per headline measure (the band is measured by the two baseline runs;
this is what to expect, assuming ~10% of units answer differently between identical runs —
consistent with the ±0.04 F1 measured on the earlier 120-verdict sample — and band ≈
0.8·√(share differing / n)):

| Headline measure | Scored units (full run) | Expected band now | At the earlier ~2× volume | Smallest change that can pass |
|---|---|---|---|---|
| review F1 | ≈260 flagged-or-planted units (CUAD ≈48, NDA ≈100, constructed ≈110) | ≈ ±0.016 | ≈ ±0.011 | ≈ +0.02 F1 |
| extraction accuracy (macro over fields) | ≈500 field items (per field 20–110) | ≈ ±0.015 | ≈ ±0.011 | ≈ +0.015–0.02 |
| Q&A evidence found | 100 questions | ≈ ±0.025 | ≈ ±0.018 | ≈ +3 questions in 100 |
| portfolio completeness | ≈50 expected contract mentions over 8 questions | ≈ ±0.045 | unchanged (same 40 / 8) | ≈ +0.05 |
| routing accuracy | ≈50 requests | ≈ ±0.03 | unchanged | ≈ +2 requests in 50 |
| quote validity | ≈1,500 quotes | ≈ ±0.005 | ≈ ±0.004 | ≈ +0.005 |
| truncation rate (lower better) | ≈486 calls | ≈ ±0.005 | ≈ ±0.004 | ≈ −0.005 |

Sensitivity lost by halving: every band widens by about √2 (×1.4), so a change must move
review F1 by about 0.02 (not 0.015) and Q&A by about 3 questions in 100 (not 2) to pass.
Sub-buckets become indicative only: past-25k review plants (n ≈ 12–20, band ≈ ±0.08), Q&A
past 25k (n ≈ 50, ≈ ±0.035), per-field accuracy (n 20–110, ±0.03–0.07), the stamp-only "as
read today" line (n = 12 field items, ±0.2). Every number carries its n so a small bucket is
not over-read (spec edge case).

### Frontend (Next.js page/component tree)

- `src/app/(app)/ai-usage/page.tsx` — adds `traffic` state (`"real"` default) and a second
  segmented toggle next to the 7d/30d/90d one, styled exactly like it: **Real usage** /
  **Exam traffic**. `queryKey: ["ai-usage", days, traffic]`, `queryFn: () =>
  aiUsageApi.summary(days, traffic)`. When `traffic === "exam"`: header description becomes
  "AI calls made by the AI exam (backend/eval/exam). They are never counted in real usage;
  batch calls are priced at half the list rate.", a `Badge` "Exam traffic" is shown in the
  header actions, the total card label reads `Exam spend (${days}d)`, and the empty state
  reads "No exam calls in the last N days." The real-usage description's hardcoded "Sonnet:
  $3 / $15" text becomes "priced per model at list rates" (the rate table is now per model).
  Everything else (tables, daily chart) renders the same data shape.
- New shared primitives in `src/components/ui.tsx`: **none**. The page already imports
  `Badge`, `Card`, `CenterSpinner`, `EmptyState`, `ErrorState`, `PageHeader`, `StatCard`,
  `Table`, `TD`, `TH`, `THead`, `TR` from `@/components/ui` — no new import needed. Toggle uses
  the existing `slate-*`/`brand-*` classes (no `bg-white`, no hardcoded colours).
- Demo-mode behavior: not applicable — this page has no demo-mode path today
  (`src/lib/demo.ts` does not exist).

## Test strategy

All backend tests run with `MOCK_CLAUDE`/`MOCK_REDUCTO` forced on by `conftest.py`; no test
can spend money or reach the exam key (paid commands refuse under mock). Tests that must
exercise the real request path (hook, batches) switch `mock_claude` off for that test and
install the fake from `tests/exam_fake_anthropic.py` in place of `_anthropic_client()` — a
~40-line fake exposing `messages.stream(**payload)` (final message built from
`app/integrations/_claude_mock.structured_payload_by_tool()` by the payload's tool name) and
`messages.batches.create/retrieve/results/cancel` (ended immediately; per-request result
type configurable: succeeded / errored / expired). Docstrings name the failure mode each test
guards.

- **`tests/test_exam_content.py`** — `owner_file_path` refuses a non-listed name and never
  opens it (patched `open`/`Path.open` asserts no call) — AC-15; NFC/NFD spelling of an
  allowed name resolves; never lists the folder. Committed content validates against the
  models; set sizes match the plan (CUAD 40, Q&A 100, NDA 10, constructed 52, portfolio 40/8);
  every public item names dataset/version/licence (AC-2); every constructed plant's severity
  equals its library rule's `risk_level` (direct or via `severity_from`) (AC-5);
  Indian-variant documents exist with records (AC-6); ≥10 plants past 25k (AC-7); every scan
  twin points at an original and has no record of its own (AC-8); portfolio keys non-empty;
  Q&A `(doc, question)` unique; sample covers every task and ≥1 past-25k item per task
  (AC-30); `content_fingerprint` stable across loads and changes when a key changes; no file
  under `content/` or `results/superseded/` contains any owner file name.
- **`tests/test_exam_score.py`** — AC-9 (7 expected, 5 right + 1 wrong ⇒ 5/2/1); AC-10 (25,000
  of 60,000 ⇒ 42% and the average); AC-11 (max_tokens counted per type); AC-12 (10 quotes,
  8 valid ⇒ 80%, the 2 listed in detail); AC-13 consistency; AC-14 totals = sum of parts;
  AC-22 (10 plants, 8 found, 1 critical missed, 2 false alarms ⇒ P/R/F1 and the critical
  named); AC-23 ("Acme Ltd", "Beta Pvt Ltd" vs "Beta Pvt Ltd, Acme Ltd." correct; "1 April
  2024" vs "2024-04-01" correct; past-25k item counted only in past-25k; `asread` line
  separate); amounts lakh/crore/₹; jurisdiction normalisation; "not stated" correct only for
  empty; control flags = false alarms; failed items never scored (AC-40); prefill-changed
  rate (AC-19).
- **`tests/test_exam_compare.py`** — AC-27 pair acceptance and refusal (different code,
  sample, incomplete, tampered results file); D-13: failed-call rate 1.0% accepted with the
  flag, 1.1% refused; AC-28 all five cases (0.80/0.84 vs 0.87 pass; quote validity drop fails
  and is named; truncation rise counts as worsening; cost doubled still passes with cost
  shown; no target ⇒ no verdict); coverage or consistency dropping beyond their band never
  fails (D-12); different content ⇒ refused; batch baseline vs sync change ⇒ refused (AC-41);
  band 0 behaviour; earlier baseline kept after `--replace`.
- **`tests/test_exam_sheets.py`** — AC-17 (unsupported row offers only `wrong`; a forced
  `correct` rejects the sheet); AC-18 (correct ⇒ key; wrong + value ⇒ value; blank ⇒
  unmarked and counted; changed locked cell / missing row / extra row ⇒ whole sheet rejected,
  nothing written); routing sheet carries the workflow fingerprint and a changed live list
  flags routing out of date (AC-21); `load-sheet` refuses a path outside `eval/private/sheets`.
- **`tests/test_exam_gate.py`** — `exam.env` missing / owner unset ⇒ refused; admin who is not
  the owner ⇒ refused and `eval.access_refused` audited (AC-31); wrong password ⇒ refused;
  owner ⇒ accepted; approval refused ⇒ `eval.spend_refused`, no provider call; two
  invocations ask twice (AC-31); exam key missing or equal to the real key ⇒ refused before
  any call (AC-34); `PREFILL_MODEL == claude_model` ⇒ prefill refused (AC-16); production
  environment ⇒ refused; `isolate` refuses when `app.core.database` is already imported;
  `reset` refuses a database name not starting `legal_clm_eval` or equal to the live one; OCR
  estimate without `REDUCTO_USD_PER_CREDIT` shows the warning and asks a ceiling in credits.
- **`tests/test_exam_claude_client.py`** — `complete_json` payload has `output_config.format`
  json_schema, no `temperature`/`tools`/`tool_choice`; `exam_meter.before` raising prevents
  the request and `reserve_tokens`; a returned replay is used with no request; `after` gets
  the response, and `None` when the request failed; `Deferred` passes through the gateway's
  and controller's `except Exception` blocks unrecorded; a replay offered to
  `stream_with_tools` raises; batch methods call the SDK with the given requests, `create`
  is not retried, all four refuse in mock mode; with `exam_meter` None the request path is
  unchanged.
- **`tests/test_exam_batch.py`** (fake client, test DB) — capture of a real
  `run_structured_skill` call leaves no ledger row and no fallback output; replay persists the
  answer, writes a ledger row with `provider_request_id "msgbatch…:<custom_id>"`, and
  stamping marks it `batched`; an `expired` / `errored` result ⇒ `FAILED` ledger row and a
  failed item (AC-41); a transient `errored` result is retried once; a request whose payload
  changed since capture ⇒ failed, never answered with another request's result; admission by
  worst case leaves later units `not_attempted` and the run `incomplete` with spend ≤ ceiling
  (AC-32); `settle_batch` adds actual cost ×0.5; `--resume` from `submitted` polls and
  replays without re-running `done` units; `--abandon` cancels and finishes `incomplete`;
  owner-document units are never captured (sync) in batch delivery (AC-41).
- **`tests/test_exam_ledger.py`** — `record_call` writes `ctx.eval_run_id` (and None by
  default); `mirror_values` nulls payload/id columns, keeps `batched`, rewrites org and actor;
  stamping touches only rows with `eval_run_id IS NULL`; crashed-run reconciliation
  (`exam_sessions=None`) writes exactly one `eval.run_finished` with `incomplete`/`crashed`
  and skips a run waiting on a batch. The real two-database copy and its idempotency are
  covered by `test_exam_isolation`.
- **`tests/test_analytics_ai_usage_traffic.py`** — default excludes rows with
  `eval_run_id`; `traffic=exam` returns only them; response echoes `traffic`; bad value ⇒
  422; user without `admin_panel:access` ⇒ 403 (AC-34); `token_cost` per model
  (sonnet-4-6 $3/$15, sonnet-5-5 $2/$10, haiku-4-5 $1/$5, opus-5-5 $4/$20, dated snapshot ⇒
  its model, unknown ⇒ $3/$15), cache read 0.1× / write 1.25×, `batched` ×0.5 on all of them;
  a batched and an unbatched row of the same operation fold into one entry with the right
  total.
- **`tests/test_exam_isolation.py`** (one slow integration test, skipped only if the DB user
  cannot `CREATE DATABASE`; CI's `legal_clm` is the container superuser) — runs
  `tests/exam_isolation_driver.py` in a **subprocess** (process-level isolation can only be
  tested in a fresh interpreter): `isolate(paid=False, eval_db_name="legal_clm_eval_ci")`
  with `ALLOW_MOCK_EMBEDDINGS=true`, then a dummy key and the fake Anthropic client, a mini
  content (1 constructed review doc, 1 CUAD-style Q&A doc, 1 portfolio doc, 1 routing
  request, 1 owner-style doc marked sync), injected owner and `ask`, **batch delivery**,
  `run_ai_job.delay` patched to raise. Asserts: live `contract` count and every non-ledger,
  non-audit live table count unchanged; new live rows = `a_i_call_log` rows with
  `eval_run_id` (batch ones `batched = true`) + `audit_log` rows with `eval.*` actions only
  (AC-38); no Celery dispatch; no Resend/SendGrid send; Q&A `retrieval_metadata.contract_ids`
  ⊆ exam contract ids while the live DB holds contracts (AC-37); the sync-marked document
  never went through the batch (AC-41); dropping the exam DB and running again rebuilds it
  (AC-38); results file written and its sha in `eval.run_finished` (AC-39). Drops the DB.
- **Frontend vitest:** none new — one query parameter, a toggle and copy; `npm run typecheck`
  pins the `AiUsageSummary`/`aiUsageApi.summary` contract; existing `npm run test` must stay
  green.
- **Gates:** backend `ruff check .`, `alembic upgrade head`, `pytest -q`; frontend
  `npm run typecheck`, `npm run test`.

Acceptance verification (`/verify`, evidence per AC):

| AC | Evidence |
|---|---|
| AC-1 | two full runs: identical ordered `items[].item_id` and `documents[].doc_id` in both `results.json`; each has a report |
| AC-2 | `test_exam_content`; report `splits` by category |
| AC-3 | `owner_facts.json` statuses (`suggested_by` = PREFILL_MODEL ≠ model under test; no unmarked value in scoring); build code has no AI call |
| AC-4 | report `counts` (CUAD 40, ContractNLI 10, Q&A 100, past-25k counts) |
| AC-5..8 | `test_exam_content`; constructed records in `items/review.json`; report scan side-by-side line |
| AC-9..14 | `test_exam_score` |
| AC-15 | `test_exam_content` allowlist test; owner-import code reads by name only |
| AC-16, AC-17 | `test_exam_gate`, `test_exam_sheets`; prefill ledger rows show `claude-sonnet-5-5` |
| AC-18, AC-19 | `test_exam_sheets`, `test_exam_score`; `eval.labels_loaded` audit row |
| AC-20 | `test_exam_gate` (estimate before OCR); OCR cache + `FrozenOCR` raises without fill; run ledger has no OCR spend |
| AC-21 | `test_exam_sheets`; report routing coverage counts; `requests.json` `drafting_model` |
| AC-22..26 | `test_exam_score`; the baseline report contains every FR-26 measure with n + explanation |
| AC-27, AC-28 | `test_exam_compare`; `baseline set` refusal transcript |
| AC-29 | `results/superseded/README.md`; report history section; Q&A sources non-empty; parties lists; routing keys from owner picks against the 32-workflow snapshot |
| AC-30 | sample run report label + estimate lower than full |
| AC-31 | `test_exam_gate`; no `eval.exam` in `.github/workflows/` or `celery_app.conf.beat_schedule` |
| AC-32, AC-33 | `test_exam_batch`, `test_exam_claude_client`; run summary actual vs estimate per call type |
| AC-34 | `test_exam_gate`, `test_analytics_ai_usage_traffic`; AI usage page toggle |
| AC-35 | `isolate()` settings (providers local, Voyage/Cohere/Sentry off, mocks on) + `eval.real_contract_sent` destinations only `reducto` / `anthropic:*` |
| AC-36 | `git check-ignore` on private paths; `git status` clean after a session; public-report guard test |
| AC-37, AC-38 | `test_exam_isolation`; live row-count diff before/after a real full run |
| AC-39 | `eval.run_finished` row; audit immutability (ORM guard + hash chain); results sha check |
| AC-40 | `test_exam_score` (failed not scored); crashed-run reconciliation test; `check_pair` refuses `incomplete` |
| AC-41 | `test_exam_batch`, `test_exam_compare`, `test_exam_isolation`; a real full run's ledger: owner rows `batched = false`, all others `true`; estimate text shows the 24-hour note; report header shows the delivery mode |

## Risks & decisions

**Decisions (rationale; rejected alternative):**

1. **Process-level isolation: the exam process's default engine is the exam DB.** App code
   opens `SessionLocal` internally (ledger own-session writes, off-thread authz audit,
   triage), so passing an exam session around would let any missed path write the live DB.
   Swapping `settings.database_url` before `app.core.database` is imported makes "touch only
   the exam DB" the default and "touch live" an explicit, small set of exam calls.
   *Rejected:* explicit eval session everywhere (leaky); a separate container/compose
   service (heavier, duplicates config).
2. **Ledger rows are copied to live after each unit (metering columns only), not redirected.**
   Skill-run ledger rows are FK targets for citations inside the exam DB (`record_on=db`), so
   they must exist there. *Rejected:* changing the gateway to write live rows (production
   behaviour change, and contract text would land in the live ledger).
3. **New nullable `a_i_call_log.eval_run_id`.** Explicit, indexed, gives both the exam/real
   split and per-run cost. *Rejected:* overloading `request_id`/`resource_type` with magic
   values (changes their meaning; every reader must know the convention).
4. **The run record is the immutable audit trail + a sha-pinned private results file.**
   FR-38 allows only ledger and audit rows in the live DB; `audit_log` is already immutable
   and hash-chained, so FR-39's "not editable" comes for free. *Rejected:* a new live
   `eval_run` table (violates FR-38); a table in the exam DB (thrown away every run).
5. **Severity comes from the platform's own playbook library (`app/playbooks/library.py`),
   for all constructed changes.** It is the only rule set with severities a person set, that
   the product really reviews against, and that is pinned in code. AEGIS-template changes read
   their own library rule; Common Paper changes are judged by exam playbooks whose rules name
   the library rule they correspond to (`severity_from`). *Rejected:* the branch's
   per-change hand-set severities (D-9 says severity comes from the rule, not the change);
   live DB playbook rows (org-editable, not pinned).
6. **Owner gate = configured owner email + AEGIS password re-entry + possession of the exam
   key + audit.** A permission string would admit administrators, which FR-31 forbids. Honest
   ceiling: anyone with root on the host can edit code; no in-process check stops that. The
   gate stops other team members (admins included) from starting runs or approving spend,
   makes every run attributable, and the dedicated key bounds the blast radius.
7. **Interactive per-invocation go-ahead; no approve flag, no stored approvals.** A go-ahead
   cannot carry over because it only exists inside the invocation it was typed into
   (`--resume` continues the same run under its recorded ceiling). *Rejected:*
   `plan`/`approve` commands with tokens (state to expire and guard).
8. **Ceiling via an inert hook in `claude.py`, with worst-case admission.** It is the one
   place every paid call passes. Every synchronous call and every batch request is admitted
   only if `spent + reserved + its worst case (answer at max_tokens) ≤ ceiling`, so actual
   spend can never exceed the ceiling, even though a batch cannot be stopped once sent. The
   cost: a ceiling that guarantees completion (≈ $24.50 worst case, ≈ $18 in practice) is
   higher than the expected spend (≈ $9.75); the dialogue shows all three numbers. See open
   question 1. *Rejected:* checking between items (paid calls could continue past the
   ceiling); expected-cost admission (a sent batch could overshoot).
9. **XLSX via `openpyxl` (already a dependency).** Dropdown validation for marks/workflows,
   no Excel CSV date/encoding mangling. *Rejected:* CSV.
10. **The exam calls the pipeline's steps, not `create_contract_from_upload`.** The
    orchestrator's tail dispatches Celery jobs and fires lifecycle triggers (job dispatch,
    workflow advance) — exactly what FR-38 forbids. Reading and persisting use the app's own
    (private) `ContractFilesService` steps and the job handlers' functions, so contracts are
    read exactly as the app reads them. The isolation integration test exercises the whole
    sequence so signature drift fails CI.
11. **Q&A and portfolio call the `ask_contract_brain` route function directly**, so the
    context is exactly what a user's question gets (aggregate path, scope resolution,
    `hybrid_sources`, grounding). *Rejected:* re-assembling retrieval in the exam (would drift
    from the app); HTTP TestClient (rate limits/auth plumbing for no extra fidelity).
12. **The exam DB is rebuilt from fixtures at the start of every run** (a few minutes:
    migrations, ~2k chunks embedded locally). That is also the FR-38 rebuild proof.
    *Skipped:* template-database caching — add when rebuild time hurts.
13. **CUAD is used for review too** (8 lawyer-labelled presence categories), because FR-4(a)
    asks for review and extraction on the long CUAD contracts. **Q&A uses LegalBench-RAG's
    CUAD questions on those same 40 documents**, and **the portfolio reuses the 28 AEGIS
    review documents**, so each document is ingested once (D-11).
14. **Scan twins and stamp-only "as read today" documents are side lines, not headline** —
    they duplicate answer records; the effect of the scan path is shown next to the text
    score (FR-8, FR-23).
15. **Headline quality measures are the five main measures + quote validity + truncation
    rate**; coverage and consistency are report-only (D-12), cost and time too (D-10).
16. **Comparisons re-score stored predictions with the current scorer**, so a scorer fix can
    never make a change look better than its baseline.
17. **The exam's process turns off the per-org daily token cap and uses its own org in the
    exam DB.** The cap would 429 a full run mid-way, and the exam's ceiling is the spend
    control. Live usage's cap counter is never touched (different org id).
18. **The exam DB uses the shipped default prompts.** The live org has no prompt overrides
    today (checked: `a_i_prompt_version` is empty); every run records the prompt key/version/
    hash it actually used, and pairs must match.
19. **No Celery task, no integration module, no new MOCK flag.** The exam is a foreground,
    manual process (FR-31) using the existing Claude and Reducto clients.
20. **The exam key, owner and Reducto price live in `backend/eval/private/exam.env`
    (`ExamSettings`), not on the app's `Settings`.** Flagged deviation from the constitution's
    "new settings go on `Settings`": these are not app settings. On `Settings` they would load
    a second spending key into every API/worker/beat process, and the constitution's required
    production refusal would break the owner's local `make prod` (the base compose pins
    `ENVIRONMENT=production` and reads the same `backend/.env`). In a private file, no app
    process can ever see the exam key, and the file is git- and docker-ignored. *Rejected:*
    `Settings` fields + `validate_runtime_settings` entry; passing the key with
    `docker compose exec -e` (ends up in shell history).
21. **Batch delivery = capture → one batch per dependency phase → replay, through the same
    hook (FR-41).** The app's call paths are synchronous; rather than duplicating them for
    batches, the exam runs the app code twice: once to record each request at the Claude
    boundary, once to hand each request its batch answer. Requests are keyed by the sha256 of
    the exact payload, so the batch carries "the same model and the same request settings"
    and a stored answer can only ever go back to the identical request. Two phases follow the
    real dependencies (review, Q&A and portfolio read what metadata/clause labelling and
    brain ingestion stored). *Rejected:* a parallel batch implementation of each skill's
    prompt building (drifts from the app); one batch per item (no discount advantage, more
    waiting); a capture loop for multi-call units (no batched path makes two calls today —
    `ponytail:` comment).
22. **`Deferred` is a `BaseException` on purpose.** Capture must abort the app operation
    without the app treating it as a provider failure: its `except Exception` fallbacks would
    otherwise write fallback metadata / deterministic playbook findings into the exam DB and a
    failure row into the ledger. Only the exam's unit wrapper catches it.
23. **`a_i_call_log.batched` (NOT NULL, default false) in the same not-yet-applied revision.**
    A two-valued column needs no `coalesce` in every reader; the default makes it
    metadata-only on Postgres. It is set by the exam's stamp from the replayed
    `provider_request_id`, so no production code has to know about batches. *Rejected:*
    pricing by a `provider_request_id` prefix inside analytics (magic string in a production
    reader); a nullable flag (tri-state).
24. **Owner-document units, pre-fill and routing drafting stay synchronous.** FR-41 forbids
    batching real contracts; drafting is a one-time ≈$0.30 content step, not an exam call on
    public/constructed content.
25. **Portfolio keeps 8 questions** (not fewer): questions cost ≈$0.02 each in batch; the
    portfolio's cost is ingestion, already cut by reusing the AEGIS review documents.

**Risks:**

- **Control keys assume the shipped AEGIS templates comply with their playbooks** (the product
  claims they do). A rule the AI flags on every control in both baseline runs is listed per
  rule so the owner can see whether it is the model or a template/playbook disagreement
  (fixing either is out of scope).
- **Private-method coupling** (`_resolve_extracted_text`, `_persist_intake_records`,
  `_auto_rule_payloads`, the route function) — mitigated by the isolation integration test.
- **Replay mismatches:** a request whose inputs change between capture and replay is failed,
  not answered. The one known source is the Brain graph's "expiring / expired" facts, which
  use today's date: a Q&A or portfolio batch that straddles midnight UTC can fail a few
  questions (counted; could push a run past the 1% rule). README advice: start full runs
  early in the UTC day.
- **Batch waits:** usually about an hour per phase, up to 24 hours; the CLI process must stay
  up, or the owner uses `--resume` (results stay available from the provider for 29 days).
- **Capture leftovers:** aborted units leave `running` skill runs in the exam DB; harmless
  (the exam scores from the ledger) and wiped at the next run's rebuild.
- **Cost:** expected ≈ $9.75 per full run (≈ $18 at list), ≈ $1.08 per sample run; the
  planning figures come from live-ledger averages and are replaced by the previous run's
  actuals once one exists.
- **Client quotes in the live audit log:** the spec requires `eval.labels_loaded` to record
  each row's suggested quote; the live DB is the owner's development database.
- **Portfolio name detection** counts a contract mentioned in a negative sentence as
  included (ceiling of free-text scoring until the structured question tool exists); a
  `ponytail:` comment marks it.
- **Determinism of generated documents** (PyMuPDF rasterisation, LegalBench-RAG archive
  version): any difference changes the content fingerprint and forces a new baseline — loud,
  never silent.

**FR traceability:**

| FR | Design component |
|---|---|
| FR-1 | `runner` fixed ordered unit list from `load_content()`; content fingerprint in every run |
| FR-2 | `Dataset` registry (name, version, licence); `category`/`source` on every item; report `splits` |
| FR-3 | keys only from datasets, construction or loaded sheets; pre-fill model ≠ model under test; unconfirmed suggestions never enter `owner_facts.json` as scored values |
| FR-4 | `build`: CUAD 40 long (review + extraction), ContractNLI 10, LegalBench-RAG 100; report counts + past-25k counts |
| FR-5 | `build` constructed sets; `PlantedChange`; severity from library (direct / `severity_from`); controls |
| FR-6 | 24 AEGIS-template documents (+4 long) with Indian law, INR, Indian names and addresses |
| FR-7 | long documents with schedule plants; build asserts ≥10 past 25k; scorer split |
| FR-8 | PyMuPDF scan twins with stamp-only text layer; `twin_of`; side-by-side line |
| FR-9 | 40-contract portfolio (reuse + 12 extra), 8 questions, portfolio user scope, found/missed/wrong |
| FR-10 | coverage per call (recomputed context / excerpt chars) + average |
| FR-11 | `stop_reason == "max_tokens"` per call type |
| FR-12 | word-for-word quote check, invalid quotes listed (private), ≥85 alignment second number |
| FR-13 | `consistency` in `score_pair` (report-only, D-12) |
| FR-14 | ledger tokens priced with `token_cost` (batch flag); latency (sync) and batch turnaround (batch); per call type and total |
| FR-15 | `OWNER_FILES` + `owner_file_path` (refuse before access, never list) |
| FR-16 | `prefill` with `complete_json` on claude-sonnet-5-5 (sync); refusal when equal to model under test; notice period per D-14 |
| FR-17 | `verify_quote` before the sheet; "no supporting sentence found"; `correct` not allowed |
| FR-18 | facts sheet format (question, suggestion, sentence, where, mark, correction) |
| FR-19 | all-or-nothing loader; unmarked excluded + counted; prefill-changed rate |
| FR-20 | file sha + frozen text sha per key; out-of-date exclusion; one-time `ocr` with go-ahead (price or credits); OCR cache reused |
| FR-21 | `draft-routing` (claude-sonnet-5-5, ≥1 per workflow), snapshot + fingerprint, routing sheet, coverage counts, out-of-date flag |
| FR-22 | review P/R/F1 + missed high/critical by name |
| FR-23 | per-field scoring, parties as list, normalisation, before/past 25k, `asread` line |
| FR-24 | `ask_contract_brain` over the exam DB; `search_found` vs `answer_cited`, split by 25k |
| FR-25 | `triage()` on transient requests against the workflow snapshot; owner picks |
| FR-26 | report measure registry: n + one-line explanation for every listed measure |
| FR-27 | `baseline set` + `check_pair` (same code/models/prompts/content/delivery, full, complete, failed-call rate ≤ 1%); averages and bands; `--replace`; history in audit |
| FR-28 | `compare` + `verdict` (main measure, other headline quality measures, truncation reversed, coverage/consistency/cost/time report-only, target recorded at start, content and delivery must match) |
| FR-29 | `results/superseded/` labelled; three defects removed (real search context, party lists + normalised keys, owner picks on today's workflows) |
| FR-30 | `sample.json`; mode in record and report; sample label |
| FR-31 | owner gate; interactive go-ahead per invocation; OCR shown separately; no CI/schedule |
| FR-32 | ceiling at go-ahead; `exam_meter` worst-case admission for sync calls and batch requests; stop, `incomplete`, spend reported; right-sized ≈ $9.75 full run |
| FR-33 | actual vs estimate (and list price) per call type and total in summary, report and `eval.run_finished` |
| FR-34 | `ExamSettings` key (private file) swapped in + refusal when unset or equal to the real key; `eval_run_id`; mirror; analytics `traffic`; AI usage toggle |
| FR-35 | `isolate()` provider/telemetry/mock settings; `eval.real_contract_sent` destinations; owner units never batched (same provider either way) |
| FR-36 | `.gitignore` + `.dockerignore`; private folder; numbers-only public reports + guard |
| FR-37 | default engine = exam DB; contract-scoped Q&A; portfolio user scope |
| FR-38 | exam DB rebuild per run; no dispatch/lifecycle/workflow/notify calls; Celery `memory://`; mail mocks; live writes = ledger copies + audit |
| FR-39 | `eval.spend_approved` + `eval.run_finished` (immutable) + results sha; `eval.run_resumed` |
| FR-40 | ledger-based failure detection; unanswered/expired batch requests = failed calls; failed items listed, not scored; `contains_errors` / `incomplete`; crash reconciliation |
| FR-41 | `--delivery batch` default: capture → Message Batch per phase → replay; owner units sync; 24-hour note in the estimate; `delivery` in run record, report and pair/compare checks; `batched` column priced ×0.5 |

### Open questions (for the owner)

None open. Resolved 2026-10-06:

1. **Ceiling semantics with batches — HARD LIMIT (owner decision).** Spend never exceeds the
   ceiling: every call and batch request is admitted at its worst case (answer at
   `max_tokens`). The estimate output must say so plainly: "a full run is expected to cost
   ≈ $9.75; to guarantee it completes, set a ceiling of about $25 — you are charged only
   for what is used".
