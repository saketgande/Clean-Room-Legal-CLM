# F-05 — Empty-original edit silently dropped (High)

**Agent 2 finding:** `_anchor_suggestions` accepts an empty `original_text` and forces `start=end=0`. The first such edit wins (`applied=True`); subsequent empty-original edits all anchor at 0 but their `applied=False` is silent. A legal user asking "add NDA + confidentiality clauses" gets only the first applied; the DOCX says "1 tracked change". Compounds with F-23 (sort-tie collapse).

## Headline change (behavior change — this finding IS a bug)
- Empty-original inserts now retain their input ordering via a `sort_index` and the deterministic sort key `(start, end, sort_index)`. Each insert lands as a distinct tracked change.
- Anchored records carry a `dropped_reason` (`original_text_not_found` / `overlapping_prior_edit` / `empty_replacement`). The new helper `collect_dropped_suggestions(anchored)` returns a list the caller can include in the tool result as `dropped_suggestions`.
- `_apply_anchored` uses the same deterministic sort key so the DOCX render and the proposed-text computation never disagree under tied offsets.

## Cross-cutting dependencies
- None.

## Agent 3 done-conditions met
- `tests/test_F05_anchor_suggestions.py::test_multiple_empty_original_all_applied` — given two `original_text=""` suggestions, both end up in the DOCX with distinct anchor positions.
- `tests/test_F05_anchor_suggestions.py::test_overlap_dropped_with_reason` — when two suggestions collide, the second has `applied=False` and `dropped_reason="overlapping_prior_edit"`.
- `dropped_suggestions` is populated whenever a non-empty set of inputs has un-applied entries.

## Files
- `ai_tool_runtime_anchor.py` — rewritten `_anchor_suggestions`, `_apply_anchored`, plus the new `collect_dropped_suggestions` helper. Intended to replace the existing inline helpers in `backend/app/ai/tool_runtime.py:1589-1649`.

## Tests
- `proposals/audit_2026-06-02/tests/test_F05_anchor_suggestions.py`
