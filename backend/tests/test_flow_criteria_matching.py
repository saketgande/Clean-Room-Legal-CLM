"""A real ANDA (Abbreviated New Drug Application) patent-litigation request got
misrouted to the "NDA Fast-Track" flow because both the flow criteria matcher
(flows/service.py::_matches) and the doc-type resolver
(intake/drafting.py::resolve_doc_type) used plain substring checks — "nda" is
a substring of "anda". Pin the word-boundary fix in both places; either one
regressing back to substring matching reintroduces the misroute."""

from types import SimpleNamespace

from app.flows.service import _matches
from app.intake.drafting import resolve_doc_type


def _request(type_label="Litigation", description="", priority=None, department=None, field_values=None):
    return SimpleNamespace(
        type_label=type_label, description=description, priority=priority,
        department=department, field_values=field_values or {},
    )


def test_resolve_doc_type_does_not_treat_anda_as_nda():
    anda = _request(description="ANDA Paragraph IV patent litigation notice; 45-day response deadline.")
    assert resolve_doc_type(anda) is None


def test_resolve_doc_type_still_recognizes_a_real_nda():
    nda = _request(type_label="NDA", description="Please draft an NDA for our new vendor relationship.")
    assert resolve_doc_type(nda) == "nda"


def test_flow_criteria_match_type_nda_does_not_match_anda_request():
    anda = _request(description="ANDA Paragraph IV patent litigation notice; 45-day response deadline.")
    assert _matches({"match_type": "nda"}, anda) is False


def test_flow_criteria_match_keyword_litigation_matches_the_anda_request():
    anda = _request(description="ANDA Paragraph IV patent litigation notice; 45-day response deadline.")
    assert _matches({"match_keyword": "litigation"}, anda) is True


def test_flow_criteria_match_type_nda_still_matches_a_real_nda_request():
    nda = _request(type_label="NDA", description="Mutual non-disclosure agreement with a new partner.")
    assert _matches({"match_type": "nda"}, nda) is True
