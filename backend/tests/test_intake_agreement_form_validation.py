"""Agreement-wizard requests must be validated by the server, not only the browser.

The nine agreement forms lived only in the frontend, so a wizard request was
stored unchecked: skipping steps in the stepper (or calling the API directly)
filed requests with no value, no agreement type and no approver note.
``create_request`` now validates against agreement_forms.json, picked by the
``request_form`` the wizard sends (there is no request-type table any more).
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import SessionLocal
from app.intake import service
from app.intake.models import IntakeRequest
from app.intake.schemas import RequestCreate
from app.parties.models import Counterparty, LegalEntity

_TAG = "test-agreement-form-types"

_COMPLETE_NEW_AGREEMENT = {
    "request_form": "new_agreement",
    "agreement_type": "Services (MSA)",
    "entity": "Test Entity Ltd",
    "counterparty": "Test Counterparty Ltd",
    "cp_signer_name": "Maya Chen",
    "cp_signer_email": "maya@example.com",
    "paper": "Our template",
    "purpose": "Managed services",
    "department": "Procurement",
    "value": "1,00,000",
    "currency": "INR",
    "start_date": "2026-10-01",
    "term": "Fixed end date",
    "end_date": "2027-09-30",
    "scope": "Hosting",
    "payment_terms": "60 days",
    "personal_data": "No",
    "gxp": "No",
}


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        for r in s.scalars(select(IntakeRequest).where(IntakeRequest.description == _TAG)):
            s.delete(r)
        s.flush()
        for model in (LegalEntity, Counterparty):
            for row in s.scalars(select(model).where(model.name.like(f"{_TAG}%"))):
                s.delete(row)
        s.commit()
        s.close()


@pytest.fixture
def actor(db):
    user = db.scalar(select(User).order_by(User.created_at))
    if user is None:
        pytest.skip("needs a seeded user")
    return user


@pytest.fixture(autouse=True)
def _register(db, actor):
    """New agreements pick their entity and counterparty from the register."""
    e = LegalEntity(org_id=actor.org_id, name=f"{_TAG} Entity Ltd")
    c = Counterparty(org_id=actor.org_id, name=f"{_TAG} Counterparty Ltd")
    db.add_all([e, c])
    db.commit()
    _COMPLETE_NEW_AGREEMENT.update(entity_id=e.id, counterparty_id=c.id)


def _file(db, actor, values):
    return service.create_request(
        db, actor=actor, defer_triage=True,
        payload=RequestCreate(type_label="New agreement Request", description=_TAG, field_values=values),
    )


def test_a_missing_required_field_is_refused(db, actor):
    """The defect: skipping to step 8 filed a request with no value at all."""
    values = {k: v for k, v in _COMPLETE_NEW_AGREEMENT.items() if k != "value"}
    with pytest.raises(HTTPException) as exc:
        _file(db, actor, values)
    assert exc.value.status_code == 422
    assert "Total contract value" in exc.value.detail


def test_a_complete_request_is_filed_with_typed_values(db, actor):
    """Guards over-correction: a properly filled form must still file, keep its
    form, and have its value coerced to a number for routing."""
    out = _file(db, actor, dict(_COMPLETE_NEW_AGREEMENT))
    assert out["field_values"]["request_form"] == "new_agreement"
    assert out["field_values"]["value"] == 100000


def test_an_option_outside_the_form_is_refused(db, actor):
    """Select fields accept only the choices the wizard offers."""
    values = dict(_COMPLETE_NEW_AGREEMENT, agreement_type="Something made up")
    with pytest.raises(HTTPException) as exc:
        _file(db, actor, values)
    assert exc.value.status_code == 422


def test_a_question_that_does_not_apply_is_neither_required_nor_kept(db, actor):
    """An NDA has no value or term: they must not be demanded, and an answer left
    over from picking Services first must not reach the contract."""
    values = {k: v for k, v in _COMPLETE_NEW_AGREEMENT.items()
              if k not in ("term", "end_date", "payment_terms", "gxp", "currency", "scope")}
    values.update(agreement_type="NDA", nda_kind="Mutual", nda_term="2 years", governing_law="India")
    out = _file(db, actor, values)
    assert "value" not in out["field_values"]
    assert out["field_values"]["nda_term"] == "2 years"


def test_a_follow_up_question_is_required_once_it_applies(db, actor):
    """Renews automatically asks how long each renewal lasts and the notice to stop it."""
    with pytest.raises(HTTPException) as exc:
        _file(db, actor, dict(_COMPLETE_NEW_AGREEMENT, term="Renews automatically"))
    assert "Each renewal lasts" in exc.value.detail
