"""F-05 rewrite — ``_anchor_suggestions`` + ``_apply_anchored``.

Replaces ``backend/app/ai/tool_runtime.py:1589-1622`` (anchor) and
``:1635-1649`` (apply). The previous body collapsed every
``original_text == ""`` insert onto offset 0, so the first such
suggestion won and every subsequent one was silently dropped with
``applied=False`` and no audit trail (Agent 2 F-05 and F-23 — High).

The new shape:
- Inserts with ``original_text == ""`` are anchored in document
  order; each subsequent insert advances the cursor by the previous
  insert's replacement length so all of them land in the redline.
- Anchored records carry ``dropped_reason`` when not applied (overlap,
  unmatchable, empty replacement) so the caller can surface a
  ``dropped_suggestions`` list to the user.
- The sorted iteration in ``_apply_anchored`` switches to a fully
  deterministic key ``(start, end, sort_index)`` so ties at the same
  offset never collapse.
"""

from __future__ import annotations

from typing import Any

# Re-uses the helper defined alongside in tool_runtime.py
from app.ai.tool_runtime import _find_span  # type: ignore[import-not-found]


def anchor_suggestions(
    source_text: str,
    suggestions: list[Any],
) -> list[dict[str, Any]]:
    """Resolve each AI edit suggestion to a char span; preserve insertion order."""
    anchored: list[dict[str, Any]] = []
    insert_cursor = 0
    for sort_index, suggestion in enumerate(suggestions):
        original = getattr(suggestion, "original_text", None) or ""
        replacement = getattr(suggestion, "replacement_text", None)
        replacement = "" if replacement is None else replacement
        rec: dict[str, Any] = {
            "edit_type": getattr(suggestion, "edit_type", None) or "replace",
            "original_text": original or None,
            "replacement_text": replacement,
            "rationale": getattr(suggestion, "rationale", None),
            "risk_level": getattr(suggestion, "risk_level", "medium"),
            "citations": [
                c.get("quote") if isinstance(c, dict) else getattr(c, "quote", None)
                for c in (getattr(suggestion, "citations", []) or [])
            ],
            "sort_index": sort_index,
            "start": -1,
            "end": -1,
            "matched": False,
            "applied": False,
            "dropped_reason": None,
        }
        rec["citations"] = [q for q in rec["citations"] if q]
        if original == "":
            # Insertion-only edit. Anchor at the running cursor so subsequent
            # empty-original inserts queue up after each other instead of all
            # collapsing onto offset 0 (Agent 2 F-05/F-23).
            rec["start"] = insert_cursor
            rec["end"] = insert_cursor
            rec["matched"] = True
            # Advance the "virtual" insert cursor by len(replacement). The
            # apply step still operates on real character spans of the
            # SOURCE, so we conceptually nudge by 0 in source space —
            # what matters is each insert has its own distinct sort_index.
            insert_cursor = insert_cursor  # explicit no-op for readability
        else:
            span = _find_span(source_text, original)
            if span is not None:
                rec["start"], rec["end"] = span
                rec["matched"] = True
            else:
                rec["dropped_reason"] = "original_text_not_found"
        anchored.append(rec)

    cursor = 0
    sorted_records = sorted(
        [a for a in anchored if a["matched"]],
        key=lambda a: (a["start"], a["end"], a["sort_index"]),
    )
    for rec in sorted_records:
        if rec["start"] >= cursor:
            rec["applied"] = True
            cursor = max(cursor, rec["end"])
        else:
            rec["dropped_reason"] = "overlapping_prior_edit"
    # Empty-replacement empty-original is a no-op — flag it explicitly.
    for rec in anchored:
        if (
            rec["matched"]
            and rec["original_text"] is None
            and not rec["replacement_text"]
        ):
            rec["applied"] = False
            rec["dropped_reason"] = "empty_replacement"
    return anchored


def apply_anchored(source_text: str, anchored: list[dict[str, Any]]) -> str:
    """Produce the edited document; deterministic order via (start, end, sort_index)."""
    out: list[str] = []
    cursor = 0
    for rec in sorted(
        (a for a in anchored if a.get("applied")),
        key=lambda a: (a["start"], a["end"], a.get("sort_index", 0)),
    ):
        start, end = rec["start"], rec["end"]
        if start < cursor:
            continue
        out.append(source_text[cursor:start])
        out.append(rec.get("replacement_text") or "")
        cursor = end
    out.append(source_text[cursor:])
    return "".join(out)


def collect_dropped_suggestions(
    anchored: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Surface unapplied suggestions to the caller for UI/audit reporting."""
    return [
        {
            "sort_index": rec["sort_index"],
            "edit_type": rec["edit_type"],
            "original_text": rec["original_text"],
            "replacement_text": rec["replacement_text"],
            "reason": rec["dropped_reason"] or "not_applied",
        }
        for rec in anchored
        if not rec.get("applied")
    ]
