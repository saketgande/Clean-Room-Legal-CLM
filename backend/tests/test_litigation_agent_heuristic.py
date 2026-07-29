"""The litigation-agent's deterministic fallback (used when Claude is mocked,
degraded, or errors) previously computed a `hold` keyword flag and then ignored
it via `hold or True` — always claiming a legal hold was required regardless of
the ticket's actual content, with a dead variable making it look like a real
check. Pin that the default is now an honest, deliberate `True` (a conservative
choice for a badge-only signal, not a fake conditional)."""

import inspect

from app.intake.litigation_agent import _SCHEMA, _heuristic


def test_heuristic_fallback_always_flags_a_legal_hold_honestly():
    source = inspect.getsource(_heuristic)

    assert '"legal_hold_required": True' in source
    # No leftover dead keyword-check masquerading as the real condition.
    assert "hold or True" not in source
    assert "any(w in desc for w in" not in source


def test_heuristic_fallback_sets_a_fixed_low_assessment_confidence():
    """The heuristic is a keyword guess, never a real analysis — its
    assessment_confidence (fact confidence, distinct from flow-routing
    confidence) must be a fixed, honestly low value regardless of how
    confident the flow pick above happens to be."""
    source = inspect.getsource(_heuristic)
    assert '"assessment_confidence": 0.3' in source


def test_assessment_confidence_is_a_required_schema_field():
    """A model response that omits assessment_confidence must fail validation
    and route through the real failure handler, not silently pass with no
    fact-confidence signal for the flow engine's escalation gate to use."""
    assert "assessment_confidence" in _SCHEMA["required"]
