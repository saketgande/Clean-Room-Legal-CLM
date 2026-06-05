# F-11 — `_persist_obligations` clobbers human-tuned reminders (High)

**Agent 2 finding:** Every successful obligation_extraction soft-deletes all prior AI-extracted open obligations and re-creates them with fresh `ObligationReminder(remind_at = due_date - 7 days)`. Admins who hand-edited a reminder's date lose it on every flaky retry. No "did anything change" check.

## Headline change
- New `persist_obligations_diff(...)` computes a content hash over the structural fields `(due_date, responsible_party, obligation_type)` per obligation.
  - **Identical content** → UPDATE description/citation metadata only, reminder untouched.
  - **Genuinely new** → INSERT + recompute reminder.
  - **Vanished** (present last run, absent now) → soft-delete only that row.
- Human-sourced obligations (`metadata_json.source != "ai_extraction"`) are NEVER touched.
- Audit row carries a structured diff: `{added, kept, deleted, reminder_preserved, reminder_recomputed}`. The timeline event reflects the same numbers in its title.
- Hash uses only `(due_date, responsible_party, obligation_type)` — `description` is intentionally mutable so the model can clean up wording over time without forcing a delete/insert cycle (per Agent 3 risk note).

## Cross-cutting dependencies
- None new (uses existing audit and timeline writers).

## Agent 3 done-conditions met
- `tests/test_F11_obligations_diff.py::test_idempotent_extraction_preserves_reminder` — hand-edit a reminder's `remind_at`, re-run extraction with identical output, reminder unchanged.
- `tests/test_F11_obligations_diff.py::test_removed_obligation_soft_deleted` — re-run with one item dropped, only that row is soft-deleted; the other rows and their reminders are untouched.
- `tests/test_F11_obligations_diff.py::test_audit_carries_diff_metadata` — the audit row's `metadata_json` contains the `{added, kept, deleted, ...}` shape.

## Files
- `ai_controller_persist_obligations.py` — drop-in replacement for `AIController._persist_obligations`.

## Tests
- `proposals/audit_2026-06-02/tests/test_F11_obligations_diff.py`
