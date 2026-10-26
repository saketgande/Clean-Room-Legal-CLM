# Status Board: Request page refinements (011-request-page-refinements)

Run started: 2026-10-23T00:00:00Z
Orchestrator session: /implement, waves per tasks.md

Legend: ⬜ pending · 🔵 running · ✅ done · 🟥 failed · 🟧 blocked

| Task | Agent | Status | Last update | Detail |
|---|---|---|---|---|
| T001 | backend-dev | ✅ done | 2026-10-23T10:30:00Z | activity endpoint + 13 tests; suite green bar known failure; ruff clean |
| T002 | frontend-dev | ✅ done | — | types + intakeApi.activity/counterparty |
| T003 | backend-dev | ✅ done | — | GET /counterparty + dossier leak fix |
| T004 | frontend-dev | ✅ done | 2026-10-23T09:31:52Z | WorkflowCard + stepFacts + overdueWords; overview rewire pending T006 |
| T005 | frontend-dev | ✅ done | 2026-10-23T10:30:00Z | ActivityCard + CounterpartyCard rewritten; helpers added, activityTrail deleted; overview rewire pending T006 |
| T006 | frontend-dev | ✅ done | 2026-10-23T11:30:00Z | request page wired: WorkflowCard, real Activity, Counterparty; 1857 tests green |
| T007 | qa-engineer | ✅ done | 2026-10-23T10:40:00Z | verification.md: 21 pass / 5 partial / 3 not-run / 0 fail; D1 pre-existing walled contract title in request payload |

## Event log
- 2026-10-23T09:30:55Z T002 frontend-dev done: RequestActivity/RequestCounterparty types + intakeApi.activity/counterparty
- 2026-10-23T10:00:00Z T001 backend-dev running: activity module + route in place, writing tests
- 2026-10-23T09:31:52Z T004 frontend-dev done: WorkflowCard replaces StepsCard; stepFacts, overdueWords added
- 2026-10-23T10:30:00Z T005 frontend-dev done: ActivityCard/CounterpartyCard + groupActivityByDay/counterpartySummary
- 2026-10-23T11:30:00Z T006 frontend-dev done: overview + page.tsx wired, tests updated (intake 384, full 1857)
- 2026-10-23T10:30:00Z T001 backend-dev done: GET /intake/requests/{id}/activity, 13 tests, full suite + ruff green
- 2026-10-23T10:00:00Z T003 backend-dev done: GET /intake/requests/{id}/counterparty per-viewer, stored dossier stripped, 22 new tests, full suite + ruff green
- 2026-10-23T12:00:00Z T005 frontend-dev done: Counterparty clip fixed (class cp collided with .li-board .cp; renamed cpbody), intake 385, full 1858
- 2026-10-23T10:40:00Z T007 qa-engineer done: gates green (known 1 failure), live access checks a-h pass, test data removed, verification.md written
