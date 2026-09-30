# Status Board: [FEATURE NAME] ([NNN-slug])

Run started: [ISO timestamp]
Orchestrator session: [short note]

Legend: ⬜ pending · 🔵 running · ✅ done · 🟥 failed · 🟧 blocked

| Task | Agent | Status | Last update | Detail |
|---|---|---|---|---|
| T001 | db-engineer | ⬜ pending | — | — |
| T002 | backend-dev | ⬜ pending | — | — |

## Event log

Append-only; newest last. One line per status change:

- [ISO timestamp] T001 db-engineer → running: starting models
- [ISO timestamp] T001 db-engineer → done: 2 models + migration 0033_x, pytest green

---
Machine-readable per-task heartbeats live beside this file as `<TASK-ID>.json`:

```json
{
  "task": "T001",
  "agent": "db-engineer",
  "status": "running",
  "detail": "writing alembic migration",
  "updated": "2026-01-01T12:00:00Z"
}
```

`status` ∈ pending | running | done | failed | blocked
