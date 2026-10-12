"""A `From:` header is typed by whoever sent the mail.

Believing it let anyone file a legal request as any employee — the General
Counsel's identity, department and spend authority driving routing, and their
name on the audit trail. A request is only attributed to the address it claims
when the receiving mail server vouched for that address.
"""

import pytest

# Registers every mapper: intake_request carries an FK to `contract`, so the
# full registry has to be loaded before these models can be queried.
import app.models  # noqa: F401
from app.auth.models import User
from app.core.database import SessionLocal
from app.intake import ingest
from app.intake.models import IntakeDocument, IntakeRequest

_MSG_PREFIX = "test-sender-verify-"

DMARC_PASS = (
    "spf=pass (sender IP is 203.0.113.9) smtp.mailfrom=acme.example; "
    "dkim=pass (signature was verified) header.d=acme.example; "
    "dmarc=pass action=none header.from=acme.example; compauth=pass reason=100"
)
DMARC_FAIL = (
    "spf=fail (sender IP is 198.51.100.7) smtp.mailfrom=attacker.example; "
    "dkim=none; dmarc=fail action=oreject header.from=acme.example; "
    "compauth=fail reason=000"
)


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


@pytest.fixture
def known_user(db_session):
    """A real user who is NOT the fallback owner.

    The distinction is the whole assertion: if the impersonated account and the
    fallback happen to be the same row, "was not attributed to the claimed
    user" is unprovable.
    """
    fallback = ingest._fallback_user(db_session)
    user = db_session.query(User).filter(User.id != fallback.id).first()
    if user is None:
        pytest.skip("need a second user in the test database to impersonate")
    return user


def _file(db, *, from_email, auth_results, suffix):
    return ingest.ingest_message(
        db, source="email", from_email=from_email,
        subject="Please review the attached", body="Counterparty: Acme Industries.",
        external_message_id=f"{_MSG_PREFIX}{suffix}", auth_results=auth_results,
    )


def _request(db, suffix) -> IntakeRequest:
    return db.query(IntakeRequest).filter(
        IntakeRequest.external_message_id == f"{_MSG_PREFIX}{suffix}"
    ).one()


# --- the verdict itself -----------------------------------------------------


def test_dmarc_is_what_decides_not_spf_or_dkim():
    """Guards the tempting shortcut of accepting spf=pass. SPF authenticates
    the *envelope* sender and DKIM the signing domain — a spoofer can pass
    both while forging the From: a human actually reads. Only DMARC ties the
    visible From: to an authenticated identity."""
    assert ingest.sender_verdict(DMARC_PASS)["verified"] is True
    assert ingest.sender_verdict(DMARC_FAIL)["verified"] is False
    spf_only = "spf=pass smtp.mailfrom=attacker.example; dkim=pass header.d=attacker.example"
    assert ingest.sender_verdict(spf_only)["verified"] is False


def test_a_missing_header_is_unverified_not_trusted():
    """Guards the fail-open reading: not having checked is not the same as
    having passed — the rule that already governs a stale sanctions list."""
    assert ingest.sender_verdict(None)["verified"] is False
    assert ingest.sender_verdict("")["verified"] is False
    assert ingest.sender_verdict("nonsense")["verified"] is False


def test_microsoft_composite_auth_counts_for_intra_tenant_mail():
    """Guards over-tightening: mail that never leaves the tenant has no DMARC
    evaluation at all, so requiring dmarc=pass alone would mark every internal
    employee unverified and break the main legitimate path."""
    assert ingest.sender_verdict("compauth=pass reason=109")["verified"] is True


def test_the_outermost_verdict_wins():
    """Exchange prepends its own header, so a forwarded message carries an
    older Authentication-Results below it. The receiving server's own result
    is the one it stands behind — a stale pass underneath must not override a
    current fail."""
    chained = f"{DMARC_FAIL}; {DMARC_PASS}"
    assert ingest.sender_verdict(chained)["verified"] is False


# --- what the verdict changes ----------------------------------------------


def test_a_forged_sender_is_not_attributed_to_the_user_it_claims(db_session, known_user):
    """The defect this file exists for."""
    _file(db_session, from_email=known_user.email, auth_results=DMARC_FAIL, suffix="forged")

    request = _request(db_session, "forged")
    assert request.requester_user_id != known_user.id
    assert request.requester_user_id == ingest._fallback_user(db_session).id
    assert request.field_values["channel_sender_verified"] is False
    # The claimed address is still kept — the arrival is never lost, only
    # refused an identity nobody checked.
    assert request.field_values["channel_from"] == known_user.email


def test_a_verified_sender_is_attributed_normally(db_session, known_user):
    """The positive control: verification must not break the legitimate path."""
    _file(db_session, from_email=known_user.email, auth_results=DMARC_PASS, suffix="genuine")

    request = _request(db_session, "genuine")
    assert request.requester_user_id == known_user.id
    assert request.field_values["channel_sender_verified"] is True


def test_sql_wildcards_in_a_from_header_match_nothing(db_session):
    """Guards the injection in the old lookup: `ilike(email)` treated `%` and
    `_` in an attacker-controlled header as SQL wildcards, so a From: of
    `%@%` matched whichever user the table returned first — with a *verified*
    verdict that would have handed over a real account."""
    user = ingest._resolve_requester(db_session, "%@%", verified=True)
    fallback = ingest._fallback_user(db_session)
    assert user.id == fallback.id

    # And the exact form still resolves, case-insensitively.
    real = db_session.query(User).first()
    if real is not None:
        matched = ingest._resolve_requester(
            db_session, real.email.upper(), verified=True
        )
        assert matched.id == real.id


def test_case_insensitive_exact_match_only(db_session):
    """Guards a prefix match slipping back in: `admin@example.com.attacker.io`
    must not resolve to `admin@example.com`."""
    fallback = ingest._fallback_user(db_session)
    real = db_session.query(User).filter(User.id != fallback.id).first()
    if real is None:
        pytest.skip("need a second user in the test database")
    near_miss = ingest._resolve_requester(
        db_session, f"{real.email}.attacker.example", verified=True
    )
    assert near_miss.id == fallback.id
