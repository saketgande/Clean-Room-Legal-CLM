"""Agreement-wizard requests must be validated by the server, not only the browser.

The nine agreement forms lived only in the frontend, so a wizard request was
stored with no request type: the server checked nothing, and skipping steps in
the stepper (or calling the API directly) filed requests with no value, no
agreement type and no approver note. Each form now has a backing request type
and ``create_request`` resolves it from the ``request_form`` the wizard sends.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import SessionLocal
from app.intake import service
from app.intake.agreement_forms import ensure_agreement_types, form_defs
from app.intake.models import IntakeRequest, IntakeRequestType
from app.intake.schemas import RequestCreate
from app.parties.models import Counterparty, LegalEntity

_TAG = "test-agreement-form-types"

_COMPLETE_NEW_AGREEMENT = {
    "request_form": "new_agreement",
    "entity": "Test Entity Ltd",
    "counterparty": "Test Counterparty Ltd",
    "region": "India",
    "department": "Procurement",
    "agreement_type": "Master Services Agreement",
    "agreement_category": "Technology",
    "business_unit": "Corporate",
    "value": "1,00,000",
    "effective_date": "2026-10-01",
    "end_date": "2027-09-30",
    "existing_contract": "No",
    "note_approvers": _TAG,
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


def test_every_form_gets_a_request_type_once(db, actor):
    """Idempotent: listing types twice must not duplicate the nine forms."""
    ensure_agreement_types(db, actor.org_id)
    ensure_agreement_types(db, actor.org_id)
    keys = db.scalars(
        select(IntakeRequestType.form_key).where(
            IntakeRequestType.org_id == actor.org_id, IntakeRequestType.form_key.is_not(None)
        )
    ).all()
    assert sorted(keys) == sorted(f["key"] for f in form_defs())


def test_a_missing_required_field_is_refused(db, actor):
    """The defect: skipping to step 8 filed a request with no value at all."""
    values = {k: v for k, v in _COMPLETE_NEW_AGREEMENT.items() if k != "value"}
    with pytest.raises(HTTPException) as exc:
        _file(db, actor, values)
    assert exc.value.status_code == 422
    assert "Total monetary value" in exc.value.detail


def test_a_complete_request_is_filed_and_linked_to_its_type(db, actor):
    """Guards over-correction: a properly filled form must still file, get its
    request type, and have its value coerced to a number for routing."""
    out = _file(db, actor, dict(_COMPLETE_NEW_AGREEMENT))
    rtype = db.get(IntakeRequestType, out["request_type_id"])
    assert rtype is not None and rtype.form_key == "new_agreement"
    assert out["field_values"]["value"] == 100000


def test_an_option_outside_the_form_is_refused(db, actor):
    """Select fields accept only the choices the wizard offers."""
    values = dict(_COMPLETE_NEW_AGREEMENT, agreement_type="Something made up")
    with pytest.raises(HTTPException) as exc:
        _file(db, actor, values)
    assert exc.value.status_code == 422


def test_agreement_form_types_cannot_be_deleted(db, actor):
    """Deleting one would silently switch its form back to no validation."""
    ensure_agreement_types(db, actor.org_id)
    t = db.scalar(select(IntakeRequestType).where(
        IntakeRequestType.org_id == actor.org_id, IntakeRequestType.form_key == "sow"))
    with pytest.raises(HTTPException) as exc:
        service.delete_type(db, actor=actor, type_id=t.id)
    assert exc.value.status_code == 409
