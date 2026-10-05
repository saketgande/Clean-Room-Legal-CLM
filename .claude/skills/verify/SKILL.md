---
name: verify
description: Phase 5 of the spec-driven workflow. Run the full test suite, spawn the read-only code-reviewer, and produce verification.md with pass/fail per acceptance criterion.
---

# /verify — prove the feature matches the spec

Input: optionally a feature ID. Default: most recent `specs/NNN-*`; ask if ambiguous.

## Gate check

All tasks in `specs/NNN-slug/tasks.md` should be complete (check the status board).
If some aren't, tell the user which — offer to verify anyway (partial verification)
only if they explicitly want it.

## Procedure

1. **Run the suites** and capture summary output — these mirror CI exactly:
   - Backend (from `backend/`): `python -m ruff check .` then `pytest -q`.
   - Migrations: `alembic upgrade head` against the compose Postgres if the stack is
     up (`docker compose up -d postgres` is enough); at minimum verify the revision
     graph has a single head.
   - Frontend (from `frontend/`): `npm run typecheck` then `npm run test`.
     Never `npm run lint` — interactive wizard trap in this repo.
2. **Spawn `qa-engineer`** (if AC verification tasks weren't already done during
   /implement) to execute acceptance-criteria checks and write
   `specs/NNN-slug/verification.md`. For live end-to-end checks the stack runs with
   `docker compose up -d` (API :8000/healthz; frontend :3000 or :3001 per override).
3. **Spawn `code-reviewer`** (read-only) on the implementation diff with pointers to
   spec.md, plan.md, and the constitution.
4. **Consolidate** into `verification.md`: AC table (PASS/FAIL/NOT-RUN), test-suite
   summaries with the exact commands, reviewer verdict and findings.
5. **Report honestly.** Failures and REQUEST CHANGES findings are presented as-is —
   never soften "2 tests failing" into "mostly passing". For each defect, propose a
   follow-up: new task routed to the owning agent (preferred) or user decision.

A feature is DONE only when: all AC pass, suites green (ruff + pytest + typecheck +
vitest + single-head migrations), reviewer verdict is APPROVE or APPROVE WITH NITS
(nits listed for the user to accept or queue).
