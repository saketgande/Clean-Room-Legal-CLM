# Task Breakdown: [FEATURE NAME]

Feature ID: [NNN-slug]
Plan: ./plan.md (must be APPROVED)
Created: [DATE]
Status: DRAFT   <!-- DRAFT | APPROVED — only the user approves -->

## Format

`- [ ] T00N [P] (agent) Description — dependsOn: T00X, T00Y — files: path/one, path/two`

- `[P]` = may run in parallel with other `[P]` tasks in the same wave (no shared files).
- `dependsOn: —` means no dependencies (wave 1).
- Every task maps to requirement(s) from spec.md, noted as `(FR-N)`.
- Two tasks that touch the same file must never carry `[P]` together — watch the
  shared hotspots: `backend/app/models.py`, `backend/app/jobs/tasks.py`,
  `frontend/src/lib/endpoints.ts`, `frontend/src/lib/types.ts`,
  `frontend/src/components/ui.tsx`.
- Migration tasks never run in parallel with each other (single Alembic head).

## Waves

Wave = all tasks whose dependencies are complete. Typical shape for this stack:

### Wave 1 — Foundation
- [ ] T001 (db-engineer) SQLAlchemy models + Alembic migration `00NN_slug` for [tables] (FR-1) — dependsOn: — — files: backend/app/<domain>/models.py, backend/app/models.py, backend/alembic/versions/00NN_slug.py

### Wave 2 — Parallel implementation
- [ ] T002 [P] (backend-dev) Pydantic schemas per plan.md contract (FR-1) — dependsOn: T001 — files: backend/app/<domain>/schemas.py
- [ ] T003 [P] (frontend-dev) TS interfaces + endpoint client additions from interface freeze (FR-2) — dependsOn: — — files: frontend/src/lib/types.ts, frontend/src/lib/endpoints.ts

### Wave 3 — Parallel build-out
- [ ] T004 [P] (backend-dev) Service + routes [list] with permission gates, org scoping, audit rows, pytest coverage (FR-1, FR-3) — dependsOn: T002 — files: backend/app/<domain>/service.py, backend/app/<domain>/routes.py, backend/tests/test_<domain>_x.py
- [ ] T005 [P] (frontend-dev) Page + components wired to the typed client (FR-2) — dependsOn: T003 — files: frontend/src/app/(app)/<feature>/page.tsx, frontend/src/app/(app)/<feature>/_*.tsx

### Wave 4 — Integration & QA
- [ ] T006 (qa-engineer) Verify AC-1..AC-N, write verification.md — dependsOn: T004, T005 — files: specs/NNN-slug/verification.md

## Task detail notes

[Optional: per-task elaboration when the one-liner isn't enough — inputs, gotchas,
links to plan.md sections.]
