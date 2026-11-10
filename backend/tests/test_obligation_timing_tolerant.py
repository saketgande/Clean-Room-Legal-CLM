"""One off-list timing word from the model must not throw away the whole extraction."""
from app.ai.schemas import ObligationExtractionOutput


def test_off_list_timing_words_are_softened_not_refused():
    """A 42-obligation answer was discarded for `kind: recurring` and `period_unit: hours`."""
    out = ObligationExtractionOutput.model_validate({"obligations": [
        {"description": "Report incidents", "timing": {"kind": "recurring"}},
        {"description": "Notify", "timing": {"kind": "deadline", "period_number": 24, "period_unit": "hours"}},
        {"description": "Pay", "timing": {"kind": "deadline", "period_number": 30, "period_unit": "days"}},
    ]})
    a, b, c = (o.timing for o in out.obligations)
    assert a.kind == "ongoing"
    assert b.period_unit is None  # no unit to count, so the deadline is left for a person
    assert (c.period_number, c.period_unit) == (30, "days")
