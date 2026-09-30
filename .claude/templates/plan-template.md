# Implementation Plan: [FEATURE NAME]

Feature ID: [NNN-slug]
Spec: ./spec.md (must be APPROVED)
Created: [DATE]
Status: DRAFT   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

[Short prose + optional diagram: how the pieces fit. Which existing domain modules
are touched, what's new. Name the existing module used as the convention template
(e.g. "mirrors app/trademarks/").]

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | [e.g. backend/app/renewals/models.py, backend/app/models.py (registry entry), backend/alembic/versions/00NN_slug.py] |
| backend-dev | [e.g. backend/app/renewals/routes.py, service.py, schemas.py, backend/app/jobs/tasks.py (one task), backend/tests/test_renewals_x.py] |
| frontend-dev | [e.g. frontend/src/app/(app)/renewals/page.tsx, _reminder-modal.tsx, src/lib/endpoints.ts (renewalsApi additions), src/lib/types.ts (interfaces)] |
| qa-engineer | specs/NNN-slug/verification.md |

Shared-hotspot note: list every task that touches `app/models.py`, `app/jobs/tasks.py`,
`src/lib/endpoints.ts`, `src/lib/types.ts`, or `src/components/ui.tsx` — those files
serialize across tasks.

## Interface freeze (contract-first — fixed before implementation starts)

### API contract

| Method | Path | Permission | Request body | Response (200/201) | Errors |
|---|---|---|---|---|---|
| POST | /api/v1/... | `resource:action` | `{...}` | `{...}` | 404, 409, 422 |

Full request/response JSON shapes:

```json
// POST /api/v1/... request
{}
// response
{}
```

Audit actions written by each mutation: [e.g. `renewals.reminder_created` on POST ...]

### Database schema

| Table | Column | Type | Constraints |
|---|---|---|---|
| ... | id | varchar (uuid via new_uuid()) | PK |
| ... | org_id | varchar | FK organization.id, indexed |

Migration: revision ID `00NN_short_slug` (≤ 32 chars) on current head `[current head]`,
with working downgrade. [Note pgvector/index specifics if any.]

### Frontend TypeScript models (added to src/lib/types.ts)

```ts
// Mirrors the API contract exactly
export interface ... {}
```

### Endpoint client additions (src/lib/endpoints.ts)

- `featureApi.method(args) → Promise<Type>` — [maps to METHOD /api/v1/...]

## Component design

### Backend
- Models: [file] — tables [...]
- Schemas: [file] — [...]
- Service: [file] — functions [...] (org scoping + audit noted per function)
- Router: [file] — endpoints [...] (permission per endpoint)
- Celery: [task name in app/jobs/tasks.py, schedule if beat-driven — or "none"]
- Settings: [new Settings fields + validate_runtime_settings() entries — or "none"]

### Frontend (Next.js page/component tree)
- `src/app/(app)/<feature>/page.tsx` — [responsibility]
  - `_child-component.tsx` — [responsibility]
- New shared primitives in `src/components/ui.tsx`: [name + variants — or "none";
  confirm every primitive the components import already exists in ui.tsx]
- Demo-mode behavior: [what the page shows without a backend]

## Test strategy

- Backend pytest: [files, cases — happy + failure + permission 403]
- Frontend vitest: [files, cases]
- Acceptance verification: [how each AC-N will be evidenced in /verify]

## Risks & decisions

- [Decision made + rationale; alternatives rejected and why.]
- FR traceability: [FR-1 → component X; FR-2 → ...]
