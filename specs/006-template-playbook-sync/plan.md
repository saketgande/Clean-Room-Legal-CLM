# Implementation Plan: Template and Playbook Sync

Feature ID: 006-template-playbook-sync
Spec: ./spec.md (must be APPROVED)
Created: 2026-10-15
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

The feature extends one existing domain module, `backend/app/drafting_templates/`, and touches
four others at known seams. It mirrors the module's own shape (service + routes) and adds
`schemas.py` the way `app/trademarks/` and `app/contracts/` have one. Background checks follow
the `issue_fix_drafting` job pattern (`app/contract_files/issue_fixes.py`): create_job, commit,
dispatch, commit, one handler in the `_run_ai_job` chain. The frontend stays the one page,
`frontend/src/app/(app)/templates/`. It is split into colocated `_*.tsx` files and keeps its
scoped `.tpl` CSS, which uses the theme variables. Controls come from `@/components/ui`.

```
 Templates page ──HTTP──▶ drafting_templates/routes.py ──▶ service.py   (types, wording, versions)
                                                    ├──▶ checks.py    (queue, status, job handler)
                                                    ├──▶ uploads.py   (P2: read/OCR/marks/confirm)
                                                    └──▶ docx_fields.py (P2: field codes inside .docx)
 save / reset / restore / upload / create / reactivate / change playbook ──▶ checks.request_check ──▶ job "template_check"
 playbooks/routes.py publish + seed-library ──▶ checks.on_playbooks_published ──▶ request_check
 jobs/tasks.py _run_ai_job ──▶ checks.run_check_job ──▶ PlaybooksService.review_text (exists), on the template's PINNED playbook
 intake/drafting.py draft_contract_for_request ──▶ checks.template_status (FR-46/47 record + notice)
                                              ├──▶ contract.review_playbook_id = the template's pin (D-11)
                                              └──▶ P3: docx_fields.fill ──▶ create_contract_from_upload
 auto_review_contract / contracts/risk.py _review_runs ──▶ PlaybooksService.playbook_for_contract(contract)
        = contract.review_playbook_id if set (drafted from a template), else today's name pick (uploads)
```

What is new and what is reused:

- **Reused unchanged:** `PlaybooksService.review_text` (the check is the playbook's own review
  over `text_only_context`), `pick_playbook_for_contract`, `select_run_version`,
  `rules_in_review_order`, `is_document_wording`, `write_audit_log` / `write_timeline_event`,
  `create_job` / `dispatch_job`, `storage_service`, `StorageObject`, `validate_upload_mime`,
  `_read_upload_with_limit`, `_scan_for_malware`, the Documents reader (`_resolve_extracted_text`),
  `reducto_client`, `_build_plain_docx`, `template_values` / `render_document`, the cost cap in the
  Claude client. Two installed libraries do the Word work: python-docx 1.2.0 and lxml 6.1.1 on the
  backend, docx-preview 0.4.0 on the frontend (installed, currently unused).
- **New tables (both Phase 1):** `drafting_template_check` and `drafting_template_type`. The type table
  carries the pinned `playbook_id` (D-11). One column is added to `contract`: `review_playbook_id`, the
  pin a drafted contract carries. Columns are added to `drafting_template_version` in both phases.
- **The reviewing playbook is pinned per template (D-11, user, 2026-10-15).** A template's checks, and
  the automatic review and risk band of every contract drafted from it, use the playbook pinned on the
  template. A later, better-named playbook never takes over. Uploaded contracts keep today's name match,
  unchanged.
- **Customer requests draft automatically (D-12, user, 2026-10-15).** Phase 2 adds "Prepare draft" to
  both Customer starter workflows.
- **No new dependency, no new setting, no new permission, no new integration.**

### Delivery in three phases (each shippable and verifiable alone)

| Phase | Delivers | Migration | FR count |
|---|---|---|---|
| **1. Sync status** | Stored checks. The 8 statuses plus "results vary". Automatic re-check on save, reset, load-back, playbook change and playbook publish. Single and Check all with a cost confirm. Findings with jump-to and one-click apply. Optimistic save conflicts. The redesigned page. The draft records its template version and status, with the notice. **The playbook is pinned per template and carried by every draft (FR-51, D-11), and can be changed.** | `0074_template_sync` | 45 |
| **2. Upload + new types** | Word and PDF upload. Scanned PDF with paid OCR and confirmation. Marking fill-in fields, including across Word runs. Preview. Download as Word with visible codes (round trip). Word-based templates are read-only on the page. New types with a chosen (pinned) playbook. Retire and reactivate. Customer Agreement drafting, **including the Customer workflows' draft step (D-12)**. | `0075_template_uploads` | 26 |
| **3. Word-format drafts** | A draft from a Word-based template is a filled `.docx` that keeps its formatting and goes through `create_contract_from_upload`. The check of a Word-based template reads the Documents reader's text of the filled file. | none | 4 |

Phase 2 drafts from a Word-based template the way Phase 1 does: plain text from its `body`. The
check uses the same text, so check and draft agree in every phase. Phase 3 switches both to the
filled `.docx` together.

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Phase | Files in this feature |
|---|---|---|
| db-engineer | 1 | `backend/app/drafting_templates/models.py` (add `DraftingTemplateCheck` and `DraftingTemplateType` with `playbook_id`; 2 columns on `DraftingTemplateVersion`), `backend/app/contracts/models.py` (add `Contract.review_playbook_id`), `backend/app/models.py` (import + `__all__` entries `DraftingTemplateCheck`, `DraftingTemplateType`), `backend/alembic/versions/0074_template_sync.py` |
| db-engineer | 2 | `backend/app/drafting_templates/models.py` (7 columns on `DraftingTemplateVersion`), `backend/alembic/versions/0075_template_uploads.py` (schema plus the D-13 Customer-workflow data step). Its test, `backend/tests/test_migration_0075_customer_draft_step.py`, is owned by backend-dev (`backend/tests/**`) and written against the frozen literals and functions named in Database schema |
| backend-dev | 1 | `backend/app/drafting_templates/schemas.py` (new), `backend/app/drafting_templates/service.py` (incl. pin: `reviewing_playbook`, `ensure_pinned`, `set_playbook`), `backend/app/drafting_templates/checks.py` (new), `backend/app/drafting_templates/routes.py`, `backend/app/jobs/tasks.py` (one `elif` branch), `backend/app/playbooks/routes.py` (publish + seed-library hooks only), `backend/app/playbooks/service.py` (new `playbook_for_contract`; `auto_review_contract` calls it), `backend/app/contracts/risk.py` (`_review_runs` calls `playbook_for_contract`), `backend/app/intake/drafting.py` (template record + notice + `review_playbook_id` on the draft; `_FORM_DOC`/`_KIND_DOC` derived from the catalog), `backend/app/intake/routes.py` (draft-contract response), `backend/app/intake/schemas.py` (`DraftContractResponse`). Tests: `backend/tests/test_drafting_templates.py` (update), `backend/tests/test_template_check.py` (update: sync endpoint becomes a job), `backend/tests/test_template_status.py` (new), `backend/tests/test_template_check_jobs.py` (new), `backend/tests/test_template_drafting_record.py` (new), `backend/tests/test_playbook_pin.py` (new) |
| backend-dev | 2 | `backend/app/drafting_templates/service.py` (new types, retire/reactivate, file refs on versions, downloads), `backend/app/drafting_templates/uploads.py` (new), `backend/app/drafting_templates/docx_fields.py` (new), `backend/app/drafting_templates/schemas.py`, `backend/app/drafting_templates/routes.py`, `backend/app/drafting_templates/checks.py` (skip retired; docx wording hash), `backend/app/contract_files/service.py` (`_sniff_mime_type`: password-protected `.docx` message only), `backend/app/intake/drafting.py` (`resolve_doc_type(request, db=None)`), `backend/app/intake/service.py` (`_draftable(db, r)`), `backend/app/workflows/service.py` (pass `db=` at the 2 call sites, lines ~408 and ~1171; the `clm_draft` "form request with no active template" skip rule), `backend/app/workflows/contract_library.py` (D-12: Prepare draft on both Customer workflows). Tests: `backend/tests/test_template_types.py` (new), `backend/tests/test_template_uploads.py` (new), `backend/tests/test_docx_fields.py` (new), `backend/tests/test_workflow_engine_consistency.py` (the `resolve_doc_type` monkeypatch lambda must accept `db=`; the skip-rule case), `backend/tests/test_contract_workflow_library.py` (Customer workflows draft) |
| backend-dev | 3 | `backend/app/intake/drafting.py` (Word draft path), `backend/app/drafting_templates/checks.py` (Word check text). Tests: `backend/tests/test_word_template_drafts.py` (new) |
| frontend-dev | 1 | `frontend/src/lib/types.ts` (replace the drafting-template block; add `DraftContractResult`), `frontend/src/lib/endpoints.ts` (`draftingTemplatesApi` rewrite; `intakeApi.draftContract` return type), `frontend/src/lib/templates.ts` (new, pure helpers), `frontend/src/lib/templates.test.ts` (new), `frontend/src/app/(app)/templates/page.tsx`, `_template-list.tsx` (new), `_template-detail.tsx` (new), `_wording-editor.tsx` (new), `_check-panel.tsx` (rewrite), `_version-history.tsx` (new), `_check-all.tsx` (new), `_leave-guard.ts` (new), `page.test.tsx` (rewrite), `frontend/src/app/(app)/intake/_request-overview.tsx` (draft notice), `frontend/src/app/(app)/intake/_request-overview.test.tsx` (one case) |
| frontend-dev | 2 | `frontend/src/lib/types.ts`, `frontend/src/lib/endpoints.ts` (Phase 2 functions), `frontend/src/lib/api.ts` (`apiBytes` accepts `{ form }` and then POSTs), `frontend/src/lib/templates.ts` + `.test.ts` (paragraph and mark helpers), `frontend/src/app/(app)/templates/_upload-flow.tsx` (new), `_field-marker.tsx` (new), `_scan-review.tsx` (new), `_new-type-modal.tsx` (new), `_docx-preview.tsx` (new), edits to `page.tsx`, `_template-list.tsx`, `_template-detail.tsx`, `_wording-editor.tsx`, `_version-history.tsx`. Tests: `_upload-flow.test.tsx` (new), `_new-type-modal.test.tsx` (new), `page.test.tsx` (extend) |
| frontend-dev | 3 | none |
| qa-engineer | all | `specs/006-template-playbook-sync/verification.md` |

Shared-hotspot note. These files serialize across tasks:
- `backend/app/models.py`: P1-db only.
- `backend/app/contracts/models.py`: P1-db only. The busiest table: one nullable column.
- `backend/app/playbooks/service.py` and `backend/app/contracts/risk.py`: P1 backend only. These are the
  two choke points every review and risk-band consumer goes through, so keep them in one task.
- `backend/app/workflows/service.py`: P2 backend only.
- `backend/app/jobs/tasks.py`: P1 backend only.
- `backend/app/drafting_templates/service.py`, `checks.py` and `routes.py`: P1 backend and P2 backend. In
  P1, `service.py` and `checks.py` import each other at function level, so put them in one task or freeze
  the signatures below.
- `backend/app/intake/drafting.py`: P1, P2 and P3 backend.
- `frontend/src/lib/types.ts` and `endpoints.ts`: P1 FE-core and P2 FE.
- `frontend/src/lib/api.ts`: P2 FE.
- `frontend/src/components/ui.tsx`: no task touches it.
- The two migrations never run in parallel. `0075_template_uploads` revises `0074_template_sync`.

## Interface freeze (contract-first — fixed before implementation starts)

All paths are under the API prefix `/api/v1`. Client paths in `endpoints.ts` omit it. Every route
takes `user=Depends(require_permission(...))` and reads `user.org_id`. Every query filters
`org_id == user.org_id`. Templates are organisation paper, not contract-adjacent, so
`accessible_contract_filter` does not apply (A-9). Routes stay thin: one call into the service.

### API contract

Phase 1 (P1). The old synchronous `POST /drafting-templates/{key}/check` is **removed**.
`POST /{key}/checks` (async, recorded) replaces it.

