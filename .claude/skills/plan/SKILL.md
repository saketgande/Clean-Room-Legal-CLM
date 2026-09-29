---
name: plan
description: Phase 2 of the spec-driven workflow. Gate-check the approved spec, then produce specs/NNN-slug/plan.md via the architect agent and get it approved.
---

# /plan — create the implementation plan

Input: optionally a feature ID (`/plan 003`). If omitted, use the most recent
`specs/NNN-*` directory; if ambiguous, ask.

## Gate check (hard requirement)

`specs/NNN-slug/spec.md` must exist and contain the line `Status: APPROVED`.
If not: refuse, explain the gate, and point the user to `/specify` (or to approving
the existing draft). Do not proceed "just this once".

## Procedure

1. **Regenerate the repo knowledge graph**: `python scripts/build_repo_graph.py`
   (or invoke `/repo-graph`). Cheap, always run — don't staleness-check. Surface any
   warnings briefly but don't block the gate on them.
2. **Spawn the `architect` agent** (foreground) with: the feature directory, an
   instruction to read the spec, `.claude/rules/constitution.md`,
   `specs/_graph/index.md`, and the existing codebase before designing (the closest
   existing domain module is the template), and to follow
   `.claude/templates/plan-template.md`.
3. **Sanity-check the output** yourself: interface freeze complete (API shapes with
   permission strings, DB schema with org_id scoping, TS interfaces — all concrete,
   no TBDs)? Migration plan names a revision ID ≤ 32 chars on the current head?
   Path mapping filled with this feature's real files? Every FR traceable? If gaps,
   send the architect back with specifics.
4. **Surface open questions** from the architect to the user and resolve them.
5. **Request approval.** Summarize the design for the user: endpoints table, tables
   created/changed, page/component tree, key decisions. On an explicit yes, set
   `Status: APPROVED` in plan.md.
6. **Tell the user the next step is `/tasks`.**

## Rules

- The interface freeze is the crux — an approved plan with a vague contract will
  break parallel implementation. Hold the line on concreteness.
