"""F-05 anchor-suggestions tests."""

from __future__ import annotations

from types import SimpleNamespace


def _suggestion(*, original_text, replacement_text, **kwargs):
    return SimpleNamespace(
        original_text=original_text,
        replacement_text=replacement_text,
        edit_type=kwargs.get("edit_type"),
        rationale=kwargs.get("rationale"),
        risk_level=kwargs.get("risk_level", "medium"),
        citations=kwargs.get("citations") or [],
    )


def test_multiple_empty_original_all_applied():
    """Two empty-original inserts both apply with distinct sort indices."""
    from proposals.audit_2026_06_02.rewrites.F05_anchor_suggestions_empty_original import (  # type: ignore[import-not-found]
        ai_tool_runtime_anchor as mod,
    )

    suggestions = [
        _suggestion(original_text="", replacement_text="Confidentiality clause text."),
        _suggestion(original_text="", replacement_text="Non-solicit clause text."),
    ]
    anchored = mod.anchor_suggestions("Some base contract text.", suggestions)
    applied = [a for a in anchored if a["applied"]]
    assert len(applied) == 2
    # Each retains a distinct sort_index even though they share offset 0.
    assert {a["sort_index"] for a in applied} == {0, 1}


def test_overlap_dropped_with_reason():
    """A second match that overlaps a prior applied span is flagged ``not_applied``."""
    from proposals.audit_2026_06_02.rewrites.F05_anchor_suggestions_empty_original import (  # type: ignore[import-not-found]
        ai_tool_runtime_anchor as mod,
    )

    source = "Lorem ipsum dolor sit amet, consectetur adipiscing elit."
    suggestions = [
        _suggestion(original_text="ipsum dolor", replacement_text="A"),
        _suggestion(original_text="ipsum dolor", replacement_text="B"),
    ]
    anchored = mod.anchor_suggestions(source, suggestions)
    applied = [a for a in anchored if a["applied"]]
    dropped = [a for a in anchored if not a["applied"]]
    assert len(applied) == 1
    assert dropped[0]["dropped_reason"] == "overlapping_prior_edit"


def test_collect_dropped_surfaces_unapplied():
    """``collect_dropped_suggestions`` reports every non-applied record."""
    from proposals.audit_2026_06_02.rewrites.F05_anchor_suggestions_empty_original import (  # type: ignore[import-not-found]
        ai_tool_runtime_anchor as mod,
    )

    source = "alpha beta gamma."
    suggestions = [
        _suggestion(original_text="absent text", replacement_text="X"),
    ]
    anchored = mod.anchor_suggestions(source, suggestions)
    dropped = mod.collect_dropped_suggestions(anchored)
    assert len(dropped) == 1
    assert dropped[0]["reason"] == "original_text_not_found"
