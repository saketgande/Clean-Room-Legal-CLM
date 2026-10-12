"""A retried Teams activity must fold into the ticket it already filed.

Bot Framework delivers at least once and replays a retried activity verbatim.
The key previously fell back to `time.time()` when the activity carried no id
— unique on every call, so the key never matched and each retry filed another
ticket.
"""

import pytest

# Registers every mapper: intake_request carries an FK to `contract`, so the
# full registry has to be loaded before these models can be queried.
import app.models  # noqa: F401
from app.core.database import SessionLocal
from app.intake import ingest
from app.intake.models import IntakeDocument, IntakeRequest


@pytest.fixture
def db_session():
    db = SessionLocal()
    before = {r.id for r in db.query(IntakeRequest.id).filter(
        IntakeRequest.source == "teams"
    ).all()}
    try:
        yield db
    finally:
        db.rollback()
        made = [r for r in db.query(IntakeRequest).filter(
            IntakeRequest.source == "teams"
        ).all() if r.id not in before]
        for request in made:
            db.query(IntakeDocument).filter(
                IntakeDocument.request_id == request.id
            ).delete(synchronize_session=False)
            db.delete(request)
        db.commit()
        db.close()


def _activity(**overrides) -> dict:
    activity = {
        "text": "We need an NDA with Acme Industries",
        "from": {"name": "Sarah Chen", "id": "29:abc", "aadObjectId": "aad-1"},
        "conversation": {"id": "19:meeting@thread.v2"},
        "timestamp": "2026-09-17T09:15:00.000Z",
    }
    activity.update(overrides)
    return activity


# --- the key itself ---------------------------------------------------------


def test_the_activity_id_is_the_key_when_teams_sends_one():
    assert ingest._teams_message_key(_activity(id="f:123")) == "teams:f:123"


def test_a_retry_without_an_id_still_produces_the_same_key():
    """The defect this file exists for: `time.time()` made every retry unique,
    so the dedupe key could never match."""
    activity = _activity()
    assert ingest._teams_message_key(activity) == ingest._teams_message_key(dict(activity))


def test_two_different_messages_do_not_collide():
    """Guards over-correcting into the opposite failure. Losing a genuinely
    distinct legal request is worse than filing a duplicate, so the digest has
    to separate them."""
    base = ingest._teams_message_key(_activity())
    assert ingest._teams_message_key(_activity(text="Different ask entirely")) != base
    assert ingest._teams_message_key(
        _activity(timestamp="2026-09-17T11:00:00.000Z")
    ) != base
    assert ingest._teams_message_key(
        _activity(conversation={"id": "19:other@thread.v2"})
    ) != base
    other_sender = {"from": {"name": "Raj", "id": "29:xyz", "aadObjectId": "aad-2"}}
    assert ingest._teams_message_key(_activity(**other_sender)) != base


def test_the_same_person_asking_twice_is_two_requests():
    """A person who types the same thing an hour later wants two matters, not
    one — which is why `timestamp` is in the digest."""
    morning = ingest._teams_message_key(_activity(timestamp="2026-09-17T09:00:00.000Z"))
    afternoon = ingest._teams_message_key(_activity(timestamp="2026-09-17T15:00:00.000Z"))
    assert morning != afternoon


# --- what it changes end to end ---------------------------------------------


def test_a_retried_activity_files_one_ticket(db_session):
    activity = _activity()

    first = ingest.handle_teams_activity(db_session, activity)
    second = ingest.handle_teams_activity(db_session, dict(activity))

    assert "already filed as" in second["text"]
    # Both replies name the same ticket.
    ref = first["text"].split("**")[1]
    assert ref in second["text"]

    filed = db_session.query(IntakeRequest).filter(
        IntakeRequest.external_message_id == ingest._teams_message_key(activity)
    ).all()
    assert len(filed) == 1


def test_two_distinct_activities_file_two_tickets(db_session):
    first = ingest.handle_teams_activity(db_session, _activity())
    second = ingest.handle_teams_activity(
        db_session, _activity(text="Separate matter: vendor MSA renewal",
                              timestamp="2026-09-17T14:00:00.000Z")
    )

    assert "already filed as" not in second["text"]
    assert first["text"].split("**")[1] != second["text"].split("**")[1]
