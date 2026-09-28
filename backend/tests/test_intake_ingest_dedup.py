"""A redelivered message must fold into the request it already filed.

Mail relays deliver at least once — Graph, Gmail and every webhook retry. The
key was previously stamped on *after* `create_request` had already committed,
so the row existed for a window with nothing to dedupe against: a crash in
between left a request that would be filed again on the next delivery, and a
concurrent delivery tripped the unique index with the duplicate already
committed, surfacing as a 500.
"""

import pytest
from sqlalchemy.exc import IntegrityError

# Registers every mapper: intake_request carries an FK to `contract`, so the
# full registry has to be loaded before these models can be queried.
import app.models  # noqa: F401
from app.core.database import SessionLocal
from app.intake import ingest, service
from app.intake.models import IntakeDocument, IntakeRequest

_MSG_PREFIX = "test-ingest-dedup-"


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        made = db.query(IntakeRequest).filter(
            IntakeRequest.external_message_id.like(f"{_MSG_PREFIX}%")
        ).all()
        for request in made:
            db.query(IntakeDocument).filter(
                IntakeDocument.request_id == request.id
            ).delete(synchronize_session=False)
            db.delete(request)
        db.commit()
        db.close()


def _file(db, suffix, *, subject="Acme MSA"):
    return ingest.ingest_message(
        db, source="email", from_email="counsel@acme.example", subject=subject,
        body="Please review the attached.",
        external_message_id=f"{_MSG_PREFIX}{suffix}",
    )


def _requests(db, suffix) -> list[IntakeRequest]:
    return db.query(IntakeRequest).filter(
        IntakeRequest.external_message_id == f"{_MSG_PREFIX}{suffix}"
    ).all()


def test_the_key_is_set_before_the_row_is_committed(db_session):
    """The defect itself. Stamping the key afterwards left a committed request
    with nothing to dedupe against — one crash between the two commits and the
    next delivery files the same contract as a second matter."""
    out = _file(db_session, "ordering")

    request = db_session.query(IntakeRequest).filter(
        IntakeRequest.id == out["id"]
    ).one()
    assert request.external_message_id == f"{_MSG_PREFIX}ordering"


def test_a_redelivery_folds_into_the_first_request(db_session):
    """The ordinary path: at-least-once delivery must not mean at-least-once
    matters."""
    first = _file(db_session, "redeliver")
    second = _file(db_session, "redeliver")

    assert second["deduped"] is True
    assert second["id"] == first["id"]
    assert len(_requests(db_session, "redeliver")) == 1


def test_a_concurrent_delivery_is_folded_in_not_raised(db_session, monkeypatch):
    """Guards the race the fast-path check cannot cover.

    Two deliveries both pass the "already filed?" lookup, then both insert.
    The partial unique index rejects the loser — and the loser must answer
    "already filed", not surface an IntegrityError as a 500 with a duplicate
    request already committed.
    """
    rival = SessionLocal()
    try:
        original = service.create_request

        def file_the_rival_first(db, **kwargs):
            """Stand in for the concurrent worker: file the same message on a
            separate session, committing it *after* this call's dedupe check
            has already passed."""
            monkeypatch.undo()
            ingest.ingest_message(
                rival, source="email", from_email="counsel@acme.example",
                subject="Acme MSA", body="Please review the attached.",
                external_message_id=f"{_MSG_PREFIX}race",
            )
            return original(db, **kwargs)

        monkeypatch.setattr(service, "create_request", file_the_rival_first)

        out = ingest.ingest_message(
            db_session, source="email", from_email="counsel@acme.example",
            subject="Acme MSA", body="Please review the attached.",
            external_message_id=f"{_MSG_PREFIX}race",
        )
    finally:
        rival.close()

    assert out["deduped"] is True
    # One message, one matter — whichever delivery won.
    assert len(_requests(db_session, "race")) == 1


def test_the_unique_index_is_what_backs_this_up(db_session):
    """Guards the constraint being dropped in a future migration: without it
    the race above has no backstop and the application check alone is a
    check-then-insert with a window in the middle."""
    owner = ingest._fallback_user(db_session)
    _file(db_session, "constraint")

    duplicate = IntakeRequest(
        org_id=owner.org_id, ref="REQ-TEST-DUP", source="email",
        requester_user_id=owner.id, type_label="Other", description="",
        external_message_id=f"{_MSG_PREFIX}constraint",
        submitted_at=db_session.query(IntakeRequest).first().submitted_at,
        created_by_user_id=owner.id, updated_by_user_id=owner.id,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_requests_without_a_key_are_not_deduped_against_each_other(db_session):
    """The index is partial (`WHERE external_message_id IS NOT NULL`) on
    purpose: ordinary web-form requests carry no key, and a NULL must never
    collide with another NULL."""
    owner = ingest._fallback_user(db_session)
    from types import SimpleNamespace

    payload = SimpleNamespace(
        source="form", requester_name=owner.email, department=None,
        request_type_id=None, type_label="Other", subject="No key",
        description="filed twice", field_values=None, priority="Medium",
    )
    first = service.create_request(db_session, actor=owner, payload=payload)
    second = service.create_request(db_session, actor=owner, payload=payload)

    assert first["id"] != second["id"]

    # Not covered by the fixture's prefix cleanup — remove them here.
    for created in (first, second):
        row = db_session.get(IntakeRequest, created["id"])
        if row is not None:
            db_session.delete(row)
    db_session.commit()
