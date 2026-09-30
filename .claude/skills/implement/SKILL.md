---
name: implement
description: Phase 4 of the spec-driven workflow. Gate-check the approved task list, then execute it — launching specialized agents in parallel waves with live status tracking.
---

# /implement — orchestrate the build

Input: optionally a feature ID. Default: most recent `specs/NNN-*`; ask if ambiguous.

## Gate check (hard requirement)

`specs/NNN-slug/tasks.md` must exist and contain `Status: APPROVED`. Otherwise refuse
and point to `/tasks`.

## Role discipline

You are the **orchestrator**. You do not write feature code in this phase — every
code task goes to its owning agent. Your jobs: launch, monitor, verify, integrate,
report.

## Setup

1. Regenerate the repo knowledge graph: `python scripts/build_repo_graph.py` (or
   invoke `/repo-graph`). Cheap, always run — don't staleness-check.
2. Parse tasks.md into a task graph: ID, agent, `[P]`, dependsOn, files.
3. Create `specs/NNN-slug/status/` and `board.md` from
   `.claude/templates/status-template.md`, one row per task, all ⬜ pending. Write a
   pending `<TASK-ID>.json` heartbeat for each task.
4. Mirror every task into the native task list (TaskCreate), with dependencies
   (TaskUpdate addBlockedBy). This gives the user live in-UI progress.

## Execution loop

1. **Compute the ready set**: tasks with all dependencies done. Within it, tasks
   marked `[P]` (and with disjoint file sets) launch together; others run alone.
2. **Launch each ready task as a background Agent** (subagent_type = the task's agent,
   e.g. `backend-dev`). The prompt must be self-contained: feature directory, task
   ID + full task line, pointers to spec.md / plan.md / constitution,
   `specs/_graph/index.md` and the relevant `specs/_graph/domains/<domain>.md`, its
   ownership paths from plan.md, and a reminder of the status protocol (heartbeat
   JSON + board.md row + event log line).
3. Mark launched tasks 🔵 running on the board and in_progress in the task list.
   While agents run, you may answer user questions; run `/status` on request.
4. **On each completion notification**:
   - Read the agent's report AND verify on disk: claimed files exist, heartbeat says
     done, tests it claims ran are plausible (spot-check with Bash when cheap —
     `python -m ruff check` on touched backend files is nearly free).
   - Update board.md row + event log + TaskUpdate (completed).
   - If **failed/blocked**: mark 🟥/🟧, keep dependents blocked, and surface to the
     user immediately with the detail — options: retry with guidance, reassign,
     or user intervenes. Independent branches keep running; never silently retry
     more than once.
   - Recompute the ready set and launch the next wave.
5. **Finish** when all tasks are done (or the user stops the run): final board state,
   per-agent summary of what was built, any deviations from plan, then point the
   user to `/verify`.

## Rules

- Never launch two tasks that touch the same file concurrently, even if both are [P].
- Never launch two migration-writing tasks concurrently — Alembic history must stay
  a single chain.
- Never edit an agent's output to "fix it quickly" — route fixes back through the
  owning agent as a follow-up task so ownership and status stay truthful.
- Everything the user needs to know mid-run goes through board.md + the task list —
  keep both current; stale status is worse than no status.
