"""An emailed-in request's type must always come from the canonical list:
one of agents.BUILTIN_EXTRA_TYPES — never an arbitrary string (e.g. the raw
email subject or an ad hoc label)."""

from app.intake import agents, email_triage_agent


def test_classify_email_maps_a_category_to_its_builtin_extra():
    """An emailed NDA gets the canonical label for its category, not the subject."""
    result = email_triage_agent.classify_email(
        None, "org-1", "Mutual NDA request", "please review the attached mutual nda"
    )
    assert result["type_label"] == agents.CATEGORY_TO_BUILTIN_EXTRA["NDA"]
    assert "request_type_id" not in result


def test_classify_email_falls_back_to_a_builtin_extra():
    result = email_triage_agent.classify_email(
        None, "org-1", "New vendor onboarding", "due diligence questionnaire for a new supplier"
    )
    assert result["type_label"] in agents.BUILTIN_EXTRA_TYPES
    assert result["type_label"] == "Vendor Due Diligence"


def test_classify_email_leaves_type_untouched_on_general_category():
    result = email_triage_agent.classify_email(None, "org-1", "Hello", "just checking in")
    assert result["type_label"] is None


def test_every_builtin_extra_fallback_is_a_canonical_string():
    assert set(agents.CATEGORY_TO_BUILTIN_EXTRA.values()) <= set(agents.BUILTIN_EXTRA_TYPES)
