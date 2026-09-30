---
name: tasks
description: Phase 3 of the spec-driven workflow. Gate-check the approved plan, then break it into a dependency-ordered, parallel-safe task list (tasks.md) and get it approved.
---

# /tasks — create the task breakdown

Input: optionally a feature ID. Default: most recent `specs/NNN-*`; ask if ambiguous.

## Gate check (hard requirement)

`specs/NNN-slug/plan.md` must exist and contain `Status: APPROVED`. Otherwise refuse
and point to `/plan`.

## Procedure

Produce `specs/NNN-slug/tasks.md` yourself (no subagent needed) following
`.claude/templates/tasks-template.md`:

1. Derive tasks from plan.md's component design. Each task line carries:
   `T00N`, optional `[P]`, owning agent, description with `(FR-N)` traceability,
   `dependsOn`, and the files it will touch.
2. **Parallel-safety check** — the invariants:
   - Tasks sharing any file are never both `[P]` in the same wave. Watch the shared
     hotspots in this repo: `backend/app/models.py` (registry), `app/jobs/tasks.py`,
     `src/lib/endpoints.ts`, `src/lib/types.ts`, `src/components/ui.tsx` — tasks
     touching the same hotspot serialize.
   - Every task's files fall inside its agent's paths from plan.md's path mapping.
   - Migration tasks are never parallel with each other (single-head discipline).
3. Group into waves (a wave = tasks whose dependencies are all in earlier waves).
   Typical shape for this stack: models + migration → backend schemas ∥ frontend
   types/scaffolding → backend service + routes ∥ frontend page/components → QA
   verification.
4. Right-size tasks: one agent-session each (roughly 30–90 min of focused work);
   split anything bigger.
5. **Request approval.** Show the wave structure and per-agent workload. On an
   explicit yes, set `Status: APPROVED`.
6. **Tell the user the next step is `/implement`.**
