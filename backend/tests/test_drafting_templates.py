"""Drafting templates: every one fills, edits are checked, versions are kept.

Failures this guards:
* a template using a placeholder drafting doesn't fill — the next request that
  drafts from it 500s with a KeyError instead of producing a contract;
* a saved template with a stray brace or unknown placeholder getting stored —
  it would break drafting for everyone until someone found it;
* an org's saved wording not being what drafting uses, or a reset not going
  back to the shipped default;
* the placeholder list shown on the Templates page drifting from what
  drafting actually fills.

(That each default passes its playbook literally is test_playbook_library's
``test_our_own_template_passes_the_literal_check``.)
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import engine, new_uuid
from app.drafting_templates.service import (
    PLACEHOLDERS,
    TEMPLATES,
    DraftingTemplateService,
    check_body,
    default_body,
)
from app.intake import drafting
from app.organizations.models import Organization

_FIELDS = {"value": "4500000", "currency": "INR", "start_date": "2026-10-01", "term": "Fixed end date",
           "end_date": "2027-09-30", "payment_terms": "45 days", "nda_kind": "One-way",
           "nda_direction": "They share", "governing_law": "India"}


@pytest.mark.parametrize("key", sorted(TEMPLATES))
@pytest.mark.parametrize("fields", [{}, _FIELDS], ids=["bare request", "full form"])
def test_every_default_template_fills_completely(key, fields):
    text = drafting.render_document(key, company="Aegis Pharma Ltd", counterparty="Globex Corporation",
                                    effective="2026-10-01", fields=fields)
    assert "Aegis Pharma Ltd" in text and "Globex Corporation" in text
    assert "{" not in text and "}" not in text, f"{key}: an unfilled placeholder"


def test_the_placeholder_list_is_exactly_what_drafting_fills():
    filled = drafting.template_values("nda", company="A", counterparty="B", effective="2026-10-01", fields={})
    assert {p["name"] for p in PLACEHOLDERS} == set(filled)


def test_a_bare_request_drafts_at_the_playbooks_preferred_positions():
    """Blank answers fall back to what the playbook prefers, not its fallback."""
    nda = drafting.template_values("nda", company="A", counterparty="B", effective="2026-10-01", fields={})
    assert nda["term_clause"] == "continues for three (3) years"
    assert nda["survival_clause"] == "continue for five (5) years"
    assert drafting.template_values("consultancy", company="A", counterparty="B", effective="x",
                                    fields={})["payment_days"] == "30"


@pytest.mark.parametrize(("body", "says"), [
    ("Dear {client_name},", "Unknown placeholder {client_name}"),
    ("Clause 1 {company", "braces don't pair up"),
    ("   ", "empty"),
])
def test_a_template_drafting_could_not_fill_is_refused(body, says):
    with pytest.raises(HTTPException) as exc:
        check_body(body)
    assert exc.value.status_code == 422
    assert says in exc.value.detail


def test_literal_braces_written_doubled_are_fine():
    check_body("Schedule {{A}} between {company} and {counterparty}.")


@pytest.fixture
def db():
    """A session whose work is rolled back at the end, so nothing the test saves outlives it."""
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _make_actor(db: Session) -> User:
    """A real organization with one user. (Drafting-template rows point at their organization, so a
    stand-in "first user in the database" does not work on an empty database like CI's.)"""
    org = Organization(id=new_uuid(), name="Drafting Co", slug=f"drafting-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"admin-{uuid.uuid4().hex[:8]}@example.com", full_name="admin",
                hashed_password="hash", status="active")
    db.add(user)
    db.flush()
    return user


def test_saved_wording_is_what_drafting_uses_and_reset_goes_back(db):
    s = db
    actor = _make_actor(s)
    service = DraftingTemplateService(s)
    edited = default_body("vendor") + "\n\nSCHEDULE — test-drafting-templates marker for {counterparty}."

    saved = service.save(actor=actor, key="vendor", body=edited, note="test")
    assert saved["customized"] is True
    assert service.body_for(org_id=actor.org_id, key="vendor") == edited

    # Saving the same text again adds no version.
    assert service.save(actor=actor, key="vendor", body=edited, note=None)["version"] == saved["version"]

    back = service.reset(actor=actor, key="vendor", note=None)
    assert back["customized"] is False and back["version"] == saved["version"] + 1
    assert service.body_for(org_id=actor.org_id, key="vendor") == default_body("vendor")
    # The edited wording stays readable in History.
    assert any(v["body"] == edited for v in service.versions(org_id=actor.org_id, key="vendor"))


def test_an_unknown_template_is_not_found(db):
    s = db
    with pytest.raises(HTTPException) as exc:
        DraftingTemplateService(s).get(org_id="x", key="customer")
    assert exc.value.status_code == 404
