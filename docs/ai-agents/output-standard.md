# AI output standard (draft — grows as each agent is reviewed)

Rules every agent's stored output should follow. Added to only when an agent review proves a need.

1. **One Pydantic model per agent** for the stored output, registered with its gateway feature.
   No hand-written dicts as the only definition.
2. **Stored names = model names.** No silent renames between what the model returns and what is saved.
3. **One home per fact.** A value (confidence, needs_human) lives in exactly one place.
4. **One `source` vocabulary:** `model | rules | form | none`. Degraded is a separate boolean, not a source.
5. **Degraded output keeps the same shape** (fields may be null, never absent) and carries
   `degraded: true` + a human `degraded_reason`.
6. **Closed vocabularies are enums shared** between backend and frontend (categories, risk, complexity, urgency).
7. **Frontend reads a typed interface**, mirrored in `src/lib/types.ts`; no inline casts of `Record<string, unknown>`.
8. **Model failure is visible**: logged in the ledger and surfaced on the record, never silent.

Reviewed so far: intake triage (see `intake-triage.md`).