| Ph | Method | Path | Permission | Request body | Response | Errors |
|---|---|---|---|---|---|---|
| 1 | GET | /api/v1/drafting-templates?include_retired=false | `playbook:read` | — | 200 `DraftingTemplateSummary[]` (P1 ignores the flag: all 7 shipped are active) | — |
| 1 | GET | /api/v1/drafting-templates/{key} | `playbook:read` | — | 200 `DraftingTemplate` | 404 |
| 1 | GET | /api/v1/drafting-templates/{key}/versions | `playbook:read` | — | 200 `DraftingTemplateVersion[]` newest first; shipped types end with a synthetic version 0 | 404 |
| 1 | POST | /api/v1/drafting-templates/{key}/preview | `playbook:read` | `{"body": str≤200000}` | 200 `{"text": str}` | 422 |
| 1 | PUT | /api/v1/drafting-templates/{key} | `playbook:update` | `TemplateSaveRequest` | 200 `TemplateMutationResult` | 404, 409 conflict, 409 Word-based (P2+), 409 retired (P2+), 422 unfillable, 422 parties, 422 finding |
| 1 | POST | /api/v1/drafting-templates/{key}/reset | `playbook:update` | `TemplateVersionAction` | 200 `TemplateMutationResult` | 404, 409 conflict, 409 not shipped (P2+), 409 retired (P2+) |
| 1 | POST | /api/v1/drafting-templates/{key}/versions/{version}/restore | `playbook:update` | `TemplateVersionAction` | 200 `TemplateMutationResult` | 404 template/version, 409 conflict, 409 retired (P2+) |
| 1 | POST | /api/v1/drafting-templates/{key}/checks | `playbook:run` | `{"body"?: str≤200000}` (omitted = the saved wording) | **202** `TemplateCheck` (new, or the existing active check of the same saved wording) | 404, 409 no playbook, 409 queue full (unsaved only), 409 Word-based unsaved (P2+), 422 unfillable |
| 1 | GET | /api/v1/drafting-templates/{key}/checks?limit=50 | `playbook:read` | — | 200 `TemplateCheck[]` newest first (unsaved checks included, flagged) | 404 |
| 1 | GET | /api/v1/drafting-templates/{key}/checks/{check_id} | `playbook:read` | — | 200 `TemplateCheck` | 404 |
| 1 | POST | /api/v1/drafting-templates/check-all | `playbook:run` | `{}` | **202** `CheckAllResult` | — |
| 1 | PUT | /api/v1/drafting-templates/{key}/playbook | `playbook:update` | `TemplatePlaybookChange` | 200 `TemplateMutationResult` (`changed:false` when it is already the pin) | 404, 409 retired (P2+), 422 playbook |
| 1 | GET | /api/v1/drafting-templates/options | `playbook:read` | — | 200 `TemplateTypeOptions` (P1 uses `playbooks` for Change playbook; P2 adds the create form's use of `bindings`) | — |
| 1 | POST | /api/v1/intake/requests/{request_id}/draft-contract | unchanged (`_READ` + `_INTAKE_EDIT`) | — | 200 `DraftContractResult` = `RequestResponse` + `template_notice` | unchanged |
| 2 | POST | /api/v1/drafting-templates | `playbook:update` | multipart: `CreateTypeForm` | **201** `TemplateMutationResult` | 409 binding held, 409 name taken, 422 playbook, 422 binding, 422 name, 422 wording, 413/415/422 file, 422 scan unconfirmed |
| 2 | POST | /api/v1/drafting-templates/{key}/retire | `playbook:update` | `{"note"?: str≤300}` | 200 `DraftingTemplate` (no-op when already retired) | 404 |
| 2 | POST | /api/v1/drafting-templates/{key}/reactivate | `playbook:update` | `{"note"?: str≤300}` | 200 `TemplateMutationResult` (no-op, `check: null`, when already active) | 404, 409 binding held, 409 name taken |
| 2 | POST | /api/v1/drafting-templates/uploads/analyze | `playbook:update` | multipart: `file` | 200 `UploadAnalysis` (nothing stored) | 413, 415, 422 (empty, damaged, password, no text, revisions/comments, AV), 503 AV down |
| 2 | POST | /api/v1/drafting-templates/uploads/recognize | `playbook:update` | multipart: `file` (must be a scan) | 200 `RecognizedText` | 413/415/422 as analyze, 422 not a scan, 422 no text, 502 OCR failed, 503 OCR not set up |
| 2 | POST | /api/v1/drafting-templates/uploads/confirm-text | `playbook:update` | `ConfirmTextPayload` | 200 `TextConfirmation` | 422 empty text |
| 2 | POST | /api/v1/drafting-templates/uploads/cancel | `playbook:update` | `CancelUploadPayload` | 204 | — |
| 2 | POST | /api/v1/drafting-templates/uploads/preview | `playbook:update` | multipart: `file`, `marks`, `ocr_text?` | 200 `UploadPreview` | as analyze + 422 mark, 422 not a scan |
| 2 | POST | /api/v1/drafting-templates/uploads/preview-docx | `playbook:update` | multipart: `file` (.docx), `marks` | 200 `application/vnd.openxmlformats-officedocument.wordprocessingml.document` bytes (filled with sample values) | as preview, 422 not Word |
| 2 | POST | /api/v1/drafting-templates/{key}/upload | `playbook:update` | multipart: `UploadForm` | 200 `TemplateMutationResult` (`changed:false` when identical, FR-37) | 404, 409 conflict, 409 retired, 413/415/422 file, 422 wording, 422 parties, 422 scan unconfirmed |
| 2 | GET | /api/v1/drafting-templates/{key}/download | `playbook:read` | — | 200 `.docx` attachment of the current wording with visible `{code}`s | 404 |
| 2 | GET | /api/v1/drafting-templates/{key}/files/{file_id} | `playbook:read` | — | 200 stored file bytes (attachment) | 404 (not referenced by a version of this template in this org) |
| 2 | GET | /api/v1/drafting-templates/{key}/preview-docx | `playbook:read` | — | 200 `.docx` bytes: the current Word file filled with sample values | 404, 409 not Word-based |

Route registration order matters (FastAPI matches in order). Register the static
paths `/options`, `/check-all` and `/uploads/*` **before** `/{key}`. Reserved keys that a new type can
never take: `options`, `check-all`, `uploads`.

No contract endpoint changes. A drafted contract's pin is internal (`contract.review_playbook_id`). It
shows through the existing risk summary's `playbook_id` and through the contract's history
(FR-46 timeline).

Every download sets `Content-Disposition: attachment; filename="…"` and
`X-Content-Type-Options: nosniff`.

#### Full request/response JSON shapes

These are the Pydantic models in `drafting_templates/schemas.py`. The TS interfaces below carry
identical names and fields, and the Literal unions match exactly.

```json
// PUT /drafting-templates/{key}  — TemplateSaveRequest
{ "body": "…≤200000 chars, braces as str.format…", "note": "≤300 or null",
  "expected_version": 3,                     // the version the editor loaded (0 = never saved); required
  "applied_findings": [ { "check_id": "uuid", "finding_index": 0 } ],   // default []
  "acknowledge_missing_parties": false }     // default false; must be true when no {company} or no {counterparty}

// POST …/reset  and  POST …/versions/{version}/restore — TemplateVersionAction
{ "note": "≤300 or null", "expected_version": 3 }

// TemplateMutationResult (save, reset, restore, upload, create, reactivate)
{ "template": { /* DraftingTemplate */ }, "changed": true, "check": { /* TemplateCheckSummary */ } | null }
// changed=false: no version was made, no check started (FR-37 and the edge cases). check=null also when no playbook covers it.

// POST …/checks — request
{ "body": "unsaved editor wording" }      // or {} for the saved wording
// 202 response: TemplateCheck (state "queued" or "running")

// PUT /drafting-templates/{key}/playbook — TemplatePlaybookChange (pin another playbook, D-11)
{ "playbook_id": "uuid of a published playbook of this org", "note": "≤300 or null" }
// 200 TemplateMutationResult. A check against the new pin starts (trigger "playbook_changed").
// Contracts already drafted keep the playbook they were drafted with (FR-45).

// POST /drafting-templates/check-all — 202 CheckAllResult
{ "started": [ { "key": "msa", "check_id": "uuid" } ],
  "skipped": [ { "key": "nda", "reason": "checking" }, { "key": "dpa", "reason": "no_playbook" } ] }

// TemplateCheck
{ "id": "uuid", "template_key": "msa", "state": "completed", "trigger": "saved",
  "trigger_detail": { "playbook_id": "…", "playbook_version_id": "…", "version_number": 3 } | null,
  "unsaved": false, "template_version": 4, "wording_sha256": "hex64",
  "requested_by": "Priya Shah" | null, "on_behalf": true,
  "playbook_id": "…" | null, "playbook_name": "Master Services Agreement Playbook" | null,
  "playbook_version_id": "…" | null, "playbook_version_number": 3 | null, "rule_count": 9 | null,
  "finding_count": 2 | null, "highest_severity": "high" | null,
  "findings": [ {
      "index": 0, "rule_index": 4, "clause_type": "service_levels", "severity": "medium",
      "issue": "…", "quote": "…filled text…", "quote_template": "…same with {codes}…" | null,
      "suggested_fix": "…", "suggested_fix_template": "…with {codes}…" | null,
      "suggestion_is_wording": true, "approval_required": false } ],
  "summary": "…" | null, "text_was_truncated": false, "text_length": 25270, "read_length": 25270,
  "error_code": null, "error_message": null,
  "created_at": "ISO", "started_at": "ISO" | null, "finished_at": "ISO" | null }
// TemplateCheckSummary = the same object without findings, summary, trigger_detail, rule_count,
// wording_sha256, text_length, read_length, playbook_id, playbook_version_id and template_key.

// DraftingTemplateSummary (list item)
{ "key": "nda", "name": "Non-Disclosure Agreement", "label": "Mutual NDA", "contract_type": "NDA",
  "binding": { "form_key": "new_agreement", "form_name": "New agreement", "agreement_kind": "NDA",
               "label": "New agreement · NDA" },
  "playbook": { "id": "…", "name": "NDA Playbook", "version_id": "…" | null, "version_number": 2 | null,
                "pinned": true,       // false = not pinned yet: today's name match, locked at the first check or draft
                "available": true },  // false = pinned playbook deleted or without a published version
              | null,                 // null = not pinned and no published playbook matches by name
  "shipped": true, "active": true, "source": "default", "version": 0,
  "original_filename": null, "updated_at": "ISO" | null, "updated_by": "name" | null,
  "editable": true, "status": { /* TemplateStatus */ } }

// TemplateStatus
{ "state": "has_findings", "label": "Has findings", "reason": "2 findings, highest High.",
  "finding_count": 2, "highest_severity": "high", "changed": null, "error_code": null,
  "results_vary": false,
  "decided_by": { /* TemplateCheckSummary */ } | null,
  "last_completed": { /* TemplateCheckSummary */ } | null,
  "active_check": { /* TemplateCheckSummary */ } | null }

// DraftingTemplate (detail) = DraftingTemplateSummary + 
{ "body": "…", "default_body": "…" | null,
  "placeholders": [ { "name": "counterparty", "label": "The other party", "about": "…", "sample": "Globex Corporation" } ],
  "fields": [ { "name": "counterparty", "label": "The other party", "place": "footer", "count": 1 } ],
  "original_file": { "id": "…", "filename": "MSA.docx", "mime_type": "…", "size_bytes": 81234 } | null }

// DraftingTemplateVersion
{ "version": 4, "note": "…" | null, "source": "text", "is_default": false, "body": "…",
  "created_at": "ISO" | null, "created_by": "name" | null,          // null for synthetic version 0
  "original_file": { /* TemplateFileRef */ } | null,
  "applied_findings": [ { "check_id": "…", "finding_index": 0, "clause_type": "payment_terms", "severity": "high" } ],
  "restored_from_version": 2 | null,
  "ocr_confirmed_by": "name" | null, "ocr_confirmed_at": "ISO" | null,
  "checks": [ /* TemplateCheckSummary of this version, saved-wording only, newest first */ ] }

// POST /intake/requests/{id}/draft-contract — DraftContractResult
{ /* every RequestResponse field */, "template_notice": "This template does not currently pass its playbook: Has findings — 2 findings, highest High. The draft was created anyway." | null }

// ---- Phase 2 ----
// GET /drafting-templates/options — TemplateTypeOptions
{ "bindings": [ { "form_key": "new_agreement", "agreement_kind": "Selling to a customer",
                  "label": "New agreement · Selling to a customer", "contract_type": "Customer Agreement",
                  "held_by": null, "suggested_playbook": { "id": "…", "name": "Customer Agreement Playbook" } | null } ],
                  // suggested_playbook = today's name match, used only to preselect; the person's choice is pinned
  "playbooks": [ { "id": "…", "name": "Customer Agreement Playbook", "version_number": 1 } ] }   // published, this org

// POST /drafting-templates — CreateTypeForm (multipart/form-data fields)
name=Customer Agreement · playbook_id=… · form_key=new_agreement · agreement_kind=Selling to a customer
· note=… (opt) · acknowledge_missing_parties=true|false
· EITHER body=<text wording>  OR  file=<.docx|.pdf> + marks=<JSON TemplateMark[]> (+ for a scan: ocr_text=…, ocr_confirmation=<token>)

// POST /drafting-templates/{key}/upload — UploadForm (multipart/form-data fields)
file · marks=<JSON TemplateMark[], default "[]"> · note (opt) · expected_version=<int> · acknowledge_missing_parties
· ocr_text + ocr_confirmation (scan only; required for a scan, refused otherwise)

// TemplateMark
{ "paragraph": 12, "start": 40, "end": 58, "field": "counterparty" }   // span becomes {counterparty}
{ "paragraph": 3, "start": 0, "end": 13, "literal": true }             // span kept as ordinary text (braces doubled)

// UploadAnalysis
{ "kind": "docx" | "pdf" | "scan", "filename": "MSA.docx", "mime_type": "…", "size_bytes": 81234, "sha256": "hex64",
  "page_count": 3 | null,
  "paragraphs": [ { "id": 0, "place": "body", "fillable": true, "text": "This Agreement is made between …" } ],  // [] for a scan
  "fields": [ { "name": "counterparty", "known": true, "label": "The other party", "places": ["body","footer"], "count": 12 } ],
  "problems": [ { "kind": "unknown_field", "paragraph": 7, "start": 10, "end": 23, "text": "{client_name}",
                  "place": "body", "message": "Unknown placeholder {client_name}. …" } ],
  "ocr": { "provider": "Reducto", "pages": 3, "estimate": "3 pages of paid text recognition (priced per page)" } | null }

// RecognizedText
{ "filename": "…", "sha256": "hex64", "page_count": 3,
  "pages": [ { "page": 1, "text": "…" }, { "page": 3, "text": "…" } ], "empty_pages": [2], "text": "…" }

// ConfirmTextPayload → TextConfirmation
{ "filename": "…", "sha256": "<file sha256>", "text": "<the text as the person corrected it, ≤200000>" }
{ "confirmation": "<HS256 JWT>", "confirmed_at": "ISO" }

// CancelUploadPayload
{ "filename": "…", "sha256": "…", "after_ocr": true }

// UploadPreview
{ "text": "<filled with sample values>", "body": "<template wording with {codes}>",
  "fields": [ /* TemplateFieldUse */ ], "problems": [ /* TemplateWordingProblem */ ],
  "missing_parties": ["company"] }
```

#### Error messages (exact; tests pin them)

| Code | Message |
|---|---|
| 404 template | `No such template` (existing) |
| 404 version / check / file | `No such version of this template.` / `No such check.` / `No such file for this template.` |
| 409 conflict (FR-59) | `{name} saved version {n} of this template at {YYYY-MM-DD HH:MM} UTC after you opened it. Your text is still in the editor: compare it with theirs, then save again.` |
| 409 Word-based (FR-40) | `This template was made from a Word file, so its wording changes only by uploading a replacement: download it, edit it in Word, then upload it.` |
| 409 retired | `This template type is retired. Reactivate it before changing it.` |
| 409 no playbook | `No published playbook covers {name}, so there is nothing to check it against.` (existing) |
| 409 queue full | `A check of this template is already waiting to start. Try again once it has started.` |
| 409 not shipped | `Only a shipped template has shipped wording to go back to.` |
| 409 binding held (FR-49/55) | `{binding label} already drafts from {holder name}. Retire that template first, or choose another kind of agreement.` |
| 409 name taken (FR-50) | `An active template type is already called {name}.` |
| 422 unfillable (FR-34) | `Unknown placeholder {client_name} (line 12). Use one from the list, or keep it as ordinary text by writing it as {{client_name}}.` · `The braces don't pair up (line 4). Write a literal brace as {{ or }}.` · `The template is empty.` (A Word place uses `(in the footer)` instead of `(line N)`. A code in a text box, footnote or endnote reads `Text in a {place} can't be filled; keep it as ordinary text.`) |
| 422 parties (FR-35) | `This wording has no field for {our entity / the other party / our entity or the other party}, so drafts will not name them. Confirm that this is intended to save it.` |
| 422 finding | `That suggestion is not from a check of this template.` |
| 422 playbook (FR-50/51/69) | `Choose one of this organisation's published playbooks.` (create, change playbook: the playbook must belong to this org, not be deleted, and have a published version; this is the only case in which FR-51's guarantee can't be given, so it is FR-51's refusal) |
| 409 no playbook (pinned one gone) | `{name} is reviewed by {playbook}, which {was deleted / has no published version}. Publish it, or choose another playbook for this template.` (manual check; the same sentence is the No playbook status reason) |
| 422 binding | `Choose a request form or kind of agreement that drafts a new contract.` |
| 422 name | `Give the template type a name (up to 120 characters).` |
| 415 type | `Only Word (.docx) and PDF files can be used as a template.` |
| 415 protected .docx | `This Word file is password-protected or saved in the old .doc format. Remove the password and save it as .docx, then upload it again.` (added inside the shared `_sniff_mime_type`, so contract uploads get the clearer reason too) |
| 413 / 415 mismatch / 422 empty / 422 AV / 503 AV | the existing `contract_files.service` messages, unchanged |
| 422 damaged / PDF password / no text | `This file is damaged and can't be read.` · `This PDF is password-protected. Remove the password and upload it again.` · `This Word file has no text in it.` |
| 422 revisions (FR-29) | `This Word file has tracked changes. Accept or reject every change (Review → Accept/Reject All), save, and upload it again.` and/or `This Word file has comments. Delete them (Review → Delete All Comments), save, and upload it again.` |
| 422 mark | `A field can only mark text within one paragraph, not across a tab or line break.` · `That mark points outside the text of paragraph {n}.` |
| 422 scan | `The recognised text has not been confirmed. Confirm it before saving the template.` · `Recognised text can only be given for a scanned PDF.` |
| 422 / 502 / 503 OCR (FR-73/74) | `Text recognition found no text in this file, so it can't be used as a template. Nothing was changed.` · `Text recognition failed, so the scan could not be read. Nothing was changed. You can try again as a new paid step.` · `Text recognition is not set up on this system, so the scan can't be read. Nothing was changed.` |
| 409 not Word | `This template has no Word file.` |

#### Audit actions written by each mutation (FR-70)

These go through `write_audit_log`, on the request's own session, and commit before any slow
work. For a system action, `actor_user_id=None` and `metadata={"actor":"system","on_behalf_of_user_id": <person>}`.

| Action | Written by | `after` payload |
|---|---|---|
| `drafting_template.saved` (exists) | PUT | key, version, note, source, wording_sha256, applied_findings |
| `drafting_template.reset` (exists) | reset | key, version, note |
| `drafting_template.restored` | restore | key, version, restored_from_version |
| `drafting_template.check_started` | `request_check` | check_id, key, template_version, trigger, unsaved, on_behalf |
| `drafting_template.check_finished` | worker | check_id, key, template_version, playbook_id, playbook_version_id, playbook_version_number, trigger, finding_count, text_was_truncated |
| `drafting_template.check_failed` | worker | check_id, key, template_version, playbook_version_id (if known), trigger, error_code, error_message |
| `drafting_template.check_superseded` | `request_check` | check_id, superseded_by_check_id |
| `drafting_template.check_all_started` | check-all | count, keys |
| `drafting_template.playbook_pinned` | `ensure_pinned` (first check, draft or matching publish of an unpinned template; system, on behalf of the person) | key, playbook_id, playbook_name, how: "matched by name at first use" |
| `drafting_template.playbook_changed` | PUT …/playbook (person) | before {playbook_id, playbook_name}, after {playbook_id, playbook_name, note} |
| `intake.contract_drafted` (exists) | drafting | + template_key, template_version, template_source, template_status, template_status_reason, review_playbook_id |
| `drafting_template.upload_refused` (P2) | uploads | filename, mime_type, reason (413/415/AV refusals) |
| `drafting_template.upload_confirmed` (P2) | upload / create | key, version, filename, size_bytes, mime_type, sha256, fields, source |
| `drafting_template.ocr_agreed` (P2) | recognize, before the call | filename, sha256, estimate |
| `drafting_template.ocr_failed` / `drafting_template.ocr_no_text` (P2) | recognize | filename, sha256, reason |
| `drafting_template.ocr_text_confirmed` (P2) | confirm-text | filename, file_sha256, text_sha256, confirmed_at |
| `drafting_template.upload_cancelled` (P2) | cancel (only when `after_ocr`) | filename, sha256 |
| `drafting_template.type_created` / `type_retired` / `type_reactivated` (P2) | type endpoints | key, name, playbook_id (created), form_key, agreement_kind |

Timeline (FR-46). On the drafted contract: `write_timeline_event(resource_type="contract",
event_type="contract.drafted_from_template", title="Drafted from the {name} template, version {v} —
{status label}", details={template_key, template_version, template_source, status, reason, notice,
review_playbook_id, review_playbook_name})`.
The existing intake-request timeline event `intake.contract_drafted` also gets `template_version`
and `template_status` in its details. Template actions themselves have no timeline: the page shows
history from versions and check records.

### Database schema

Column style follows `0068_drafting_templates`: String(36) ids, an `org_id` FK to `organization.id`,
actor FKs to `user.id`, and timestamptz `created_at`/`updated_at` with `server_default now()`. Models use
`TableNameMixin, IdMixin, OrgScopedMixin, ActorTrackedMixin, TimestampMixin, Base`. Status-like
strings are plain `String` (no DB CHECK or enum), matching `job_run.status`. The allowed values are
the Literal unions in `schemas.py`.

**Phase 1: `0074_template_sync`** (18 chars), down_revision `0073_merge_trademark_suite` (verified single head).
It creates `drafting_template_check` and `drafting_template_type`, adds the 2 version columns, and adds
`contract.review_playbook_id`.

| Table | Column | Type | Constraints |
|---|---|---|---|
| drafting_template_check | id | varchar(36) | PK (`new_uuid()`) |
| | org_id | varchar(36) | FK organization.id, NOT NULL, index `ix_drafting_template_check_org_id` |
| | template_key | varchar(40) | NOT NULL |
| | template_version | integer | NOT NULL (0 = shipped wording, never saved) |
| | wording_sha256 | varchar(64) | NOT NULL: sha256 of the wording checked (body text; P2+ Word file: canonical docx hash) |
| | unsaved | boolean | NOT NULL, server_default false |
| | unsaved_body | text | NULL, set only when `unsaved` |
| | trigger | varchar(30) | NOT NULL: saved, uploaded, restored, reset, type_created, type_reactivated, playbook_published, playbook_changed, manual, check_all, retry |
| | trigger_detail | json | NULL (playbook_published: `{playbook_id, playbook_version_id, version_number}`) |
| | status | varchar(20) | NOT NULL, server_default 'queued': queued, running, completed, failed, superseded |
| | playbook_id | varchar(36) | FK playbook.id, NULL, index |
| | playbook_version_id | varchar(36) | FK playbook_version.id, NULL, index |
| | playbook_name | varchar(255) | NULL (snapshot for display, survives rename or delete) |
| | playbook_version_number | integer | NULL |
| | rule_count | integer | NULL |
| | findings | json | NULL (list, shape = `TemplateCheckFinding`) |
| | summary | text | NULL |
| | text_was_truncated | boolean | NULL |
| | text_length | integer | NULL |
| | read_length | integer | NULL |
| | error_code | varchar(40) | NULL: ai_unavailable, cost_cap, no_rules, no_playbook, unfillable, wording_changed, interrupted |
| | error_message | text | NULL (plain words) |
| | started_at / finished_at | timestamptz | NULL |
| | created_by_user_id | varchar(36) | FK user.id, NULL: the person the check is for (person or on whose behalf) |
| | updated_by_user_id | varchar(36) | FK user.id, NULL |
| | created_at / updated_at | timestamptz | NOT NULL, server_default now() |
| | *index* | (org_id, template_key, created_at) | `ix_drafting_template_check_org_key_created` |
| | *index* | status | `ix_drafting_template_check_status` |
| | *partial unique* | (org_id, template_key) WHERE status = 'running' | `uq_drafting_template_check_one_running` (FR-15) |
| | *partial unique* | (org_id, template_key) WHERE status = 'queued' | `uq_drafting_template_check_one_queued` (FR-15) |
| drafting_template_version | applied_findings | json | NULL: `[{check_id, finding_index, clause_type, severity}]` (FR-25) |
| | restored_from_version | integer | NULL (FR-58) |
| contract | review_playbook_id | varchar(36) | FK playbook.id, NULL, index `ix_contract_review_playbook_id`. The pinned playbook of the template the contract was drafted from (D-11). NULL = not drafted from a pinned template (uploads, attachments, custom drafts, drafts made before this feature), which keeps today's name match. Set once at drafting and never changed by this feature (FR-45). |
| drafting_template_type | id | varchar(36) | PK |
| | org_id | varchar(36) | FK organization.id, NOT NULL, index |
| | key | varchar(40) | NOT NULL; `uq_drafting_template_type_key` UNIQUE (org_id, key) |
| | name | varchar(120) | NOT NULL |
| | label | varchar(120) | NOT NULL (draft title prefix; = name for new types) |
| | form_key | varchar(40) | NOT NULL (`new_agreement`, `sow`, `dpa`) |
| | agreement_kind | varchar(80) | NULL (a New-agreement kind; NULL for sow/dpa) |
| | shipped | boolean | NOT NULL, server_default false |
| | status | varchar(20) | NOT NULL, server_default 'active' (`active`, `retired`; P1 only ever writes `active`) |
| | playbook_id | varchar(36) | FK playbook.id, NULL, index `ix_drafting_template_type_playbook_id`. The pinned reviewing playbook (D-11). NULL = not pinned yet: shipped types until their first check, draft or matching publish |
| | created_by_user_id / updated_by_user_id | varchar(36) | FK user.id, NULL |
| | created_at / updated_at | timestamptz | NOT NULL |
| | *partial unique* | (org_id, form_key, coalesce(agreement_kind, '')) WHERE status = 'active' | `uq_drafting_template_type_binding` (FR-49) |
| | *partial unique* | (org_id, lower(name)) WHERE status = 'active' | `uq_drafting_template_type_name` (FR-50) |

Downgrade: drop `contract.review_playbook_id` and its index. Drop `drafting_template_type` with its
indexes. Drop the check table's partial indexes, other indexes and the table. Then drop the two
version columns.

The whole type table lands in Phase 1, because the pin needs a per-org row for shipped types. Phase 1
writes rows only for pins. Phase 2 adds new types and `retired`, with no further schema change to
the table.

**Phase 2: `0075_template_uploads`** (21 chars), down_revision `0074_template_sync`. It only adds columns
to versions.

| Table | Column | Type | Constraints |
|---|---|---|---|
| drafting_template_version | source | varchar(10) | NOT NULL, server_default 'text'; backfill `UPDATE … SET source='default' WHERE body IS NULL`. Values: default, text, docx, pdf, scan |
| | original_file_id | varchar(36) | FK storage_object.id, NULL, index (the uploaded file, FR-38/75) |
| | docx_file_id | varchar(36) | FK storage_object.id, NULL, index (the Word template with `{code}`s; Word-based only) |
| | content_sha256 | varchar(64) | NULL (canonical hash of `docx_file_id`'s bytes; Word-based only) |
| | fields | json | NULL (Word-based: `[{name, place, count}]` incl. headers and footers; NULL = derive from body) |
| | ocr_confirmed_by_user_id | varchar(36) | FK user.id, NULL (FR-75) |
| | ocr_confirmed_at | timestamptz | NULL (FR-75) |

**Data step in `0075` (D-13).** It runs online only and is skipped in `--sql` offline mode, as `0068` does.
It does not import `contract_library.py`: it carries frozen literals.

- **Frozen literals.**
  - `OLD_STEPS[name]` for "Customer — standard" and "Customer — strategic deal": today's library steps
    after `stages.infer`, normalized.
  - `NEW_STEP`, normalized: `{type: "clm_draft", name: "Prepare draft", stage: "drafting",
    cond: {field: "paper", op: "eq", value: "Our template"}, config: {sla_hours: 24, outcomes: ["complete"],
    instructions: "Draft from the Customer Agreement template.", assign_by: "Auto — least-loaded in team"}}`.
  - **Normalized** means the step `id` is dropped and `config.team` / `config.team_id` are dropped. Those are
    the only per-org parts seeding writes (`seed_builtin_flows` gives fresh step ids and resolves team keys
    to org team ids). Everything else is compared as JSON.
- **upgrade.** For each `workflow` row with `is_builtin = true`, `name` in the two names and `version = 1`,
  if its normalized `steps` **equal** `OLD_STEPS[name]`:
  - set `steps = [NEW_STEP + {id: new uuid4, config.team_id: that org's legal_counsel team id
    (from intake_team where org_id = row.org_id and key = 'legal_counsel'; omitted if none, as seeding
    does)}] + the row's existing steps`, unchanged with their ids;
  - set `version = 2`, the same +1 as `update_flow`; `updated_at = now()`; `updated_by_user_id` stays
    NULL (the release, not a person).
  Each UPDATE is keyed by `id` and `org_id`. A second run finds no match (version 2), so it is idempotent.
  An edited workflow (version ≥ 2, or different steps) is never touched.
- **downgrade.** For each such row with `version = 2` whose normalized steps equal
  `[NEW_STEP] + OLD_STEPS[name]`, drop the first step and set `version = 1`, which restores the exact
  pre-upgrade row (a rollback). Anything else is left alone, including a workflow edited after the
  upgrade. Then drop the version columns.
- The logic lives in two module-level functions, `customer_workflows_upgrade(bind)` and
  `customer_workflows_downgrade(bind)`. `upgrade()`/`downgrade()` call them, and they return the ids they
  changed, which are logged through `logging.getLogger("alembic")`.
- **Not in the audit log, and why that is acceptable.** `audit_log` is a hash chain:
  `write_audit_log` computes `previous_hash`/`row_hash` in Python under the chain's advisory lock, and
  `verify_audit_integrity` checks the chain nightly. A raw SQL insert from a migration would break the
  chain, and importing the app's audit writer into a migration is the app-code coupling the other
  revisions avoid. The change is instead recorded by:
  - the version bump to 2 with `updated_by_user_id` NULL, shown in the builder as the system's change;
  - `alembic_version` = `0075_template_uploads`;
  - the migration log line listing org and workflow ids.
  This follows `0068_drafting_templates`, which inserted screen and menu rows unaudited at deploy. It is a
  release-time configuration change, not a person's action.

Downgrade: drop the version columns (after the data step's downgrade).

No row is seeded for the 7 shipped types: their facts stay in code (`TEMPLATES`). A
`drafting_template_type` row exists for every new type. For a shipped key, a row appears the first
time it is pinned (P1) or retired (P2). That row copies the shipped name, label and binding, so the
partial unique indexes see it. See the decisions. `drafting_template_version` keeps joining by
`(org_id, key)` (no FK). Existing rows, the 7 keys and their defaults are untouched.

No pgvector. Registry: `app/models.py` imports `DraftingTemplateCheck` and `DraftingTemplateType` (both
P1) and lists both in `__all__`.

### Frontend TypeScript models (added to src/lib/types.ts)

These replace the current block `DraftingTemplateSummary … TemplateCheckResult`. The old
`TemplateCheckResult` and `customized` field are removed.

```ts
// ---- Drafting templates ------------------------------------------------------
export type TemplateSource = "default" | "text" | "docx" | "pdf" | "scan";
export type TemplateStatusState =
  | "passes" | "has_findings" | "checking" | "out_of_date"
  | "partly_checked" | "couldnt_check" | "never_checked" | "no_playbook";
export type TemplateCheckTrigger =
  | "saved" | "uploaded" | "restored" | "reset" | "type_created" | "type_reactivated"
  | "playbook_published" | "playbook_changed" | "manual" | "check_all" | "retry";
export type TemplateCheckState = "queued" | "running" | "completed" | "failed" | "superseded";
export type TemplateCheckError =
  | "ai_unavailable" | "cost_cap" | "no_rules" | "no_playbook" | "unfillable" | "wording_changed" | "interrupted";
export type FindingSeverity = "low" | "medium" | "high" | "critical";
export type TemplatePlace = "body" | "table" | "header" | "footer" | "text_box" | "footnote" | "endnote";

export interface TemplateBinding { form_key: string; form_name: string; agreement_kind: string | null; label: string; }
export interface TemplatePlaybookRef {
  id: string;
  name: string;
  version_id: string | null;       // its latest published version (null when it has none)
  version_number: number | null;
  pinned: boolean;                 // false = today's name match, locked at the template's first check or draft
  available: boolean;              // false = deleted, or no published version
}
export interface TemplateFileRef { id: string; filename: string; mime_type: string; size_bytes: number; }

export interface TemplateCheckSummary {
  id: string;
  state: TemplateCheckState;
  trigger: TemplateCheckTrigger;
  unsaved: boolean;
  template_version: number;
  playbook_name: string | null;
  playbook_version_number: number | null;
  finding_count: number | null;        // null until completed
  highest_severity: FindingSeverity | null;
  text_was_truncated: boolean | null;
  error_code: TemplateCheckError | null;
  error_message: string | null;        // plain words
  requested_by: string | null;         // display name of the person
  on_behalf: boolean;                  // true = the system, on behalf of requested_by
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface TemplateCheckFinding {
  index: number;                       // position in the check; what applied_findings names
  rule_index: number | null;
  clause_type: string;
  severity: FindingSeverity;
  issue: string;
  quote: string | null;                // as the review quoted it (filled text)
  quote_template: string | null;       // the same with sample values turned back into {codes}
  suggested_fix: string | null;
  suggested_fix_template: string | null;
  suggestion_is_wording: boolean;      // false = advice, never applied
  approval_required: boolean;
}

export interface TemplateCheck extends TemplateCheckSummary {
  template_key: string;
  trigger_detail: Record<string, unknown> | null;
  wording_sha256: string;
  playbook_id: string | null;
  playbook_version_id: string | null;
  rule_count: number | null;
  findings: TemplateCheckFinding[];
  summary: string | null;
  text_length: number | null;
  read_length: number | null;
}

export interface TemplateStatus {
  state: TemplateStatusState;
  label: string;                       // "Has findings"
  reason: string;                      // "2 findings, highest High."
  finding_count: number | null;
  highest_severity: FindingSeverity | null;
  changed: "wording" | "playbook" | "both" | null;   // out_of_date only
  error_code: TemplateCheckError | null;             // couldnt_check only
  results_vary: boolean;
  decided_by: TemplateCheckSummary | null;
  last_completed: TemplateCheckSummary | null;
  active_check: TemplateCheckSummary | null;
}

export interface DraftingTemplateSummary {
  key: string;
  name: string;
  label: string;
  contract_type: string;
  binding: TemplateBinding;
  playbook: TemplatePlaybookRef | null;   // the playbook that reviews its drafts (null = none)
  shipped: boolean;
  active: boolean;
  source: TemplateSource;
  version: number;                        // 0 = shipped wording, never saved
  original_filename: string | null;
  updated_at: string | null;
  updated_by: string | null;
  editable: boolean;                      // false for a Word-based template (FR-40)
  status: TemplateStatus;
}

export interface DraftingTemplatePlaceholder { name: string; label: string; about: string; sample: string; }
export interface TemplateFieldUse { name: string; label: string; place: TemplatePlace; count: number; }

export interface DraftingTemplate extends DraftingTemplateSummary {
  body: string;
  default_body: string | null;            // null for a type that was not shipped
  placeholders: DraftingTemplatePlaceholder[];
  fields: TemplateFieldUse[];
  original_file: TemplateFileRef | null;
}

export interface AppliedFinding { check_id: string; finding_index: number; clause_type: string; severity: FindingSeverity; }

export interface DraftingTemplateVersion {
  version: number;
  note: string | null;
  source: TemplateSource;
  is_default: boolean;
  body: string;
  created_at: string | null;              // null for the synthetic version 0
  created_by: string | null;
  original_file: TemplateFileRef | null;
  applied_findings: AppliedFinding[];
  restored_from_version: number | null;
  ocr_confirmed_by: string | null;
  ocr_confirmed_at: string | null;
  checks: TemplateCheckSummary[];
}

export interface TemplateSavePayload {
  body: string;
  note?: string;
  expected_version: number;
  applied_findings?: { check_id: string; finding_index: number }[];
  acknowledge_missing_parties?: boolean;
}
export interface TemplateVersionAction { note?: string; expected_version: number; }
export interface TemplatePlaybookChange { playbook_id: string; note?: string; }
export interface TemplateMutationResult { template: DraftingTemplate; changed: boolean; check: TemplateCheckSummary | null; }
export interface CheckAllResult {
  started: { key: string; check_id: string }[];
  skipped: { key: string; reason: "checking" | "no_playbook" }[];
}

// Phase 1 (playbooks, for Change playbook) / Phase 2 (bindings, for New template type)
export interface TemplateBindingOption {
  form_key: string;
  agreement_kind: string | null;
  label: string;
  contract_type: string;
  held_by: { key: string; name: string } | null;
  suggested_playbook: { id: string; name: string } | null;   // today's name match; preselect only
}
export interface TemplateTypeOptions { bindings: TemplateBindingOption[]; playbooks: { id: string; name: string; version_number: number }[]; }

// Phase 2
export interface UploadParagraph { id: number; place: TemplatePlace; fillable: boolean; text: string; }
export interface UploadFieldFound { name: string; known: boolean; label: string | null; places: TemplatePlace[]; count: number; }
export interface TemplateWordingProblem {
  kind: "unknown_field" | "unpaired_brace" | "unfillable_place";
  paragraph: number;
  start: number;
  end: number;
  text: string;
  place: TemplatePlace;
  message: string;
}
export interface UploadAnalysis {
  kind: "docx" | "pdf" | "scan";
  filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  page_count: number | null;
  paragraphs: UploadParagraph[];          // [] for a scan until its text is confirmed
  fields: UploadFieldFound[];
  problems: TemplateWordingProblem[];
  ocr: { provider: string; pages: number | null; estimate: string } | null;
}
export interface RecognizedText {
  filename: string;
  sha256: string;
  page_count: number | null;
  pages: { page: number; text: string }[];
  empty_pages: number[];
  text: string;
}
export interface ConfirmTextPayload { filename: string; sha256: string; text: string; }
export interface TextConfirmation { confirmation: string; confirmed_at: string; }
export interface CancelUploadPayload { filename: string; sha256: string; after_ocr: boolean; }
export interface TemplateMark { paragraph: number; start: number; end: number; field?: string; literal?: boolean; }
export interface UploadPreview {
  text: string;
  body: string;
  fields: TemplateFieldUse[];
  problems: TemplateWordingProblem[];
  missing_parties: ("company" | "counterparty")[];
}

// Intake — POST /intake/requests/{id}/draft-contract
export interface DraftContractResult extends IntakeRequest { template_notice: string | null; }
```

For a PDF or a scan, `paragraphs[i].text` is `text.split("\n")[i]`, and the client and server use
that same rule. For a scan, the client builds the paragraphs from the confirmed text, and marks
refer to those lines.

### Endpoint client additions (src/lib/endpoints.ts)

`draftingTemplatesApi` (replaces the current object). Phase 2 functions are added in Phase 2.

- `list(includeRetired = false) → Promise<DraftingTemplateSummary[]>`: GET `/drafting-templates` (`?include_retired=true` when set)
- `get(key) → Promise<DraftingTemplate>`: GET `/drafting-templates/{key}`
- `versions(key) → Promise<DraftingTemplateVersion[]>`: GET `/drafting-templates/{key}/versions`
- `preview(key, body) → Promise<{ text: string }>`: POST `/drafting-templates/{key}/preview`
- `save(key, payload: TemplateSavePayload) → Promise<TemplateMutationResult>`: PUT `/drafting-templates/{key}`
- `reset(key, payload: TemplateVersionAction) → Promise<TemplateMutationResult>`: POST `/drafting-templates/{key}/reset`
- `restore(key, version, payload: TemplateVersionAction) → Promise<TemplateMutationResult>`: POST `/drafting-templates/{key}/versions/{version}/restore`
- `check(key, body?: string) → Promise<TemplateCheck>`: POST `/drafting-templates/{key}/checks` (`{}` or `{ body }`)
- `checks(key) → Promise<TemplateCheck[]>`: GET `/drafting-templates/{key}/checks`
- `checkDetail(key, checkId) → Promise<TemplateCheck>`: GET `/drafting-templates/{key}/checks/{checkId}`
- `checkAll() → Promise<CheckAllResult>`: POST `/drafting-templates/check-all` (`{}`)
- `setPlaybook(key, payload: TemplatePlaybookChange) → Promise<TemplateMutationResult>`: PUT `/drafting-templates/{key}/playbook`
- `options() → Promise<TemplateTypeOptions>`: GET `/drafting-templates/options`
- P2 `create(form: FormData) → Promise<TemplateMutationResult>`: POST `/drafting-templates` (`{ form }`)
- P2 `retire(key, note?) → Promise<DraftingTemplate>`: POST `/drafting-templates/{key}/retire`
- P2 `reactivate(key, note?) → Promise<TemplateMutationResult>`: POST `/drafting-templates/{key}/reactivate`
- P2 `analyzeUpload(form) → Promise<UploadAnalysis>`: POST `/drafting-templates/uploads/analyze`
- P2 `recognize(form) → Promise<RecognizedText>`: POST `/drafting-templates/uploads/recognize`
- P2 `confirmText(payload: ConfirmTextPayload) → Promise<TextConfirmation>`: POST `/drafting-templates/uploads/confirm-text`
- P2 `cancelUpload(payload: CancelUploadPayload) → Promise<void>`: POST `/drafting-templates/uploads/cancel`
- P2 `previewUpload(form) → Promise<UploadPreview>`: POST `/drafting-templates/uploads/preview`
- P2 `previewUploadDocx(form) → Promise<ArrayBuffer>`: `apiBytes("/drafting-templates/uploads/preview-docx", { form })`
- P2 `upload(key, form) → Promise<TemplateMutationResult>`: POST `/drafting-templates/{key}/upload`
- P2 `previewDocx(key) → Promise<ArrayBuffer>`: `apiBytes("/drafting-templates/{key}/preview-docx")`
- P2 `download(key, fallbackName) → Promise<void>`: `apiDownload("/drafting-templates/{key}/download", …)`
- P2 `downloadFile(key, fileId, fallbackName) → Promise<void>`: `apiDownload("/drafting-templates/{key}/files/{fileId}", …)`

`intakeApi.draftContract(id) → Promise<DraftContractResult>` (P1: return type only).

`api.ts` (P2): `apiBytes(path: string, opts?: { form?: FormData })`. With `form` it POSTs that body.
The auth and refresh handling stays as it is.

Multipart field names are frozen: `file`, `marks` (JSON string), `note`, `expected_version`,
`acknowledge_missing_parties` (`"true"`/`"false"`), `ocr_text`, `ocr_confirmation`, and for create also
`name`, `playbook_id`, `form_key`, `agreement_kind`, `body`.

## Component design

### Backend

**Models** (`backend/app/drafting_templates/models.py`, db-engineer)
- P1: `DraftingTemplateCheck` (table above) and `DraftingTemplateType` (with `playbook_id`).
  `DraftingTemplateVersion.applied_findings`, `.restored_from_version`. `backend/app/contracts/models.py`:
  `Contract.review_playbook_id = Column(String(36), ForeignKey("playbook.id"), index=True, nullable=True)`.
  The partial unique indexes are declared in `__table_args__` with
  `postgresql_where=text("status = 'active'")` / `"status = 'running'"` / `"status = 'queued'"`.
- P2: `DraftingTemplateVersion` gains `.source`, `.original_file_id`, `.docx_file_id`, `.content_sha256`,
  `.fields`, `.ocr_confirmed_by_user_id` and `.ocr_confirmed_at`.

**Schemas** (`backend/app/drafting_templates/schemas.py`, new). The request models move here from
routes.py (`TemplateSave` becomes `TemplateSaveRequest`). Response models (`DraftingTemplateSummaryOut`,
`DraftingTemplateOut`, `DraftingTemplateVersionOut`, `TemplateCheckOut`, `TemplateCheckSummaryOut`,
`TemplateStatusOut`, `TemplateMutationResultOut`, `CheckAllResultOut`, and in P2 `TemplateTypeOptionsOut`,
`UploadAnalysisOut`, `RecognizedTextOut`, `TextConfirmationOut`, `UploadPreviewOut`, `TemplateMarkIn`,
`ConfirmTextRequest`, `CancelUploadRequest`, `TemplateNoteRequest`) are set as `response_model=`.
Shape drift then fails tests instead of the page. `TemplateMarkIn` validates that exactly one of
`field` and `literal` is set and that `end > start`.

**Catalog and type registry** (`service.py`)
- `TEMPLATES` stays the shipped catalog (the 7 keys, unchanged names). Each entry gains `form_key` and
  `agreement_kind`. `contract_type` comes from `playbooks.library.CONTRACT_TYPE_OF_KIND` / `CONTRACT_TYPE_OF_FORM`
  rather than being typed again. It is the contract type stored on drafts and the name-match input for a
  shipped type that is not pinned yet; the pin itself (D-11) is what FR-51 relies on. `PLACEHOLDERS` entries gain a unique
  short `label` (FR-62): company "Our entity", counterparty "The other party", effective_date
  "Effective date", governing_law "Governing law", agreement_term "Term", payment_days "Payment days",
  value_sentence "Total value sentence", nda_kind_title "NDA kind (title)", nda_kind_word "NDA kind",
  purpose_phrase "Purpose", direction_recital "Who discloses to whom", term_clause "NDA term",
  survival_clause "Survival period".
- `@dataclass(frozen=True) TemplateType(key, name, label, form_key, agreement_kind, shipped, active, row_id,
  playbook_id)` with the property `contract_type` (from the library maps) and `binding` (dict as in the API).
- `list_types(db, org_id, *, include_retired=False) -> list[TemplateType]` overlays the table on the
  catalog. A row keyed by a shipped key supplies that type's `playbook_id` and, from P2, its status.
  Rows of new keys (P2) are appended. In P1 a row only ever exists because of a pin.
- **The pin (D-11)**:
  - `reviewing_playbook(db, org_id, ttype) -> Reviewing(playbook: Playbook | None, pinned: bool,
    available: bool)` is read only, for list, detail and status, and never writes.
    - Pinned: the pinned row (it must be in this org). It is available when `deleted_at` is null and it
      has a PUBLISHED version.
    - Not pinned: `PlaybooksService.pick_playbook_for_contract(contract_type)`, today's name match, with
      `pinned=False`.
  - `ensure_pinned(db, *, org_id, ttype, actor_user_id, commit: bool) -> Playbook | None` is the only
    lazy pin.
    - If the type is pinned, it returns the pinned playbook (possibly unavailable).
    - Otherwise it takes the name match. If there is none it returns None and pins nothing.
    - Otherwise it runs `INSERT INTO drafting_template_type (… catalog name/label/binding …, playbook_id) ON
      CONFLICT (org_id, key) DO UPDATE SET playbook_id = EXCLUDED.playbook_id WHERE
      drafting_template_type.playbook_id IS NULL RETURNING id`. When a row comes back it writes
      `drafting_template.playbook_pinned`, as the system on behalf of the person. It then re-reads the
      effective pin, so of two concurrent first uses one wins and both see it.
    - Called by `checks.request_check` (inside its lock, before the check row), by
      `draft_contract_for_request` (with `commit=True`, **before** rendering or creating the contract, so
      the audit lock is never held across text extraction), and indirectly by `on_playbooks_published`.
  - GET paths never pin.
- `get_type(db, org_id, key) -> TemplateType` (404 `No such template`, any status).
- `active_type_for_binding(db, org_id, form_key, agreement_kind) -> TemplateType | None` (P2).
- `current_wording(db, org_id, ttype) -> Wording(version, sha256, body, row)`. The body is the newest row's
  body, else `default_body(key)`. sha256 is `sha256(body)` for text, or `row.content_sha256` for a Word-based
  row. The shipped default is hashed live, so a deploy that edits `defaults/<key>.txt` turns version 0
  Out of date.

**Wording service** (`service.py`, `DraftingTemplateService`). Every method filters on `org_id`.
Every mutation writes its audit row on the request session and commits. Only **after** that commit
does it call `checks.request_check`, which takes the template lock and then the audit lock (lock order,
see Risks).
- `list(org_id, include_retired)`, `get(org_id, key)`, `versions(org_id, key)`. Versions carry checks by
  `template_version` and a synthetic version 0 for shipped types.
- `save(actor, key, body, note, expected_version=None, applied_findings=(), acknowledge_missing_parties=False)`.
  Steps: retired 409 (P2), Word-based 409 (P2), `_check_version(expected)` 409, `check_body(body)` 422 with
  line, `_missing_parties(body)` 422 unless acknowledged, validate applied findings 422, unchanged
  returns `changed=False`. Otherwise `_add(...)` (NULL body when equal to the default, as today), then
  `request_check(trigger="saved")`. `expected_version=None` skips the conflict check (service-level callers
  and tests); the route always passes it.
- `reset(...)` (shipped only in P2; `trigger="reset"`). `restore(actor, key, version, note, expected_version)`
  copies body (and in P2 source, file ids, hash, fields and OCR fields), sets `restored_from_version`,
  and uses `trigger="restored"`. Version 0 is the same as reset.
- `set_playbook(actor, key, playbook_id, note)` (P1, D-11). It checks, in order:
  1. Retired gives 409 (P2).
  2. The playbook must be in this org, not deleted, and have a PUBLISHED version, else 422 `Choose one of
     this organisation's published playbooks.`
  3. If it is already the pin, return `changed=False`.
  Otherwise it upserts the type row (a shipped type gets its catalog row) with the new `playbook_id`,
  writes `drafting_template.playbook_changed` (before/after), commits, then calls
  `request_check(trigger="playbook_changed")`. Contracts already drafted keep their own
  `review_playbook_id`: their review and risk band do not move (FR-45).
- `check_body(body, *, place=None)` gains a location: a line number for text, or the Word place. Its
  "Unknown placeholder" wording stays, so `test_drafting_templates.py` keeps its substrings. A field must
  be a bare identifier (`{name}`): a conversion or format spec reads as unknown.
- P2: `create_type(actor, form)`, `retire(actor, key, note)` (inserts or updates the row to `retired`; audit),
  `reactivate(actor, key, note)` (row to `active`; IntegrityError on a partial index, or the pre-check,
  gives 409 naming the holder; then `request_check(trigger="type_reactivated")`). Also
  `download(org_id, key)` (Word-based: the stored `docx_file_id` bytes; otherwise `_build_plain_docx(title="",
  subtitle="", text=body)` from `contract_files.routes`, with codes visible), `file(org_id, key, file_id)`
  (the id must equal `original_file_id` or `docx_file_id` of a version of this key in this org) and
  `preview_docx(org_id, key)`.
- `options(org_id)` (P1): `playbooks` are this org's playbooks that are not deleted and have a published
  version (FR-50/69). `bindings` (used from P2) carry `held_by` and a `suggested_playbook` from today's
  name match, which is only a preselection.
- `create_type` checks in this order: name 1–120 after strip; a binding in
  `CONTRACT_TYPE_OF_KIND ∪ CONTRACT_TYPE_OF_FORM`; the binding not held by an active type (409);
  the name unique among active (409); **the playbook in this org, not deleted, with a published version
  (else 422; this is FR-51's refusal, because any such playbook can be pinned and so guaranteed)**;
  then the wording (text through `check_body` and parties, or a file through
  `uploads.wording_from_submission`). It then inserts the type row with `playbook_id` = the chosen
  playbook (the pin) and version 1 (`source` from the wording), writes the audits `type_created` and
  `upload_confirmed` (if a file), commits, and calls `request_check(trigger="type_created")`. The key is a
  slug of the name, `[a-z0-9_]`, at most 40 characters, made unique with `_2`…, never a shipped or
  reserved key. Nothing is written before every check passes (FR-48, AC-43).

**Checks** (`backend/app/drafting_templates/checks.py`, new). This is the single place for queueing,
running and judging checks. Frozen signatures:

```python
def request_check(db, *, org_id: str, key: str, trigger: str, actor_user_id: str | None,
                  unsaved_body: str | None = None, trigger_detail: dict | None = None
                  ) -> DraftingTemplateCheck | None:   # commits; None = no playbook covers it (FR-9)
def check_all(db, *, user) -> dict                     # CheckAllResult; one request_check per template
def on_playbooks_published(db, *, org_id: str, playbook_ids: list[str], actor_user_id: str | None,
                           detail_for: Callable[[str], dict]) -> None
async def run_check_job(db, job: JobRun) -> None       # the Celery handler
def template_status(db, *, org_id: str, ttype: TemplateType) -> dict   # TemplateStatus
def status_notice(status: dict) -> str | None          # FR-47 text; None when Passes
def serialize_check(db, check, *, full: bool) -> dict  # TemplateCheck / TemplateCheckSummary
```

- **Single flight (FR-15).** `request_check` takes `pg_advisory_xact_lock(hashtext('drafting_template_check:'
  || org_id || ':' || key))`. The int4 space cannot hit the audit chain's bigint key. Under that lock it
  reads the queued and running checks of the template and applies these rules in order:
  1. Automatic triggers skip a retired type (P2). Then `ensure_pinned(...)` (D-11). If there is no
     playbook, or the pinned one is unavailable (deleted or without a published version), it returns None:
     no row, no cost (FR-9). For a manual check the route turns that into 409 first.
  2. Unsaved request: if a check is queued, 409 "queue full". Otherwise it queues.
  3. Saved wording: a queued check of the same wording (version + sha) is returned as is. That check
     resolves the pinned playbook's latest published version when it runs, so it already covers a newer
     publication or a pin change. A running check of the
     same wording is returned for `manual`/`check_all`/`retry` only.
  4. Otherwise any queued check is marked `superseded` (audit `check_superseded`), and the new check is
     queued.
  5. It writes `check_started`, then commits. If nothing is running it calls `_dispatch(check)`:
     `create_job(job_type="template_check", resource_type="drafting_template_check", resource_id=check.id,
     created_by_user_id=…, idempotency_key=f"template_check:{check.id}")`, commit, `dispatch_job`, commit.
  The partial unique indexes back this up in the database.
- **Trigger** for a manual request: `retry` when the latest finished saved check matches the current
  pair and failed, otherwise `manual`.
- **`run_check_job`** works in four steps. Each step has its own commit, and no audit row is held open
  across the AI call.
  1. `SELECT … FOR UPDATE` the check (`org_id == job.org_id`). If it is not `queued` (superseded or
     already handled), mark the job succeeded and return.
  2. If `job.attempt_count > 1` (a dead worker was reclaimed), fail with `interrupted`. FR-20: never
     re-run a paid review on its own.
  3. Set `running` and `started_at`, then commit.
  4. Resolve the type and **its pinned playbook** (`ttype.playbook_id`; missing or deleted:
     `no_playbook`). The worker never pins and never falls back to the name match. Then
     `select_run_version` (409: `no_playbook`) and `rules_in_review_order` (empty: `no_rules`). Build the
     text. A saved check loads version N; its sha must equal `check.wording_sha256`, else `wording_changed`.
     `check_body` failing gives `unfillable`. The text is `render_document(key, company=SAMPLE company,
     counterparty=SAMPLE counterparty, effective=SAMPLE date, fields={}, body=…)`, exactly as today.
     **P3:** a Word-based version instead uses `docx_fields.fill(docx, template_values(key, …, fields={}))`,
     then `(await contract_files.service._resolve_extracted_text(content, mime_type=DOCX,
     filename="template.docx")).text`, the very reader path the draft takes. It then awaits
     `PlaybooksService(db).review_text(user=<requester, else the org's oldest user>, playbook, text,
     title=ttype.name, contract_type=ttype.contract_type, counterparty_name=SAMPLE counterparty,
     resource_type="drafting_template_check", resource_id=check.id)`. It catches `HTTPException` 429 as
     `cost_cap` and any other exception as `ai_unavailable` (logged, with no detail leaked). Nothing is
     re-raised, so Celery `autoretry_for` never fires.
  5. It records `completed` with the playbook ids, name, version number, rule_count, findings, summary,
     truncation, `text_length = len(text)` and `read_length = min(len(text), MAX_FULL_TEXT_CHARS)`, or
     `failed` with code and message. It writes the audit, marks the job SUCCEEDED (completed) or FAILED
     with `error_message` (failed), and commits. Reclaim never retries FAILED.
  6. `_start_next(db, org_id, key)`: take the lock; if nothing is running, dispatch the oldest queued
     check; commit.
- **Findings stored** add `index` and `suggestion_is_wording = is_document_wording(suggested_fix)` to each
  `review_text` finding. They also add `quote_template` / `suggested_fix_template`, made by
  `template_form_of(text, values)`: escape `{`→`{{` and `}`→`}}`, then replace each check value of at
  least 12 characters (company, counterparty, effective_date, governing_law, agreement_term,
  purpose_phrase, direction_recital, term_clause, survival_clause), longest first, with `{name}`.
  The page can then locate a quote in the template, and Apply never hard-codes "Globex Corporation"
  into our paper.
- **`template_status`** reads checks with `unsaved = false`, `status in (completed, failed)`, for this
  org and key. Here pv is the latest published version of `reviewing_playbook(...)` (the pin, or for an
  unpinned shipped type today's name match), and "pair" means (template_version, wording_sha256,
  playbook_version_id). A pin change therefore reads as Out of date "playbook changed" until it is
  re-checked.

| Order | Condition | State |
|---|---|---|
| 1 | reviewing playbook is None or not `available` | `no_playbook`. Reason: unpinned and no match gives "No published playbook covers {contract_type} contracts. Choose a playbook for this template."; pinned but gone gives "{name} is reviewed by {playbook}, which {was deleted / has no published version}. Publish it, or choose another playbook for this template." |
| 2 | a queued or running saved-wording check exists | `checking` |
| 3 | A = latest finished saved check (by `finished_at`) is None | `never_checked` |
| 4 | A's wording ≠ current, or (A.playbook_version_id set and ≠ pv) | `out_of_date` (`changed` = wording / playbook / both) |
| 5 | A failed | `couldnt_check` (A.error_code / message) |
| 6 | A completed and `text_was_truncated` | `partly_checked` (reason: "The review read the first {read:,} of {total:,} characters; the last {pct}% was not checked.") |
| 7 | A completed with ≥1 finding (fallback and low count, FR-4) | `has_findings` (count, highest severity) |
| 8 | otherwise | `passes` |

  `results_vary` is true when, among the completed saved checks of the **current pair**, at least one has
  findings and at least one has none. It is never computed from unsaved checks and it starts nothing
  (FR-11). The labels are the spec's eight names. The reasons are fixed sentences in `checks.py`.
  `status_notice`: `"This template does not currently pass its playbook: {label} — {reason} The draft was
  created anyway."`.
- **`on_playbooks_published`** goes through each active type. Its reviewing playbook is the pin, or,
  only for an unpinned shipped type, today's name match. If that playbook's id is in `playbook_ids`, it
  calls `request_check(trigger="playbook_published", trigger_detail=…)`, which pins an unpinned one, with
  one commit per template. A pinned template whose pin is a different playbook is **not** checked:
  a newly published, better-named playbook never takes it over (D-11). Exceptions are logged and never
  fail the publish, which has already committed (FR-14).

**Uploads** (`backend/app/drafting_templates/uploads.py`, new, P2). Stateless: nothing is stored until
the final confirmation (FR-26, FR-72).
- `read_template_upload(db, user, upload) -> IngestedUpload`:
  1. `_read_upload_with_limit` (413).
  2. Refuse empty (422).
  3. Work out the MIME type from the content-type or the `.docx`/`.pdf` extension. Anything else is 415
     with the template-only message.
  4. `validate_upload_mime` (415 mismatch, or the protected-.docx message).
  5. `asyncio.to_thread(_scan_for_malware)`.
  On 413, 415 or AV it writes `upload_refused`, commits, then re-raises. All of these helpers are reused
  from `contract_files.service`.
- `analyze(content, mime) -> dict`. A docx goes through `docx_fields.refuse_revisions_and_comments`, then
  `paragraphs`, then `field_uses`; with no text it is 422. A PDF: `fitz.open(stream=…)`, `needs_pass` gives
  the 422 password message; the Documents reader `PdfParser.parse` returns `needs_ocr` for a scan
  (`ocr` estimate = page count, provider "Reducto"), otherwise paragraphs are the lines of the reader
  text. A damaged file is 422.
- `recognize(db, user, upload)`: it must be a scan. `reducto_api_key` unset and not mock gives 503.
  It writes `ocr_agreed` and commits **before** the call. `await reducto_client.extract_text(…)` runs
  inline (see decisions). An exception writes `ocr_failed`, commits, and returns 502. Empty text writes
  `ocr_no_text`, commits, and returns 422 (FR-74). Pages are built from `OCRResult.blocks[].page`, and
  `empty_pages` are the pages from 1 to `num_pages` that are missing. Nothing is kept. It is never
  retried by code.
- `confirm_text(user, payload)`: write `ocr_text_confirmed`, commit, and return a JWT HS256 with
  `settings.secret_key` and claims `{typ:"template_ocr_text", uid, file_sha256, text_sha256, confirmed_at,
  exp: +24h}` (the `editor.py` `_sign` style).
- `wording_from_submission(user, upload, marks, ocr_text, ocr_confirmation) -> Submitted(source, body,
  docx_bytes|None, fields, original bytes, ocr_by, ocr_at)`:
  - docx: `apply_marks`, `field_uses` (any problem is 422), then the body is
    `(await _resolve_extracted_text(template_docx, DOCX)).text`.
  - pdf: the lines with marks applied, joined by "\n".
  - scan: needs `ocr_text` and a valid token whose `uid` is the user, whose `file_sha256` is the file's
    sha and whose `text_sha256` is the sha of `ocr_text` (else 422 unconfirmed). It is refused for a
    non-scan.
  Then `check_body(body)`.
- `confirm_upload(actor, key, form)`: retired 409, version conflict 409, `wording_from_submission`,
  then parties 422 unless acknowledged. **Unchanged (FR-37):** for a Word file, the canonical hash equals
  the current `content_sha256`; otherwise the body equals the current body while the current version is
  not Word-based. That returns `changed=False` and stores nothing. Otherwise it stores the original
  (and the template docx) with `storage_service.save_bytes`, creates `StorageObject` rows and the
  version (`source`, file ids, `content_sha256`, `fields`, `ocr_*`), writes `upload_confirmed`, and
  commits. **Any exception after the bytes are saved deletes them** (`delete_bytes_permanently`), the
  same invariant as `create_contract_from_upload`. Then `request_check(trigger="uploaded")`.

**Word field codes** (`backend/app/drafting_templates/docx_fields.py`, new, P2 engine, P3 drafting).
It needs no new dependency: `zipfile` + `lxml` (installed with python-docx). It is the risky part,
so here it is concretely:
- **How a field is marked.** A field is the visible plain text `{name}` inside the Word file, the same
  notation as text templates. A literal brace is `{{` / `}}`. There are no content controls and no
  MERGEFIELDs. A plain code survives Word editing, LibreOffice and Google Docs, it is what FR-39 asks
  to be visible, and FR-32 needs code recognition anyway. Word splitting `{counterparty}` into
  `{` + `counter` + `party}` runs (spell-check, rsid, a bold first letter) is handled by working at the
  **paragraph** level, never the run level.
- **Paragraph text model.** For each `w:p`, the text is the concatenation of every `w:t` whose nearest
  ancestor `w:p` is this paragraph, so a text box nested in a run counts as its own paragraph. Runs inside
  `w:hyperlink`, inline `w:sdt` and smart tags are included. `w:tab` gives `"\t"` and `w:br`/`w:cr` give
  `"\n"`; these virtual characters show in the text but have no `w:t`. A mark or code may not span one.
  An offset map records `(w:t node, start, end)`.
- **Parts and order (paragraph ids).** The order is deterministic for the same bytes: `word/document.xml`
  body in document order (tables, content controls, text boxes included), then each distinct header part,
  then each footer part (by `[Content_Types].xml` and the relationship target, de-duplicated), then
  footnotes and endnotes.
  - Place: `table` (ancestor `w:tc`), `text_box` (ancestor `w:txbxContent`), `header`, `footer`,
    `footnote`, `endnote`, else `body`.
  - Fillable: body, table, header, footer. A code in another place is a problem that blocks confirmation
    (spec edge case).
- **`_rewrite(paragraph, spans)`** is the one primitive used to mark and to fill. It processes
  `(start, end, replacement)` spans right to left. The replacement goes into the `w:t` where the span
  starts, so it carries that run's `w:rPr` (FR-42: the formatting at the field's place). The covered text
  is cut from the following `w:t` nodes, which are emptied, never removed, and `xml:space="preserve"` is set.
- **`apply_marks(docx, marks)`**: a mark is (paragraph, start, end) with the replacement `{field}`, or
  with the span text brace-doubled (`literal`). An out-of-range mark, a mark across a tab or break, or a
  mark in an unfillable place is 422.
- **`field_uses(docx) -> (uses, problems)`** scans every paragraph with the same tokenizer as
  `string.Formatter` (`{{`, `}}`, `{identifier}`, lone braces) and reports unknown names, unpaired braces
  and codes in unfillable places with paragraph and offsets.
- **`fill(docx, values) -> bytes`**:
  1. `{name}` becomes the value (newlines turned into spaces), and `{{`/`}}` become `{`/`}`, in fillable
     places.
  2. An empty value drops one of two spaces that it would leave side by side.
  3. A paragraph that held a code and is now whitespace-only is removed. It is kept if it holds a
     drawing, a field or `w:sectPr`, or if it is the last `w:p` of its cell, header or footer.
  4. Other content is untouched. Values go in as text nodes, so lxml escapes quotes, braces and
     non-English text (FR-42 edge).
- **`refuse_revisions_and_comments(docx)`** looks in the document, header, footer, footnote and endnote
  parts for any of `w:ins w:del w:moveFrom w:moveTo w:rPrChange w:pPrChange w:sectPrChange w:tblPrChange
  w:trPrChange w:tcPrChange w:numberingChange w:cellIns w:cellDel`, and for any `w:commentRangeStart`,
  `w:commentReference` or a non-empty comments part. It returns 422 with the FR-29 messages.
- **Writing.** The output zip copies every member's `ZipInfo` (name, order, date, compression) and swaps in
  only the changed XML parts. Styles, numbering, tables, headers, footers, section and page setup, media
  and unknown parts are kept byte for byte (FR-41). `content_hash(docx)` is the sha256 over the sorted
  (member name, member bytes), so zip timestamps never make an identical upload look new (FR-37).
- **Safety.** It reuses `_guard_zip` (zip-bomb limits) from `app.documents.reader.parsing.docx`, and parses
  with `etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)` (no XXE).

**Drafting** (`backend/app/intake/drafting.py`)
- P1: `_FORM_DOC` / `_KIND_DOC` are derived from `TEMPLATES` (`form_key` / `agreement_kind`), so the binding
  shown on the page and the one drafting uses cannot drift. In `draft_contract_for_request`, non-custom:
  1. `ttype = get_type(...)`.
  2. `pin = drafting_templates.service.ensure_pinned(db, org_id, ttype, actor_user_id=actor.id, commit=True)`
     (D-11). It pins an unpinned shipped type at its first draft and commits that on its own, so the audit
     lock is never held across text extraction.
  3. `status = checks.template_status(...)` and `notice = status_notice(status)`.
  All three are computed **before** rendering ("at that moment"). `on_created`, one wrapper that runs
  `apply_request_facts` unchanged and then stamps the template, sets in the same transaction, before any
  AI job is dispatched:
  - `contract.review_playbook_id = pin.id` (also when the pin is currently unavailable; NULL only when
    the template has no playbook at all);
  - `contract.metadata_json["drafted_from_template"] = {key, name, version, source, status: state, reason,
    notice, review_playbook_id, review_playbook_name}`.
  The existing audit `intake.contract_drafted` gains the template fields and `review_playbook_id`. A contract timeline event is added (FR-46); the
  notice text is in it for every draft, workflow or person (FR-47, A-15). Drafting is never blocked.
  `intake/routes.py` `draft_contract` returns `DraftContractResponse` with
  `template_notice = contract.metadata_json["drafted_from_template"]["notice"]`.
- P2: `resolve_doc_type(request, db: Session | None = None)`. Without `db` it behaves as today, which keeps
  the pure unit tests and the `__main__` self-check. With `db`:
  - Form `new_agreement`, `sow` or `dpa`: `active_type_for_binding(...)`, and its key or None.
  - Any other form: None.
  - Keyword path: the shipped key only if that type is still active.
  Call sites that change to pass `db=`: `intake/drafting.py` (`draft_contract_for_request`,
  `ingest_attachment_as_contract`), `intake/service.py` (`_draftable(db, r)`, called from `serialize_request`
  with `self.db`), and `workflows/service.py` `select_flow` (~line 408) and the `clm_draft` step (~line 1171).
  `_type_token` (~line 119) stays pure: it is a word-routing token, not a drafting decision.
  `_DOC_TYPES[doc_type]` lookups become `get_type(db, org_id, key)`. For "Selling to a customer" with an
  active Customer type, the request page's Draft button appears, and the Customer workflows' new draft
  step drafts it automatically (FR-53, D-12; see Workflows). A retired kind, or a kind with no template,
  resolves to None, so Legal handles it (FR-53/54; see the `clm_draft` rule below).
- P3: when the current version is Word-based, the draft is built as follows:
  1. Read the template docx from storage.
  2. `docx_fields.fill(…, template_values(key, company, counterparty, effective, fields=fv))`.
  3. `UploadFile(content-type DOCX, filename "{label} - {counterparty}.docx")`.
  4. The same `create_contract_from_upload(..., contract_type=ttype.contract_type, on_created=…,
     advance_reason=DRAFTED_MOVE_REASON)`.
  `ingest_upload` checks it, the bytes are stored, `_resolve_extracted_text` reads it, the snapshot and
  elements are written and the AI jobs queue, exactly like any Word upload (FR-43). Every other source
  keeps `render_document` and the `.txt` path, byte for byte as today (FR-44).

**FR-51 and D-11: the pinned playbook, and the call sites.** A template records the playbook that
reviews it (`drafting_template_type.playbook_id`). The template's checks use it, and so does every
contract drafted from it: its automatic review **and** its risk band. The contract carries it in a
**column**, `contract.review_playbook_id`, set once at drafting.

*Why a column and not the FR-46 metadata record.* The risk band decides approvals, authority limits and
fast lanes. `metadata_json` is rewritten by many paths (renewals, field sources, auto-review flags), so
it is easy to drop or overwrite. A FK column is immutable in practice, has integrity, and is a cheap
read in the risk hot path. The FR-46 record copies the id and name for history only.

The smallest guarantee is **one new function** that the two contract-level choke points call:

```python
# playbooks/service.py — PlaybooksService
def playbook_for_contract(self, contract: Contract) -> Playbook | None:
    """The playbook that reviews this contract: the one pinned when it was drafted from a template
    (contract.review_playbook_id), else today's name match on contract_type (uploads, attachments,
    custom drafts, drafts made before pins existed). A pinned playbook that is deleted or has no
    published version gives None ("no applicable playbook"); it never falls back to the name match,
    so a better-named playbook can't take over (D-11)."""
```

Call sites that change:
1. `PlaybooksService.auto_review_contract` (`playbooks/service.py` ~line 1465):
   `self.pick_playbook_for_contract(org_id=…, contract_type=contract.contract_type)` becomes
   `self.playbook_for_contract(contract)`. This covers the review after drafting (the `_maybe_auto_review`
   job) and after a counterparty revision or accepted edits.
2. `contracts/risk.py` `_review_runs` (~line 104): the same swap. Every risk consumer goes through
   `_review_runs`, so they all follow the pin with no change of their own: `contract_risk` /
   `refresh_contract_risk`, the "Retry review" path (`contracts/auto_review.py`), the workflow AI-review step
   (`workflows/service.py` ~line 959–976), approvals (`approvals/service.py:257`), authority limits
   (`authority/service.py:85`), the assistant's risk read (`ai/tool_runtime.py:1340`), and the jobs
   "review still holds" short-circuit (`jobs/tasks.py:122`).
3. `intake/drafting.py` `draft_contract_for_request`: `ensure_pinned` before rendering, and
   `on_created` sets `contract.review_playbook_id` (above). `apply_request_facts` does **not** change:
   it still sets `contract_type` (display, NDA fast lane, and the name match for unpinned contracts).
4. `drafting_templates/service.py` / `checks.py`: checks, status and the publish hook use the pin
   (`reviewing_playbook` / `ensure_pinned`). The worker uses `ttype.playbook_id` only.
   `TEMPLATES[*]["contract_type"]` still comes from the library maps: it is only the name-match input
   for a not-yet-pinned shipped type, and the contract type stored on drafts.

`pick_playbook_for_contract` itself and its name matching do **not** change. Its remaining callers are
`playbook_for_contract`'s fallback, `reviewing_playbook` / `ensure_pinned` for unpinned shipped types,
and `options()` for `suggested_playbook`. Explicit playbook runs are unchanged (`POST /playbooks/{id}/runs`,
the assistant's `run_playbook_review` / `redline_against_playbook` with a named playbook). As today, they
count towards a contract's band only when they are of the playbook `_review_runs` picks, which for a
drafted contract is now its pin.

**Contracts not drafted from a template keep today's name pick, unchanged.** These are uploads (web, Word
add-in, editor saves of uploads), "use the attachment" contracts, custom AI drafts (`custom=True`), and
every contract drafted before this feature ships (`review_playbook_id` NULL; no backfill, see decisions).
For all of them `playbook_for_contract` is literally `pick_playbook_for_contract(contract_type)`. Existing
tests pin that and must stay green untouched: `test_playbook_library.py`
(`test_each_contract_type_gets_its_own_playbook`, `test_ndas_are_left_to_the_orgs_own_nda_playbook`),
`test_one_risk_signal.py` (incl. `test_no_playbook_for_the_type_is_unknown` and the qualifying-run cases),
`test_risk_consumers.py`, `test_current_findings.py`, `test_nda_fast_lane.py`,
`test_nda_ai_step_reviews_contract.py`, `test_carry_decisions.py`, `test_interrupted_review.py` and
`test_issue_fix_drafting.py`. The last one monkeypatches `pick_playbook_for_contract`, which still takes
effect through the fallback.

**Shipped types get their pin lazily, by today's name match, at first use.** A shipped type has no row
until it is first needed. Its first check request (save, reset, restore, manual, Check all), its first
draft, or a publish of the playbook that matches it by name calls `ensure_pinned`, which records the
name match at that moment (audited `playbook_pinned`). From then on it is locked. Until then the page
shows the name match with "matched by name; locked at its first check or draft" (`pinned: false`), and
behaves exactly as today. Nothing is pinned at release, which keeps A-11. A shipped type with no matching
playbook stays unpinned and shows No playbook; an admin pins one with Change playbook.

**When the pinned playbook is deleted or has no published version:**
- Status is **No playbook** (the spec's own edge case: "unpublished … or deleted: the template shows No
  playbook, earlier results stay viewable, no new check starts"), with the reason naming the playbook and
  what happened to it. No check starts (`request_check` returns None; a manual check is 409). Earlier
  results stay viewable.
- Drafting is still allowed (FR-47). The notice says No playbook and why. The draft still records the pin
  in `review_playbook_id`, so if that playbook is published again its reviews resume against it. Until
  then `playbook_for_contract` gives None: the draft's automatic review does what it does today when no
  playbook applies ("no published playbook"), and its band is unknown. There is no silent fallback to
  another playbook.
- The remedy is to publish the playbook again or Change playbook.

**Changing the pin** is `PUT /drafting-templates/{key}/playbook` (`playbook:update`, audited
`drafting_template.playbook_changed` with before/after). It only accepts published, non-deleted playbooks
of this org, and starts a check against the new pin (`playbook_changed`). Contracts already drafted keep
the playbook they were drafted with (FR-45). Moving them would change their risk band after the fact.

**Workflows (P2).**
- **`clm_draft` rule** (`workflows/service.py`, the `clm_draft` branch, ~line 1171):
  - On the fast-lane path, when `resolve_doc_type(req, db=db)` is None **and the request was filed on a
    form** (`field_values.request_form` set), the step is **skipped**. Its note is "No active template for
    this kind of agreement — Legal prepares the paper." The workflow goes on to its next step (the Legal
    review). That is "handled by Legal as today" for a kind with no template (FR-53) or a retired one
    (FR-54).
  - Requests without a form (email, chat) keep today's `custom` path, which keeps
    `test_a_contract_kind_with_no_template_is_drafted_fresh_instead_of_failing` green.
  - Today every form kind that has a draft step also has a template, so no current form request changes
    behaviour.
- **D-12, Customer starter workflows** (`workflows/contract_library.py`): both "Customer — standard" and
  "Customer — strategic deal" get
  `draft("legal_counsel", 24, "Draft from the Customer Agreement template.", cond=OUR_TEMPLATE)` as their
  **first** step, where every other drafting kind puts it (before AI review and Legal review).
  - `stages.infer` gives it the fixed `drafting` stage. The following AI review and Legal review stay in
    `review`, so lifecycle order holds (`stages.problems` is empty).
  - The `cond` is `{"field": "paper", "op": "eq", "value": "Our template"}`. The step is skipped on "Their
    paper", the way NDA requests draft only on our template, and the AI review then reads their paper as
    it does today.
  - `draft()` gains a `cond=None` parameter passed to `_step`, and `OUR_TEMPLATE` sits next to
    `PERSONAL_DATA`.
  - Nothing else in the two workflows changes: no step text, no description, no conditions, no
    `eval_order`. A migrated workflow is then identical to a freshly seeded one apart from step ids
    (D-13). Only the code comment above the section changes. "Prepare our terms or review the customer's"
    in Legal review still holds when the draft step skips. Routing doesn't change. With no Customer
    template the new step skips by the rule above, which keeps FR-53's "until one exists … handled by
    Legal as today".
- **Orgs that already seeded the Customer workflows (D-13).** `seed_builtin_flows` (`workflows/builtin.py`,
  run by "Seed defaults") only adds builtin workflows missing by name and never updates one, so the
  `0075_template_uploads` data step does it (see Database schema). It touches only unedited builtin
  Customer workflows. In-flight runs are unaffected because `WorkflowRun.steps` is a pinned snapshot
  (`workflows/models.py:53`). A workflow an admin has edited, or one that doesn't match the original steps
  exactly, is left alone. For those the release note says: open it in Admin → Workflows, add a "Prepare
  draft" step at the top (the builder puts it in Drafting), and set "only when Whose paper? is Our template".

**Router** (`routes.py`): the endpoints above, with permissions as in the API table. `_rid(request)` is
passed through. P2 multipart endpoints use `UploadFile = File(...)` and `Form(...)`, and marks are
parsed with `TypeAdapter(list[TemplateMarkIn])`.

**Playbooks hooks** (`playbooks/routes.py`): after `publish_playbook`'s `db.commit()`, call
`checks.on_playbooks_published(db, org_id, [playbook.id], actor_user_id=current_user.id, detail_for=…)`.
After `seed_library`'s commit, do the same for the ids of the `added` names. `app/devtools.py` seeding
(dev `make seed`) gets **no** hook: see the decisions.

**Celery**: job type `template_check`. Add one branch in `_run_ai_job`:
`elif job.job_type == "template_check": from app.drafting_templates.checks import run_check_job;
await run_check_job(db, job)`. The idempotency key is `template_check:{check_id}`. There is no beat
schedule, which means no periodic re-check and no retry loop (FR-20).

**Settings**: none. Nothing goes into `validate_runtime_settings()`. OCR reuses `MOCK_REDUCTO` /
`REDUCTO_API_KEY`, the confirmation token reuses `settings.secret_key`, and the size limit is
`max_upload_size_bytes`.

### Frontend (Next.js page/component tree)

- `src/app/(app)/templates/page.tsx`: the page shell.
  - Header (title, description).
  - Actions: **Check all** (shown only with `playbook:run`) and, in P2, **New template type** (only with
    `playbook:update`).
  - A standing info `MessageBar` with the three FR-10 statements.
  - The status strip: one `<button aria-pressed>` per status with its count, plus "Needs attention (n)"
    (FR-63).
  - The two-column list and detail.
  - Polling: `refetchInterval` 3000 ms while any listed status is `checking`.
  - States: loading `SkeletonRows`, `LoadError` with `onRetry` (FR-60), `EmptyState` (P1 filter empty:
    "No templates need attention"; P2 none active: "No active templates. Reactivate a retired one or add a
    template type.", with the action when the person may change templates).
  - Owns the leave guard.
  - `_template-list.tsx`: the items, as keyboard-focusable buttons. Each shows the name, `StatusBadge`
    (text label + lucide icon + `Badge` tone, never colour alone, FR-65), a "Results vary" `Badge` when
    flagged, the playbook name or "No playbook", the binding label, and the source and version ("Shipped
    wording", "Edited · v4", P2 "Word file · v2 · MSA.docx", "PDF · v1", "Scan · v1"). In P2 a collapsed
    "Retired (n)" group follows. Exports `StatusBadge`.
  - `_template-detail.tsx`: the header shows the name, "Reviewed by {playbook} v{n}" (or "No playbook
    reviews it"), "Drafts {binding}", the source and version, `StatusBadge` with its reason, and "Last
    checked {finished_at} against {playbook} v{n}" (FR-10).
    - The playbook line says "locked to this template" when `pinned`, or "matched by name; locked at its
      first check or draft" when not. It says "deleted" or "has no published version" when not
      `available`.
    - Actions by permission:
      - **Change playbook** (`playbook:update`, D-11): a `Modal` with a `Select` of `options().playbooks`.
        It confirms that "a paid AI review against the new playbook starts; contracts already drafted
        keep the playbook they were drafted with", then calls `setPlaybook`.
      - Reset (shipped).
      - In P2: Upload replacement, Download Word file, and Retire / Reactivate with a `useConfirm` that
        spells out the FR-54 consequences.
    - People without change permission see a one-line reason ("You can view and check templates; changing
      them needs the permission to edit playbooks.", FR-64).
    - `Tabs`: Wording · Preview · Checks · History. The Preview tab is inline: text uses
      `draftingTemplatesApi.preview`; P2 Word uses `_docx-preview.tsx`; both list the fields used.
  - `_wording-editor.tsx`: the Wording tab.
    - Text templates keep a raw `<textarea>`, as today, because the chip overlay needs its exact metrics.
      An `aria-hidden` mirror sits behind it and renders `«Label»` chips and unknown `{x}` in danger tone.
      Field codes appear as `«The other party»` (FR-62); `templates.ts` `toEditorText` / `toBodyText` convert
      between the two. Pasting turns known `{name}` into chips.
    - The field list (plain name, about, example) has Insert buttons.
    - Problems are listed with "Use a field…" / "Keep as ordinary text" fixes (FR-34). A missing-party
      warning comes with a required checkbox (FR-35).
    - The save bar has a note, Discard changes, and Save. Save goes through `useConfirm` and says "Saving
      starts one paid AI review of the new wording." when a playbook covers it (FR-13). It sends
      `expected_version` and `applied_findings`.
    - On 409 conflict a `MessageBar` names who saved, with "Compare with their version": both texts read
      only, side by side, with "Load theirs into the editor" and "Keep mine" (the next save then uses the
      new expected_version) (FR-59).
    - Jump-to selects the located range with `setSelectionRange` and scrolls it into view (FR-24).
    - P2 Word-based: a read-only `WordingView` with chips and `<mark>` highlight, the FR-40 explanation and
      the Download and Upload replacement buttons.
  - `_check-panel.tsx`: the Checks tab.
    - The FR-10 statements and the status explanation, the results-vary explanation ("Identical wording
      has given different results: N checks found findings, M found none.") and the last completed check.
    - **Check now (1 paid AI review)**: with unsaved edits it reads "Check the unsaved wording", which
      checks the editor text (FR-16). **Try again** appears when the status is `couldnt_check`. Both need
      `playbook:run`.
    - The shown result is the selected check, by default `decided_by` or the person's own unsaved check.
      It is labelled "Unsaved wording" or "Older wording (version n)" when it applies.
    - A finding card shows the clause, a severity `Badge` in words, "Needs approval", the issue, the
      quoted passage, and the suggested wording (FR-23).
      - **Show in template** is disabled and says "Couldn't locate this passage in the current wording"
        when the passage is not found (FR-24).
      - **Apply suggestion** appears only when `canApplyFinding(...)` holds (FR-25). It replaces the range
        in the editor as an unsaved change and records `{check_id, finding_index}`; Discard clears both.
    - A check history `Table` (FR-18) shows when, trigger, by ("Priya Shah" / "System, for Priya Shah"),
      template version, playbook version and outcome. A row opens its findings. While a check is queued
      or running it polls `checkDetail` every 3 s.
  - `_version-history.tsx`: the History tab (FR-57/58).
    - Each version shows its number, source label (P2: file name with Download original; Scan with "text
      confirmed by X at T"), who and when, the note, the applied suggestions, and outcome chips for its
      checks.
    - Expanding shows the wording read only, with chips.
    - **Load this version back** calls the restore endpoint after a `useConfirm` that mentions the paid
      review (`playbook:update`).
  - `_check-all.tsx`: a `Modal`: "This runs N paid AI reviews, one for each of these templates: … Templates
    already being checked or with no playbook are left out." Confirm then shows a progress `MessageBar`:
    "Check all: k of N finished", with failures named. It is computed from the list statuses of the
    started keys (FR-17).
  - `_leave-guard.ts`: `useLeaveGuard(message: string | null)`.
    - Adds `beforeunload` while a message is set (refresh and close).
    - Captures clicks on same-origin `<a href>` (the AegisRail, breadcrumbs and links), calls
      `preventDefault`, asks through `useConfirm`, then calls `router.push`.
    - **Back button** (owner answer, history-entry trap): when work becomes unsaved it pushes one sentinel
      entry (`history.pushState({tplGuard: true}, "")`). A `popstate` while still unsaved pushes the
      sentinel again (so the page stays) and asks. If the person confirms leaving, the hook disarms and
      calls `history.go(-2)`. When the work is saved or discarded, the hook removes its own sentinel
      (`history.back()` with the listener disarmed).
    - Switching template inside the page asks through the same confirm. Messages: "You have unsaved changes
      to this template." and in P2 "The upload isn't finished. The recognised text will be lost." (FR-61).
  - P2 `_upload-flow.tsx`: a `Modal size="xl"` used for Replace wording and for New type, with these steps:
    1. Choose a file (`<input type="file" accept=".docx,.pdf">`, analyze).
    2. Scan only: an agreement step naming Reducto, the page estimate and "paid", with Agree and recognise
       or Cancel (FR-30). Then `_scan-review.tsx`.
    3. "What was read": the paragraphs.
    4. `_field-marker.tsx`.
    5. Preview: PDF and scan as text with the FR-31 notice; Word through `_docx-preview.tsx`; the fields
       used; the missing-party acknowledgement.
    6. Confirm ("starts one paid AI review").
    Cancel from any step after OCR asks (with the leave-guard message), then calls `cancelUpload`.
    Nothing is sent to `upload` or `create` before the last step.
  - P2 `_scan-review.tsx`: the local PDF in a native `<iframe src={objectURL}#page=n>`, beside one textarea
    per page. Empty pages are listed. **Confirm the recognised text is correct** is its own step and calls
    `confirmText` (FR-71). Marking, preview and confirm stay disabled until then.
  - P2 `_field-marker.tsx`:
    - The fields found (FR-32), and the problems with their fixes.
    - Mark by selecting text inside one paragraph, or by the keyboard-first "Find text to mark" input:
      choose a field from a `Select` showing the plain name, what it fills and an example, and tick
      "Apply to every identical occurrence (n)" (FR-33).
    - Marked spans show as chips; clicking one unmarks it.
    - It sends `TemplateMark[]`.
  - P2 `_docx-preview.tsx`: a dynamic `import("docx-preview")`, `renderAsync` into the `contentDocument` of an
    `<iframe sandbox="allow-same-origin">` with no `allow-scripts` and no `allow-popups`. Script or
    `javascript:` content in a docx cannot run (docx-preview had a stored-XSS history).
  - P2 `_new-type-modal.tsx`:
    - Name.
    - Binding `Select` from `options()`. Held bindings are disabled and show "drafts from {holder}".
    - Playbook `Select` of this org's published playbooks, preselected to `suggested_playbook`, with a
      hint: "This playbook is locked to the template: its checks and every contract drafted from it are
      reviewed by it."
    - Wording: "Type or paste", which reuses `_wording-editor`'s editor, or "Upload a file", which embeds
      `_upload-flow` steps.
    - Confirm, which posts `create`.
- `src/app/(app)/intake/_request-overview.tsx` (P1): the `draft` mutation's `onSuccess` keeps
  `template_notice` in state and shows it as a warning `MessageBar` at the top of the overview. The draft
  itself is not delayed (FR-47).
- `src/lib/templates.ts` (pure, unit-tested): `toEditorText`, `toBodyText`, `wordingProblems` (mirrors
  `check_body`'s cases), `missingPartyFields`, `locatePassage(text, passage) → {start, end, count}`
  (exact first, then whitespace-insensitive), `canApplyFinding({check, finding, editorText,
  currentVersion, currentSha, editable, canChange})`, `templatePermissions(user) → {canView, canRun,
  canChange}` (strict: `permissions.includes(p) || includes("*")`; owner answer, see decisions),
  `statusTone(state, severity)`, `needsAttention(state)` (has_findings, out_of_date, partly_checked,
  couldnt_check, never_checked), and in P2 `paragraphsOf(text)`, `occurrences(paragraphs, passage)` and
  `applyMarksPreview`.
  `canApplyFinding` is true only when all of these hold:
  - the template is editable and the person may change it;
  - `suggestion_is_wording` is true;
  - the check is a completed saved check of the current saved version and sha, or the person's unsaved
    check made since the last load;
  - `locatePassage(toEditorText(quote_template ?? quote), editorText).count === 1`.
- New shared primitives in `src/components/ui.tsx`: **none**. Imports used, all checked as exported:
  `Button, Badge, Modal, useConfirm, MessageBar, LoadError, EmptyState, SkeletonRows, Tabs, Select, Input,
  Field, Table, THead, TH, TR, TD, Spinner`. Icons come from `lucide-react`, toasts from `useToast`.
  There is no `bg-white` and no hard-coded colour: the scoped CSS keeps the page's existing theme variables.
- Demo mode: none. `src/lib/demo.ts` no longer exists and the page has no demo path today.

## Test strategy

The CI gates stay green throughout: `ruff check .` → `alembic upgrade head` (0074 then 0075, single
head) → `pytest -q`. On the frontend: `npm run typecheck` and `npm run test` (never `npm run lint`). Tests
run with every `MOCK_*` on.

**What the mocked review returns.** Mock Claude's `return_playbook_review` returns no deviations, so an
unpatched check is **Passes**. Status tests monkeypatch `ai_controller.run_structured_skill` with the
existing `_FakeReview` (from `test_template_check.py`): `PlaybookReviewOutput(deviations=[…])` for Has
findings, a list-varying fake for results vary, `HTTPException(429)` for the cost cap, and
`RuntimeError("sk-secret…")` for AI unavailable. Jobs are not run by Celery in tests: conftest uses the
in-memory broker. Tests call `asyncio.run(checks.run_check_job(db, job))` on the test session. Long text
for Partly checked comes from patching `MAX_FULL_TEXT_CHARS` low. Reducto is patched with
`monkeypatch.setattr(reducto_client, "extract_text", …)`, because the mock returns empty text, which is the
FR-74 path. Word fixtures are **generated in the test with python-docx** (styles, numbering, a table, a
footer with a code, `{counterparty}` split across three runs with different formatting, "Globex
Corporation" × 12 split across runs). No binary fixture is committed.

Each test carries a docstring naming the failure mode it guards (repo convention).

Backend (pytest):
- `test_template_status.py` (P1) covers the eight-state table row by row:
  - each state, and that exactly one is returned;
  - fallback and low severity count as findings;
  - truncated is never Passes;
  - a failed check leaves the earlier completed check viewable and the status is not Passes;
  - Out of date says wording / playbook / both, and an edited default file makes version 0 Out of date;
  - results vary only for the current pair, never from unsaved checks, and gone after the wording or
    playbook changes;
  - the list endpoint shape, 403 without `playbook:read`, and 404 for another org's key.
- `test_template_check_jobs.py` (P1):
  - Single flight: a second save while running gives one queued check; a third save supersedes the queued
    one; never two queued (also tested at the DB level by inserting a duplicate and expecting
    IntegrityError).
  - The same wording queued is deduplicated.
  - Unsaved while one is queued gives 409.
  - The worker: completed, the reasons for failed (no rules, no playbook, unfillable, cost cap 429,
    AI error with no secret leaked), `attempt_count > 1` gives interrupted with no AI call, and a
    superseded check is a no-op.
  - The next queued check is dispatched after finish.
  - The job is FAILED on a failed check and nothing re-dispatches it.
  - Automatic triggers are attributed on behalf (audit metadata).
  - Publish checks only the templates that playbook reviews, none for another playbook's templates and
    (P2) none for a retired one.
  - The seed-library hook works.
  - No playbook means no check row and no `AISkillRun`.
  - Check all starts exactly N, skips checking and no-playbook templates, carries on past a failure, and
    writes the `check_all_started` audit.
  - 403 for `playbook:read`-only on `/checks` and `/check-all`.
- `test_template_check.py` (P1 update). The invariants stay pinned through the job:
  - the text sent equals `_bare_draft(key, body)`;
  - unsaved text is sent;
  - consultancy has 30 days;
  - unknown placeholder gives 422 with no AI call;
  - no playbook gives 409;
  - unknown key gives 404;
  - no run, deviation or contract is created;
  - the real controller writes an `AISkillRun` with no contract (now `resource_type="drafting_template_check"`).
  The tests of the old synchronous 200 response are rewritten to the 202 plus job flow.
- `test_drafting_templates.py` (P1 update):
  - responses are `TemplateMutationResult`;
  - `expected_version` stale gives 409 with the exact message;
  - missing parties gives 422 unless acknowledged;
  - `applied_findings` is stored and audited, and a foreign check id gives 422;
  - restore makes a new version and the earlier ones are unchanged;
  - reset on the default makes no version;
  - the check is requested after save, reset and restore;
  - 403 for a legal-reviewer-like role (read + run) on every change endpoint, and the permission table
    is the same before and after (AC-70).
- `test_template_drafting_record.py` (P1):
  - each status gives the right notice text;
  - the draft is created in every state;
  - metadata, audit and contract timeline name the template, version and status (AC-42);
  - a workflow-started `clm_draft` writes the notice into the history;
  - Passes gives no notice;
  - drafts are unchanged after edit, reset or publish (FR-45);
  - check counts, contracts, runs and deviations do not change from checks (FR-21);
  - the draft carries `review_playbook_id` = the template's pin, set before the AI jobs are dispatched.
- `test_playbook_pin.py` (P1, D-11 / FR-51). Each case's docstring names the takeover it guards.
  - **Locked through publishing.** Draft an MSA, then publish a better-named playbook ("Master Services
    Agreement Standard Playbook"). Then:
    - `auto_review_contract` still runs the pinned playbook;
    - `contract_risk` / `_review_runs` count only pinned-playbook runs;
    - "Retry review" (`contracts/auto_review.py`) targets the pin;
    - the template is not re-checked and its status keeps the pin.
  - The same holds for a counterparty revision of the drafted contract.
  - **Lazy pin:**
    - an unpinned shipped type is pinned by today's name match at its first check request, first draft
      and matching publish, with the `playbook_pinned` audit;
    - GET list and status never write a row;
    - two concurrent first pins end with one pin.
  - **Pinned playbook deleted or without a published version:**
    - status is No playbook with the reason;
    - a manual check gives 409, and no check row or AI call;
    - drafting still works with the notice, and the draft's review reports no playbook;
    - no fallback to another playbook.
  - **Change playbook:**
    - 403 for read + run only;
    - 422 for a playbook of another org, deleted, or unpublished;
    - audit before/after;
    - a check starts with `playbook_changed`, and status is Out of date "playbook changed" until it ends;
    - contracts already drafted keep their old `review_playbook_id` and band (FR-45).
  - **Uploads unchanged:** an uploaded contract (`review_playbook_id` NULL) is reviewed and banded by the
    name match, so `playbook_for_contract == pick_playbook_for_contract(contract_type)`. The existing
    name-pick suites (`test_playbook_library.py`, `test_one_risk_signal.py`, `test_risk_consumers.py`,
    `test_current_findings.py`, `test_nda_fast_lane.py`, `test_carry_decisions.py`,
    `test_interrupted_review.py`, `test_issue_fix_drafting.py`) run unmodified as the regression proof.
- `test_docx_fields.py` (P2):
  - codes split across runs are found and filled, keeping the first run's `rPr`;
  - marking "Globex Corporation" × 12 across runs;
  - literal braces;
  - unknown and unpaired codes;
  - codes in a text box or footnote are problems;
  - an empty value leaves no empty paragraph and no double space, and the `sectPr` paragraph is kept;
  - quotes, braces and non-ASCII values;
  - header and footer are filled;
  - a mark across a tab is refused;
  - tracked changes and comments are refused with the messages;
  - `content_hash` is stable across re-zipping;
  - output parts other than the changed ones are byte-identical;
  - XXE payload not resolved, zip bomb refused.
- `test_template_uploads.py` (P2):
  - analyze for docx, PDF and scan, and nothing stored (count `StorageObject` before and after);
  - refusals: image or `.doc`, mismatch, oversize (patch the limit), protected docx, protected PDF,
    damaged, empty, no text, with an audit row for the 413/415/AV refusals;
  - recognize: agreed audit before the call, failure gives 502 and no retry (the fake is called once),
    no text gives 422 and nothing kept, not set up gives 503;
  - confirm-text token: wrong user, wrong text sha or expired gives 422 on confirm;
  - an unconfirmed scan cannot be saved;
  - confirm makes a version with source, file ids, OCR fields and fields;
  - an identical upload makes no version and no check;
  - an exception after the store deletes the bytes (patch `_add` to raise, assert `delete_bytes_permanently`
    is called);
  - download and re-upload round trip with no re-marking (AC-36);
  - `files/{id}` is scoped to the template and org;
  - FR-40: PUT on a Word-based template gives 409.
- `test_template_types.py` (P2):
  - create with text and with a file;
  - nothing created on any refusal (count rows);
  - binding held gives 409 naming the holder;
  - a name already taken by an active type gives 409, and a blank or over-long name gives 422;
  - unpublished, deleted and other-org playbooks are not offered and are refused (422, FR-51's refusal);
  - a chosen playbook that does **not** match by name is accepted and pinned, and its drafts are reviewed
    by it (AC-46 under D-11);
  - the first check starts, and drafting works before it finishes;
  - Selling to a customer drafts from the Customer type, and with none it resolves to None;
  - retire stops drafting and automatic checks and leaves the history readable;
  - reactivate gives 409 when the binding is taken, otherwise it is active and checked;
  - `resolve_doc_type(db=)` at the workflow call sites.
- `test_workflow_engine_consistency.py` (P2 update):
  - a **form** request whose kind has no active template skips its `clm_draft` step with the note and goes
    on to the next step (no draft, no AI call): no Customer template, and a retired NDA;
  - with an active Customer type the Customer workflow's draft step drafts it (template mode);
  - the existing non-form "drafted fresh" case stays as is (custom path).
- `test_contract_workflow_library.py` (P2 update):
  - every existing routing case and `test_every_form_and_kind_has_a_workflow` unchanged (still exactly one
    workflow per form and kind);
  - new `test_customer_workflows_draft_on_our_paper`: both Customer workflows start with a `clm_draft` step
    in stage `drafting`, with `cond` paper = "Our template";
  - `stages.problems(...)` is empty for every `CONTRACT_FLOWS` entry.
- `test_migration_0075_customer_draft_step.py` (P2, new; owned by backend-dev). It loads the revision with
  `importlib` from `alembic/versions/0075_template_uploads.py` and runs its two data functions on the test
  connection inside a rolled-back transaction.
  - **Upgrade:** an unedited builtin "Customer — standard" (built from `OLD_STEPS` with fresh ids and the
    org's team id, `version = 1`) gets the draft step first, with that org's legal_counsel `team_id`, and
    `version = 2`. Its other step ids are unchanged.
  - **Edited workflows are left alone:** one with `version = 3`, and one at `version = 1` with a changed
    step, are untouched.
  - **Other orgs:** a second org's rows get their own team id.
  - **Idempotent:** running the upgrade twice changes nothing more.
  - **Downgrade:** restores the unedited one to its exact original steps and `version = 1`, and leaves an
    edited-after-upgrade one (`version = 3`) alone.
  - **Drift guard:** normalized, `[NEW_STEP] + OLD_STEPS[name]` equals what `contract_library.py` now defines
    for both Customer workflows, so the migration inserts exactly the library's step.
  - **Offline:** `alembic upgrade 0074_template_sync:0075_template_uploads --sql` and the matching downgrade
    still produce the schema SQL (the `test_notice_deadlines.py` 0072 pattern).
- `test_word_template_drafts.py` (P3):
  - the draft is a `.docx`, its `StorageObject` mime is DOCX and its snapshot came from the reader;
  - headings, numbering, table and footer formatting are preserved (compare `styles.xml`, `numbering.xml`
    and footer part bytes, and that the footer value is filled);
  - no `{name}`, `{{` or `}}` left;
  - every filled body paragraph appears in the snapshot text in order;
  - the check text equals the draft's snapshot text for the same values;
  - text, PDF and scan templates still draft `.txt`, byte-identical to the Phase 1 output (FR-44).

Frontend (vitest):
- `src/lib/templates.test.ts`:
  - `toEditorText`/`toBodyText` round trip, including literal braces and unknown codes;
  - pasted codes become chips;
  - `wordingProblems` on the same cases as `check_body`;
  - `locatePassage` for 0, 1 and 2 occurrences and whitespace differences;
  - `canApplyFinding` truth table (Word-based, advice, older version, twice, zero times, no permission);
  - `templatePermissions` for a legal reviewer;
  - `needsAttention`;
  - P2 occurrences across paragraphs.
- `templates/page.test.tsx`:
  - loading, empty and error with retry;
  - every status shows its words;
  - counts and the Needs attention filter;
  - Check now sends `undefined` when clean and the body when dirty, and the unsaved result is labelled;
  - Try again on Couldn't check;
  - Apply suggestion is offered once and not when the passage appears twice, and Discard clears it;
  - the save confirm mentions the paid review;
  - the 409 conflict message and compare view;
  - switching template while dirty asks; Back (a `popstate`) while dirty asks and stays;
  - Change playbook is shown only with `playbook:update`, its confirm says old drafts keep their playbook,
    and it calls `setPlaybook`; "matched by name" or "locked" shows from `playbook.pinned`;
  - a viewer (read + run) sees no change controls and sees the reason;
  - Check all states N and calls nothing before confirm.
- `intake/_request-overview.test.tsx`: a `template_notice` from `draftContract` is shown, and none is
  shown when it is null.
- P2 `_upload-flow.test.tsx`:
  - the scan agreement comes before `recognize`;
  - marking and preview stay disabled until `confirmText`;
  - the FR-31 notice for PDF;
  - cancel after OCR asks and calls `cancelUpload`, and never `upload`;
  - apply to all marks n occurrences.
- P2 `_new-type-modal.test.tsx`: held bindings are disabled, `suggested_playbook` is preselected but any
  listed playbook can be chosen, the server's 422 playbook message is shown, and nothing is posted before
  confirm.

Acceptance verification (/verify, `verification.md`):
- AC-1–AC-35, 38–54, 63–75 are evidenced by the named pytest and vitest cases above. qa-engineer maps
  each AC to a test id.
- AC-11, 15, 17, 55–60 and 69 are also walked on the dev stack (`docker compose up -d`, admin plus a
  user with only `playbook:read` + `playbook:run`). This includes closing the tab during a check (AC-17)
  and keyboard-only operation (AC-60).
- **AC-37 and AC-36 also need a manual check in real Microsoft Word.** Open the generated draft and the
  downloaded template with `open -g` in Word. python-docx-valid files have been rejected by Word in this
  repo before (memory "Verify .docx in real Word"). Never touch `~/Library/Containers`.
- AC-19 is checked by counting rows before and after a Check all on the stack.
- AC-70 is checked by diffing the `permission` table before and after.

## Risks & decisions

**Owner decisions (2026-10-15).** These continue the spec's D-1 to D-10.
- **D-11: the playbook is pinned per template (user, 2026-10-15).** A template type, shipped or new,
  records the playbook that reviews it. Its checks use that playbook, and so does every contract drafted
  from it, for both the automatic review and the risk band. A later, better-named playbook never takes
  over. Design: "FR-51 and D-11" in Component design. This replaces the earlier plan's name-match
  validation.
- **D-12: Customer requests draft automatically (user, 2026-10-15).** Both Customer starter workflows get
  a "Prepare draft" step (Phase 2). Design: "Workflows (P2)" in Component design.
- **D-13: existing orgs' unedited Customer workflows get the draft step by migration (orchestrator, from
  the user's D-12 answer, 2026-10-15).** This is the data step in `0075_template_uploads`; see Database
  schema.
- Answers to the plan's open questions:
  - Page permissions use a **strict page-level check** (`templatePermissions`), not the stubbed `can()`.
  - The **Back button is trapped** with a history sentinel while work is unsaved.
  - New types bind only to the 6 New-agreement kinds and the SoW and DPA forms, not "Something else".
  - An abandoned scan upload closed with the tab is not audited as cancelled; the trail shows
    `ocr_agreed` with no `upload_confirmed`.
  - The OCR estimate is the page count only.
  - FR-39/FR-40 are in Phase 2.
  - Everything the spec asks for stays in scope, including scans, the text-template Word download and
    queued unsaved checks.

**Decisions (with the rejected alternative)**
- **Checks run in the background and are recorded, including unsaved checks.** FR-18/19 require records
  that survive leaving the page. The synchronous `POST /check` is removed. *Rejected:* keeping the
  synchronous call for "Check now". It would be the one check that is lost if the tab closes, and the
  only path outside single flight.
- **The pinned playbook's version is resolved when the check runs, not when it is queued.** A queued
  check therefore already covers a later publication of the pinned playbook, or a pin change, so either
  deduplicates against it. The check records what it actually used. *Rejected:* pinning the version at
  queue time, which can run against an outdated version and needs re-queueing.
- **Single flight uses an advisory xact lock per template plus two partial unique indexes.** The lock
  makes "supersede the waiting one, queue the new one" atomic, and the indexes make "never two waiting"
  a database fact. Lock order: a mutation commits (with its audit) **before** calling `request_check`.
  `request_check` takes the template lock and then the audit lock. The worker writes its audit without the
  template lock, and `_start_next` takes the template lock with no audit. No transaction holds the audit
  lock while waiting for a template lock, so there is no cycle (CLAUDE.md audit-lock freeze).
  *Rejected:* `SELECT … FOR UPDATE` on a row, because a shipped template has no row until it is pinned.
  The lazy pin (`ensure_pinned`) runs inside `request_check` after the template lock and writes its audit
  there, so the lock order is the same. In the draft path the pin commits on its own **before**
  `create_contract_from_upload`, so the audit lock is never held across text extraction.
- **No automatic retry anywhere (FR-20).** The handler swallows every exception, so Celery `autoretry_for`
  never fires. A reclaimed job (`attempt_count > 1`) fails as `interrupted` without calling the AI. A
  failed job is FAILED, which reclaim never re-dispatches. There is no beat task. The Claude client's own
  HTTP retries inside one call (`claude_max_retries`) and the Reducto SDK's transport retries count as
  part of one attempt: they are not new reviews. ponytail: a check whose worker dies three times is
  dead-lettered by reclaim and stays `running`. The upgrade path is a reclaim hook that fails the check.
- **The check text for text templates stays the rendered text.** Measured on 2026-10-15 over all 7
  defaults: the draft's snapshot text (Documents reader of the `.txt`) differs from `render_document` only
  in whitespace (a trailing newline; `\n\n\n` read as `\n\n` in the DPA). Keeping it keeps
  `test_template_check.py`'s "sends exactly what a bare request drafts" pin. For Word-based templates (P3)
  the check reads the reader's text of the filled file, because that is exactly what the draft's review
  reads.
- **D-11: the pin lives on the template and is copied onto each drafted contract as a column.** A single
  `playbook_for_contract(contract)` is used by `auto_review_contract` and `contracts/risk.py._review_runs`.
  Every review and risk consumer goes through those two, so the pin reaches all of them by changing two
  lines. *Rejected:*
  - The FR-46 metadata record as the carrier: mutable JSON, rewritten by many paths, on the hot path of
    approvals and authority.
  - Re-deriving the pin from the template at review time: an admin's later Change playbook would then
    silently move contracts already drafted, against FR-45.
  - Validating the name match only (the earlier plan): a better-named playbook would take over, which D-11
    forbids.
- **Shipped types are pinned lazily at first use, by today's name match.** The triggers are the first check
  request, the first draft, or a publish of the matching playbook. *Rejected:*
  - Pinning at release in a migration: the migration would have to import the app's name-matching code,
    and test organisations need the lazy path anyway.
  - Pinning on GET: writes in read paths.
  Until the first use, behaviour is exactly today's (A-11: release starts nothing).
- **No backfill of `review_playbook_id` for contracts drafted before this feature.** They keep the name
  match, like uploads. Backfilling would change which reviews count for their existing risk bands, which
  goes against FR-45.
- **A pinned playbook that is deleted or has no published version gives No playbook, never a fallback.**
  This follows the spec's edge case and D-11's "must not take over". Drafting still works with the notice
  (FR-47). The draft keeps the pin, so its reviews resume if the playbook is published again.
- **A drafted contract's pin does not follow later edits to its `contract_type`.** The contract was drafted
  from that template and is reviewed by that template's playbook (D-11). Changing a drafted contract's
  reviewing playbook is out of scope; see the risks.
- **New types bind only to a form or kind with a mapped contract type** (the 6 New-agreement kinds and the
  SoW and DPA forms; not "Something else" or the change and records forms, which never draft new paper).
  With D-11, FR-51's refusal is "the chosen playbook isn't a published playbook of this organisation". Any
  such playbook can be pinned, so the guarantee holds for every accepted choice, matched by name or not.
- **Template types are data by overlay, not seeding.** The 7 shipped types' facts live in code. A
  `drafting_template_type` row exists for new types, and for shipped types once pinned or retired. That row
  copies the binding so the partial unique indexes see it. A shipped type with no row is active and
  unpinned, and `create_type` and `reactivate` pre-check it. *Rejected:* materialising 7 rows per org on
  first read, which means writes in GET paths and rows that test organisations, created in rolled-back
  transactions, cannot hold. Also rejected: seeding in the migration plus `create_first_admin`, which is
  two mechanisms.
- **D-12: Customer drafting is a `clm_draft` step, with a form-request skip when no template exists.**
  - The step is skipped on "Their paper" by the existing step `cond` mechanism.
  - When the kind has no active template, it is skipped by one engine rule (form requests only), so
    FR-53's "until one exists … handled by Legal as today" and FR-54's retired kinds stay literal.
  - *Rejected:* letting the existing fallback write an AI "custom" draft for every Customer request in an
    org with no Customer template. That is a paid AI call per request and a contract Legal didn't ask for.
  - *Rejected:* a Customer-only config flag. The workflow builder can't set it, so a step an admin adds by
    hand would behave differently.
  - The other non-NDA kinds (MSA, Vendor, SaaS, Consultancy) keep their draft step without the paper
    condition. That is today's behaviour, left unchanged (see the risks).
- **Stateless upload flow.** analyze, preview and confirm each re-send the file; nothing is stored before
  the final confirmation. "Discard recognised text on cancel or interruption" (FR-72) is then true by
  construction. The confirmed scan text is bound by a signed, 24-hour token
  (`uid + file sha + text sha + confirmed_at`), so unconfirmed OCR text can never be saved, even by a
  crafted request. *Rejected:* a pending-upload table with a sweeper (a table, a job and expiry, all for
  data the spec says to discard).
- **OCR runs inline in the request.** nginx allows 300 s, and the person waits for the text anyway.
  ponytail: move it to a Celery job with polling if template scans start timing out.
- **Word field codes are visible plain `{name}` text, handled per paragraph across runs, written by
  zip + lxml.** See Component design. *Rejected:* content controls and MERGEFIELDs (not visible as codes,
  lost by other editors, and FR-32 needs plain codes anyway); python-docx `save()` (re-zips with fresh
  timestamps, so identical uploads would hash differently); a new templating dependency (docxtpl: Jinja
  syntax, a new dependency, and still run splitting to handle).
- **Word preview uses docx-preview (already installed) inside a sandboxed iframe.** Scan comparison uses
  the browser's own PDF viewer. *Rejected:* server-side conversion (LibreOffice is not in the image) and
  pdf.js page rendering code.
- **Retired types are read-only until reactivated.** FR-54 says a retired type is not checked
  automatically, and FR-13 says every wording change starts a check; read-only removes the conflict.
- **devtools seeding (`make seed`) gets no publish hook.** It is a dev bootstrap. A-11 says releases and
  seeding start no paid checks, and the dev container can hold real keys. The two production publish paths
  (`publish_playbook`, `POST /playbooks/seed-library`) both hook.
- **The editor notation is `«Plain name»` for known fields.** It is converted to `{name}` on save, check
  and preview; unknown `{x}` stays raw and is flagged. ponytail: a literal `«The other party»` typed into
  the wording would turn into a field. That is very rare in English contracts, and the preview shows it.
- **FR-61 guard** covers in-app links, switching template, refresh or close (`beforeunload`), and the
  browser Back button, trapped with a history sentinel while work is unsaved (owner answer).

**Risks**
- Real Word may reject or repair a rewritten package. Mitigation: only text nodes change, `ZipInfo` order
  and compression are preserved, plus the manual Word check (AC-36/37) and part byte-equality tests.
- Large check history makes the list slower, because each template reads its checks and the current pair.
  Mitigation: the index on (org_id, template_key, created_at), with at most about 10 templates.
- `serialize_request` gains one indexed query per request (P2 `_draftable(db, r)`), in line with its
  existing per-request lookups.
- `test_template_check.py`, `test_drafting_templates.py`, `test_workflow_engine_consistency.py` and
  `test_contract_workflow_library.py` change shape. Their invariants are kept and listed above. Reviewers
  should diff intent, not just pass or fail.
- **Riskier with D-11: the risk-band choke point changes.** `_review_runs` decides which reviews count for
  every contract's band, which approvals, authority limits, fast lanes and the workflow AI step all read.
  A bug there moves bands app-wide. Mitigations:
  - uploads (`review_playbook_id` NULL) take the exact old call, and the name-pick suites run unmodified;
  - `test_playbook_pin.py` covers the pinned path end to end.
- **Riskier with D-11: deleting or never re-publishing a pinned playbook now stops reviews of its drafts**
  instead of quietly switching playbook. Their band reads unknown, which exceeds every authority ceiling
  and holds the workflow AI step for a person. The remedy for new drafts is re-publish or Change playbook.
  Contracts already drafted keep their pin (FR-45): moving an existing contract to another playbook is
  not built. Follow-up if needed: an admin action on the contract.
- **Riskier with D-11: the first use locks today's name match.** If an org's playbook names currently pick
  the wrong playbook for a shipped template, that choice is locked at its first check or draft. It is the
  same playbook that reviews those drafts today, and the page shows it ("locked to this template") with
  Change playbook next to it.
- **D-13: the migration edits org-owned workflow rows.**
  - Guards: builtin, the two exact names, `version = 1`, normalized steps equal to the frozen originals,
    and every UPDATE keyed by id and org. In-flight runs are unaffected (pinned `WorkflowRun.steps`).
  - Residual: a Customer workflow that was edited, or seeded before step stages existed (no `stage` keys,
    so no match), does not get the step. It needs the release-note builder edit.
  - The change is not in `audit_log` (see Database schema for why, and how it is recorded instead).
- **D-12 leaves an existing inconsistency alone:** the MSA, Vendor, SaaS and Consultancy workflows still
  run their draft step on "Their paper" unless the contract already exists. That is today's behaviour, not
  changed here; the same `cond` would fix them in a follow-up.

**FR traceability** (each FR in exactly one phase; "→" means the component that delivers it)

| FR | Ph | Component |
|---|---|---|
| FR-1 | 1 | `service.list` → `DraftingTemplateSummary`; `_template-list.tsx` |
| FR-2 | 1 | `checks.template_status` (one state); `StatusBadge` |
| FR-3 | 1 | status rule 8 (Passes needs current pair, untruncated, 0 findings) |
| FR-4 | 1 | status rule 7; fallback/low count; count + highest severity |
| FR-5 | 1 | status rule 2; `active_check`; earlier result in Checks history with version labels |
| FR-6 | 1 | status rule 4; `changed` |
| FR-7 | 1 | status rule 6; `text_length`/`read_length` reason |
| FR-8 | 1 | status rule 5; error codes + messages; Try again (`trigger=retry`) |
| FR-9 | 1 | status rules 1/3 (unpinned with no match, or pinned playbook deleted or unpublished); `request_check` returns None, no row, no AI |
| FR-10 | 1 | page `MessageBar` + `_check-panel` statements; `last_completed` |
| FR-11 | 1 | `results_vary` from stored completed saved checks of the current pair |
| FR-12 | 1 | check text = wording filled with blank answers only; fixed copy |
| FR-13 | 1 | save/reset/restore → `request_check`; confirm copy (P2 upload/create/reactivate call the same; Change playbook too, `playbook_changed`) |
| FR-14 | 1 | `on_playbooks_published` (templates whose pin, or for unpinned ones whose name match, is the published playbook); hooks in `publish_playbook` + `seed_library` |
| FR-15 | 1 | single-flight rules + advisory lock + partial unique indexes |
| FR-16 | 1 | `POST /checks {body}`; unsaved rows excluded from status |
| FR-17 | 1 | `POST /check-all`; `_check-all.tsx` confirm + progress |
| FR-18 | 1 | `drafting_template_check` row; `GET /checks`; Checks history |
| FR-19 | 1 | Celery job `template_check` |
| FR-20 | 1 | 429 → `cost_cap`; no-retry guards; no beat task |
| FR-21 | 1 | `review_text` (no run/deviation/contract); test |
| FR-22 | 1 | same skill/prompt/rules/payload via `review_text`; same (pinned) playbook as the drafts |
| FR-23 | 1 | stored findings; finding card |
| FR-24 | 1 | `quote_template` + `locatePassage` + jump / "couldn't locate" |
| FR-25 | 1 | `canApplyFinding`; `applied_findings` on the version + audit |
| FR-26 | 2 | `_upload-flow.tsx`; stateless analyze → preview → `upload` |
| FR-27 | 2 | `read_template_upload` (allowlist, sniff, size, AV) + `upload_refused` audit |
| FR-28 | 2 | specific refusals (damaged, password, empty, no text) |
| FR-29 | 2 | `refuse_revisions_and_comments` |
| FR-30 | 2 | scan detection + agreement step + `recognize` (+ `ocr_agreed` audit) |
| FR-31 | 2 | PDF/scan notice in the preview/confirm step |
| FR-32 | 2 | `field_uses` code recognition (across runs) |
| FR-33 | 2 | `_field-marker.tsx` + `TemplateMark` + `apply_marks` |
| FR-34 | 2 | `check_body` with location + Word problems, for every entry way |
| FR-35 | 1 | `acknowledge_missing_parties` (save; reused by P2 upload/create) |
| FR-36 | 2 | `uploads/preview` + `preview-docx` + `_docx-preview.tsx` |
| FR-37 | 2 | unchanged detection (`content_hash` / body) → `changed:false` |
| FR-38 | 2 | storage + version file fields + `files/{id}` |
| FR-39 | 2 | `GET /download` (stored Word file or plain docx with codes); round trip via FR-32 |
| FR-40 | 2 | `editable=false`; PUT 409; read-only `WordingView` |
| FR-41 | 3 | Word draft via `docx_fields.fill` → `create_contract_from_upload` |
| FR-42 | 3 | `fill` formatting, empty-value and escaping rules |
| FR-43 | 3 | same intake path; check reads the reader's text of the filled file |
| FR-44 | 3 | text/PDF/scan sources keep `render_document` → `.txt` |
| FR-45 | 1 | drafts are snapshots, never re-rendered; test |
| FR-46 | 1 | `drafted_from_template` metadata + audit + contract timeline |
| FR-47 | 1 | `status_notice` → `template_notice` + request-page `MessageBar`; history for workflow drafts |
| FR-48 | 2 | `POST /drafting-templates` (`create_type`, chosen playbook pinned) + `_new-type-modal.tsx` |
| FR-49 | 2 | binding partial unique index + pre-check (409 names holder) |
| FR-50 | 2 | `options.playbooks` (published, org, P1 endpoint); name partial unique index |
| FR-51 | 1 | D-11 pin: `drafting_template_type.playbook_id` → checks; `contract.review_playbook_id` → `playbook_for_contract` in `auto_review_contract` + `risk._review_runs`; Change playbook; the refusal (unpublished or foreign playbook) is exercised by P2 `create_type` (AC-46 verified in P2) |
| FR-52 | 2 | `create_type` → wording rules + `request_check(type_created)`; draft allowed at once |
| FR-53 | 2 | `resolve_doc_type(db=)` → active Customer type; Draft button; D-12 Customer workflows' draft step (D-13: added to existing orgs' unedited ones by `0075`); the `clm_draft` form-request skip keeps "Legal as today" while no template exists |
| FR-54 | 2 | `retire` + resolver/automatic-check skips; the `clm_draft` skip sends a retired kind to Legal |
| FR-55 | 2 | `reactivate` (409 holder) + check |
| FR-56 | 1 | `reset` → version + check |
| FR-57 | 1 | `versions` with source and checks; synthetic v0; rows immutable |
| FR-58 | 1 | `restore` endpoint (P2 extends it to copy file refs and marks) |
| FR-59 | 1 | `expected_version` 409 + compare view (P2 uploads send it too) |
| FR-60 | 1 | page loading/empty/error states |
| FR-61 | 1 | `useLeaveGuard` + template-switch confirm (P2 upload flow uses it) |
| FR-62 | 1 | `«Label»` editor notation + chips everywhere wording is shown |
| FR-63 | 1 | status strip counts + Needs attention filter |
| FR-64 | 1 | `templatePermissions` gates controls + reason line |
| FR-65 | 1 | status in words + icons; buttons/tabs/Find-to-mark keyboard operable |
| FR-66 | 1 | `playbook:read` on every read route (P2 routes too) |
| FR-67 | 1 | `playbook:run` on `/checks`, `/check-all` |
| FR-68 | 1 | `playbook:update` on every change and upload-step route; no new permission |
| FR-69 | 1 | `org_id` filter on every query; file and playbook ownership checks |
| FR-70 | 1 | audit table above (P2 adds its rows) |
| FR-71 | 2 | `_scan-review.tsx` + `confirm-text` (separate step) |
| FR-72 | 2 | token required at confirm; stateless discard |
| FR-73 | 2 | 502/503 + new agreed step; no retry |
| FR-74 | 2 | no text → 422, nothing kept |
| FR-75 | 2 | `source="scan"`, `ocr_confirmed_*`, original file kept |

Counts: Phase 1 = 45, Phase 2 = 26, Phase 3 = 4 (total 75).

## Open questions

None. Every question is answered (D-11, D-12, D-13 and the owner answers under Risks & decisions).
