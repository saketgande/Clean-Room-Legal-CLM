"""An emailed-in request's type must always come from the canonical list: a
real configured IntakeRequestType, or one of agents.BUILTIN_EXTRA_TYPES —
never an arbitrary string (e.g. the raw email subject or an ad hoc label)."""

from app.intake import agents, email_triage_agent


def test_classify_email_matches_a_configured_type_by_name(monkeypatch):
    monkeypatch.setattr(
        email_triage_agent, "list_types",
        lambda db, *, org_id: [{"id": "type-nda-1", "name": "NDA"}],
    )
    result = email_triage_agent.classify_email(
        None, "org-1", "Mutual NDA request", "please review the attached mutual nda"
    )
    assert result["type_label"] == "NDA Request"
    assert result["request_type_id"] == "type-nda-1"


def test_classify_email_falls_back_to_a_builtin_extra_when_no_type_configured(monkeypatch):
    monkeypatch.setattr(email_triage_agent, "list_types", lambda db, *, org_id: [])
    result = email_triage_agent.classify_email(
        None, "org-1", "New vendor onboarding", "due diligence questionnaire for a new supplier"
    )
    assert result["type_label"] in agents.BUILTIN_EXTRA_TYPES
    assert result["type_label"] == "Vendor Due Diligence"
    assert result["request_type_id"] is None


def test_classify_email_leaves_type_untouched_on_general_category(monkeypatch):
    monkeypatch.setattr(email_triage_agent, "list_types", lambda db, *, org_id: [])
    result = email_triage_agent.classify_email(None, "org-1", "Hello", "just checking in")
    assert result["type_label"] is None
    assert result["request_type_id"] is None


def test_every_builtin_extra_fallback_is_a_canonical_string():
    assert set(agents.CATEGORY_TO_BUILTIN_EXTRA.values()) <= set(agents.BUILTIN_EXTRA_TYPES)
