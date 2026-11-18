# Intake triage (`intake_triage`)

Reads a newly filed request and decides what it is, how risky it is, who should
handle it and which workflow fits. Result is stored on `IntakeRequest.ai_triage`.

## Trigger
- Request created → `service.py` sets `ai_triage = {"status": "pending"}` when triage is deferred,
  and the `intake_triage` job (`app/jobs/tasks.py`) calls `run_intake_triage`
  (applies triage, assigns the owner, writes the "AI triage complete" timeline event).
- Otherwise triage runs inline (`_apply_ai_triage`).

## Two paths (`app/intake/triage_agent.py`)
| Path | When | Who decides | `source` |
|---|---|---|---|
| `form_triage` | request filed on one of the nine agreement forms | the form (deterministic); model only adds a note | `form` |
| `_triage` | free text (email, Teams, chat) | the model, tool `triage_request` (`_SCHEMA`) | `llm` |
| `_fallback` | model call failed | keyword rules (`agents.classify` + `suggest_flow`) | `regex`, `degraded: true` |

`_to_triage` maps the raw model answer to the stored shape.

## Raw model output → stored shape
Raw (`_SCHEMA`): category, complexity, risk, urgency, confidence, reasoning, needs_human,
optional sub_type, business_unit, estimated_value, jurisdiction, key_asks, missing_info,
recommended_workflow_id, workflow_reasoning.

Stored (real rows REQ-4015 / REQ-4016 / degraded REQ-4017):
```
{ source, agent_id, category, risk_flag, complexity, confidence, needs_info,
  understanding: { urgency, key_asks, sub_type, reasoning, jurisdiction, missing_info, business_unit, estimated_value } | null,
  flow_suggestion: { source, flow_id, flow_name, reasoning, confidence, needs_human, alternatives, steps? },
  degraded?, degraded_reason?, needs_human? }
```

## Where it shows on the UI
`frontend/src/app/(app)/intake/_request-overview.tsx`
- "What the AI understood" card with confidence bar
- "Needs info before drafting"
- degraded banner (`degraded_reason`)
- AI urgency suggestion next to Priority (only when `source === "llm"`)
`_legal-intake.tsx` reads `flow_suggestion` for the list.

## Findings (against the output standard)
1. **Silent degradation.** The dev Claude key is rejected (401). Ledger `a_i_call_log`: intake_triage
   failed 8× (23 Oct – 2 Nov); REQ-4017…4021 are all `source: regex, degraded: true`.
2. **Renamed field:** model `risk` is stored as `risk_flag`.
3. **`needs_human` lives in two places** (top level only when degraded, else inside `flow_suggestion`).
4. **`confidence` duplicated** (top level and `flow_suggestion`); they differ in the degraded case.
5. **Two `source` vocabularies:** `llm|regex|form` vs `llm|degraded|none`.
6. **`understanding` is null** in the fallback, so the UI card disappears.
7. **No typed output.** Agents have hand-written tool dicts, no Pydantic model; frontend types
   `ai_triage` as `Record<string, unknown>` and casts inline.
8. Category list is duplicated across modules.
