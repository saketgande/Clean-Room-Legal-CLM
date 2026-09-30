---
name: status
description: Read-only status board. Shows every feature's phase and, for running implementations, live per-task/per-agent state from the status files. Usable any time, in any session.
---

# /status — where is everything?

Read-only; never blocks on running agents; works in a fresh session or for a
teammate who just pulled the repo.

## Procedure

1. **Scan `specs/*/`.** For each feature determine its phase from artifacts:
   spec.md / plan.md / tasks.md present? Each DRAFT or APPROVED? verification.md?
2. **For features with a `status/` directory**, read every `<TASK-ID>.json` heartbeat
   and board.md. Also check the native task list (TaskList) — if this session is the
   orchestrating one, merge; the JSON files win on conflict (they're the durable
   record).
3. **Render one report:**

   - Per feature, one line: `003-renewal-reminders — Phase 4 (implementing) — 4/7 tasks done`
   - For the active feature, the task table:

     | Task | Agent | Status | Updated | Detail |
     |---|---|---|---|---|
     | T004 | backend-dev | 🔵 running | 12:04:31 | writing pytest cases |

   - Flag anomalies explicitly: 🟥 failed / 🟧 blocked tasks (with detail), and
     **stale heartbeats** — a `running` task whose `updated` is older than ~15
     minutes is marked "possibly stalled".

4. If a specific feature ID was passed (`/status 003`), show only that feature but
   in full detail, including the event log tail from board.md.
