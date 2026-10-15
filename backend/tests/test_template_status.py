"""The one status a template shows, and the check record it is read from.

Failures this guards:
* a template shown as Passes when its last review was of other wording, another playbook version, only
  part of the text, or failed (FR-3): a false all-clear on our own paper;
* a failed check erasing the earlier result, or two states shown at once (FR-2, FR-5, FR-8);
* a fallback or low-severity finding not counting, so a template with a known deviation reads clean (FR-4);
* "results vary" computed from unsaved checks, from other wording or another playbook version, or by
  starting a review (FR-11);
* a status that wrote a row (a pin, a check) just by being read;
* a check record whose shape drifts from the contract the page is typed against.

The 403/404 endpoint cases belong to the routes' tests.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.ai.schemas import PlaybookReviewOutput
from app.core.database import engine, utcnow
from app.drafting_templates import checks, schemas
from app.drafting_templates import service as svc
from app.drafting_templates.models import DraftingTemplateCheck, DraftingTemplateType
from app.drafting_templates.service import default_body, get_type
from app.playbooks.models import PlaybookVersion
from tests.test_template_check_jobs import (
    _RULES,
    SERVICES,
    _ai,
    _ai_runs,
    _deviation,
    _FakeReview,
    _playbook,
    _raw_check,
    _ready,
    _request,
    _run,
    _save_version,
    _set_running,
    _version,
    _world,
)


@pytest.fixture
def db():
    """A session whose work is rolled back at the end, so nothing the test writes outlives it."""
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _status(db, user, key: str = "msa") -> dict:
    """The status, checked against the contract: shape drift fails here instead of on the page."""
    out = checks.template_status(db, org_id=user.org_id, ttype=get_type(db, user.org_id, key))
    schemas.TemplateStatusOut.model_validate(out)
    assert set(out) == set(schemas.TemplateStatusOut.model_fields)
    assert out["label"] == checks.LABELS[out["state"]]
    return out


def _finding(severity: str = "medium", index: int = 0) -> dict:
    return {"index": index, "rule_index": 1, "clause_type": "payment_terms", "severity": severity,
            "issue": "x", "quote": None, "quote_template": None, "suggested_fix": None,
            "suggested_fix_template": None, "suggestion_is_wording": False, "approval_required": False}


def _ago(minutes: int):
    return utcnow() - timedelta(minutes=minutes)


def _done(db, user, playbook, version: PlaybookVersion, *, findings=(), ago: int = 5, truncated: bool = False,
          unsaved: bool = False, trigger: str = "saved", **kw) -> DraftingTemplateCheck:
    """A completed check of the shipped wording (version 0) against ``version`` of ``playbook``."""
    extra = {"text_length": 500_000, "read_length": 400_000} if truncated else {}
    return _raw_check(db, user, state="completed", unsaved=unsaved, body=default_body("msa"), trigger=trigger,
                      findings=list(findings), finished_at=_ago(ago), playbook_id=playbook.id,
                      playbook_name=playbook.name, playbook_version_id=version.id,
                      playbook_version_number=version.version_number, text_was_truncated=truncated,
                      **extra, **kw)


def _failed(db, user, playbook, version, *, ago: int = 1, code: str = "ai_unavailable",
            message: str = "The AI review could not run, so the template was not checked. Try again in a moment."
            ) -> DraftingTemplateCheck:
    """A failed check of the shipped wording."""
    return _raw_check(db, user, state="failed", body=default_body("msa"), finished_at=_ago(ago),
                      error_code=code, error_message=message, playbook_id=playbook.id,
                      playbook_version_id=version.id)


# --- row 1: no playbook ---------------------------------------------------------------------


def test_a_template_nothing_reviews_is_no_playbook_and_says_why(db):
    """FR-9: not covered by any playbook. The page must be able to tell the person what to do."""
    user = _world(db)

    status = _status(db, user)

    assert status["state"] == "no_playbook" and status["label"] == "No playbook"
    assert status["reason"] == "No published playbook covers MSA contracts. Choose a playbook for this template."
    assert status["active_check"] is None and status["decided_by"] is None


@pytest.mark.parametrize(("deleted", "published", "why"), [
    (True, True, "was deleted"), (False, False, "has no published version")])
def test_a_pinned_playbook_that_cannot_review_is_no_playbook_not_a_fallback(db, deleted, published, why):
    """D-11: the pin stands even when its playbook is gone; a better-named one does not take over."""
    user = _world(db)
    pinned, _ = _playbook(db, user, SERVICES, deleted=deleted, published=published)
    svc_pin = DraftingTemplateType(
        org_id=user.org_id, key="msa", name="Master Services Agreement", label="Master Services Agreement",
        form_key="new_agreement", agreement_kind="Services (MSA)", shipped=True, status="active",
        playbook_id=pinned.id)
    db.add(svc_pin)
    _playbook(db, user, "Master Services Agreement Playbook")
    db.flush()

    status = _status(db, user)

    assert status["state"] == "no_playbook"
    assert status["reason"] == (f"Master Services Agreement is reviewed by {SERVICES}, which {why}. "
                                "Publish it, or choose another playbook for this template.")


def test_no_playbook_outranks_a_check_in_flight_and_keeps_the_earlier_result_viewable(db):
    user, playbook, version = _ready(db)
    earlier = _done(db, user, playbook, version)
    _set_running(db, _raw_check(db, user, state="queued"))
    playbook.deleted_at = utcnow()
    db.flush()

    status = _status(db, user)

    assert status["state"] == "no_playbook" and status["active_check"] is None
    assert status["last_completed"]["id"] == earlier.id


# --- rows 2 and 3: checking, never checked -------------------------------------------------


@pytest.mark.parametrize(("state", "words"), [("queued", "waiting to start"), ("running", "running")])
def test_a_check_in_flight_is_checking_and_the_earlier_result_stays_viewable(db, state, words):
    """FR-5: while a check waits or runs the status is Checking, and the previous completed result is
    still there to open, labelled with the wording and playbook version it covered."""
    user, playbook, version = _ready(db)
    earlier = _done(db, user, playbook, version)
    active = _raw_check(db, user, state=state)

    status = _status(db, user)

    assert status["state"] == "checking" and status["reason"] == f"A check is {words}."
    assert status["active_check"]["id"] == active.id and status["active_check"]["state"] == state
    last = status["last_completed"]
    assert last["id"] == earlier.id and last["template_version"] == 0 and last["playbook_version_number"] == 1
    assert status["decided_by"] is None


def test_a_template_never_checked_says_so(db):
    user, _, _ = _ready(db)

    status = _status(db, user)

    assert status["state"] == "never_checked" and status["reason"] == "Not checked yet."
    assert status["finding_count"] is None and status["last_completed"] is None


def test_unsaved_and_superseded_checks_do_not_count_as_a_check_of_the_template(db):
    """FR-16: a check of text still in the editor says nothing about the saved wording, and a
    superseded check never ran."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, unsaved=True)
    _raw_check(db, user, state="superseded", finished_at=_ago(3))
    _set_running(db, _raw_check(db, user, state="queued", unsaved=True, body="Draft {company} and {counterparty}."))

    assert _status(db, user)["state"] == "never_checked"


# --- row 4: out of date -------------------------------------------------------------------------


def test_new_wording_since_the_last_check_is_out_of_date_for_the_wording(db):
    """FR-6: a check of version 0 says nothing about version 1."""
    user, playbook, version = _ready(db)
    old = _done(db, user, playbook, version)
    _save_version(db, user, "msa", "New {company} and {counterparty}.")

    status = _status(db, user)

    assert status["state"] == "out_of_date" and status["changed"] == "wording"
    assert status["reason"] == "The wording changed after the last check."
    assert status["decided_by"]["id"] == old.id and status["error_code"] is None


def test_a_newer_playbook_version_since_the_last_check_is_out_of_date_for_the_playbook(db):
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version)
    _version(db, user, playbook, 2, _RULES)

    status = _status(db, user)

    assert status["state"] == "out_of_date" and status["changed"] == "playbook"
    assert status["reason"] == "The playbook changed after the last check."


def test_both_moving_says_both(db):
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version)
    _save_version(db, user, "msa", "New {company} and {counterparty}.")
    _version(db, user, playbook, 2, _RULES)

    status = _status(db, user)

    assert (status["state"], status["changed"]) == ("out_of_date", "both")
    assert status["reason"] == "The wording and the playbook both changed after the last check."


def test_changing_the_pinned_playbook_reads_as_a_playbook_change_until_it_is_rechecked(db):
    """A pin change swaps which playbook reviews the template; the old result was against the old one."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version)
    other, _ = _playbook(db, user, "Other Playbook")
    pin = db.scalar(select(DraftingTemplateType).where(DraftingTemplateType.org_id == user.org_id))
    pin.playbook_id = other.id
    db.flush()

    status = _status(db, user)

    assert (status["state"], status["changed"]) == ("out_of_date", "playbook")


def test_a_release_that_edits_the_shipped_wording_turns_version_zero_out_of_date(db, monkeypatch):
    """The shipped default is hashed live: a deploy that changes defaults/msa.txt must not leave the old
    review standing as a pass on text that is no longer the text."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version)
    assert _status(db, user)["state"] == "passes"
    monkeypatch.setattr(svc, "default_body", lambda key: "A new shipped {company} and {counterparty}.")

    status = _status(db, user)

    assert (status["state"], status["changed"]) == ("out_of_date", "wording")


def test_a_failed_check_of_old_wording_is_out_of_date_not_couldnt_check(db):
    """Row 4 comes before row 5: a failure about wording that is no longer current is not news."""
    user, playbook, version = _ready(db)
    _failed(db, user, playbook, version)
    _save_version(db, user, "msa", "New {company} and {counterparty}.")

    assert _status(db, user)["state"] == "out_of_date"


# --- row 5: couldn't check ----------------------------------------------------------------------


@pytest.mark.parametrize("code", ["ai_unavailable", "cost_cap", "no_rules", "no_playbook", "unfillable",
                                  "wording_changed", "interrupted"])
def test_a_failed_attempt_is_couldnt_check_with_its_reason_and_never_passes(db, code):
    """FR-8: every failure reads the same way: the reason in plain words, the code for the retry
    button, and the earlier completed result still viewable."""
    user, playbook, version = _ready(db)
    earlier = _done(db, user, playbook, version, ago=30)
    failed = _failed(db, user, playbook, version, ago=2, code=code, message="Plain words about it.")

    status = _status(db, user)

    assert status["state"] == "couldnt_check" and status["label"] == "Couldn't check"
    assert status["reason"] == "Plain words about it." and status["error_code"] == code
    assert status["decided_by"]["id"] == failed.id
    assert status["last_completed"]["id"] == earlier.id, "a failure does not erase the earlier result"
    assert status["decided_by"]["error_code"] == code


def test_a_check_that_succeeds_after_a_failure_replaces_it(db):
    user, playbook, version = _ready(db)
    _failed(db, user, playbook, version, ago=10)
    _done(db, user, playbook, version, ago=1)

    assert _status(db, user)["state"] == "passes"


# --- row 6: partly checked ----------------------------------------------------------------------


def test_a_template_longer_than_the_review_can_read_is_partly_checked_never_passes(db):
    """FR-7: no findings in the first 400,000 characters says nothing about the rest."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, truncated=True)

    status = _status(db, user)

    assert status["state"] == "partly_checked"
    assert status["reason"] == "The review read the first 400,000 of 500,000 characters; the last 20% was not checked."


def test_a_partly_checked_template_with_findings_is_still_partly_checked(db):
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, truncated=True, findings=[_finding("high")])

    status = _status(db, user)

    assert status["state"] == "partly_checked" and status["finding_count"] == 1
    assert status["highest_severity"] == "high"


# --- rows 7 and 8: has findings, passes -------------------------------------------------------


def test_findings_are_counted_with_the_highest_severity(db):
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, findings=[_finding("low", 0), _finding("high", 1)])

    status = _status(db, user)

    assert status["state"] == "has_findings" and status["label"] == "Has findings"
    assert status["reason"] == "2 findings, highest High."
    assert (status["finding_count"], status["highest_severity"]) == (2, "high")


def test_a_single_low_severity_finding_still_counts(db):
    """FR-4: a finding at the fallback position, or of low severity, is still a finding."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, findings=[_finding("low")])

    status = _status(db, user)

    assert status["state"] == "has_findings" and status["reason"] == "1 finding, highest Low."


def test_a_complete_current_review_with_nothing_found_passes(db):
    """FR-3: passes needs the current wording, the current playbook version, the whole text and no findings."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version)

    status = _status(db, user)

    assert status["state"] == "passes" and status["label"] == "Passes" and status["finding_count"] == 0
    assert status["reason"] == (
        f"The latest review found nothing in the current wording (against {SERVICES} version 1).")
    assert status["changed"] is None and status["error_code"] is None


def test_the_most_recent_finished_check_decides(db):
    """FR-3/4 with D-5: the status follows the latest completed check, in either direction."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, findings=[_finding("high")], ago=20)
    _done(db, user, playbook, version, ago=2)
    assert _status(db, user)["state"] == "passes"
    _done(db, user, playbook, version, findings=[_finding("low")], ago=1)
    assert _status(db, user)["state"] == "has_findings"


# --- results vary (FR-11) -----------------------------------------------------------------------


def test_checks_of_the_same_wording_and_playbook_that_disagree_are_marked_as_varying(db):
    """D-5: identical wording gave different answers. The marker sits beside the status, which still
    follows the latest check."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, findings=[_finding()], ago=20)
    _done(db, user, playbook, version, ago=2)

    status = _status(db, user)

    assert status["state"] == "passes" and status["results_vary"] is True


@pytest.mark.parametrize("both", [[_finding()], []], ids=["both found something", "both found nothing"])
def test_agreeing_checks_are_not_marked(db, both):
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, findings=both, ago=20)
    _done(db, user, playbook, version, findings=both, ago=2)

    assert _status(db, user)["results_vary"] is False


def test_an_unsaved_check_that_disagrees_does_not_make_the_results_vary(db):
    """FR-11: text still in the editor is not the wording the saved checks are about."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, ago=20)
    _done(db, user, playbook, version, findings=[_finding()], ago=2, unsaved=True)

    assert _status(db, user)["results_vary"] is False


def test_the_marker_is_gone_when_the_wording_or_the_playbook_version_changes(db):
    """Different wording, or a different playbook version, is a different question: the old
    disagreement says nothing about it until checks of the new pair disagree."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, findings=[_finding()], ago=20)
    _done(db, user, playbook, version, ago=10)
    assert _status(db, user)["results_vary"] is True

    _version(db, user, playbook, 2, _RULES)
    assert _status(db, user)["results_vary"] is False  # same wording, newer playbook version

    _save_version(db, user, "msa", "New {company} and {counterparty}.")
    assert _status(db, user)["results_vary"] is False  # and new wording too


def test_reading_the_marker_starts_no_review_and_writes_nothing(db):
    """FR-11: worked out from stored records only."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, findings=[_finding()], ago=20)
    _done(db, user, playbook, version, ago=2)
    before = db.scalar(select(func.count()).select_from(DraftingTemplateCheck))

    assert _status(db, user)["results_vary"] is True

    assert db.scalar(select(func.count()).select_from(DraftingTemplateCheck)) == before
    assert _ai_runs(db, user.org_id) == 0


def test_two_real_checks_of_identical_wording_that_disagree_vary_through_the_worker(db, monkeypatch):
    """End to end: the same wording, reviewed twice by a model that answers differently, is marked."""
    user, _, _ = _ready(db)
    _ai(monkeypatch, _FakeReview([PlaybookReviewOutput(deviations=[_deviation()]), PlaybookReviewOutput()]))
    first = _request(db, user, trigger="manual")
    _run(db, first)
    assert _status(db, user)["state"] == "has_findings"
    second = _request(db, user, trigger="manual")
    _run(db, second)

    status = _status(db, user)

    assert first.id != second.id and status["state"] == "passes" and status["results_vary"] is True
    assert status["decided_by"]["id"] == second.id


# --- reading is read only ----------------------------------------------------------------------


def test_reading_the_status_of_an_unpinned_template_writes_no_pin_and_no_check(db):
    """D-11: the pin is written by the first check, draft or publish, never by a GET."""
    user = _world(db)
    _playbook(db, user, SERVICES)

    status = _status(db, user)

    assert status["state"] == "never_checked"
    assert db.scalar(select(func.count()).select_from(DraftingTemplateType)
                     .where(DraftingTemplateType.org_id == user.org_id)) == 0
    assert db.scalar(select(func.count()).select_from(DraftingTemplateCheck)
                     .where(DraftingTemplateCheck.org_id == user.org_id)) == 0


# --- the notice a draft carries -----------------------------------------------------------------


def test_a_draft_from_a_template_that_has_findings_carries_the_exact_notice(db):
    """FR-47: the words are pinned: the request page and the contract's history show them."""
    user, playbook, version = _ready(db)
    _done(db, user, playbook, version, findings=[_finding("low", 0), _finding("high", 1)])

    assert checks.status_notice(_status(db, user)) == (
        "This template does not currently pass its playbook: Has findings — 2 findings, highest High. "
        "The draft was created anyway.")


def test_a_template_that_passes_has_no_notice_and_every_other_state_has_one(db):
    user, playbook, version = _ready(db)
    assert checks.status_notice(_status(db, user)).startswith(
        "This template does not currently pass its playbook: Never checked — Not checked yet.")
    _done(db, user, playbook, version)
    assert checks.status_notice(_status(db, user)) is None
    _save_version(db, user, "msa", "New {company} and {counterparty}.")
    assert checks.status_notice(_status(db, user)) == (
        "This template does not currently pass its playbook: Out of date — The wording changed after the "
        "last check. The draft was created anyway.")


# --- the check record ---------------------------------------------------------------------------


def _every_kind_of_check(db, user, playbook, version) -> list[DraftingTemplateCheck]:
    return [
        _raw_check(db, user, state="queued", trigger="saved"),
        _done(db, user, playbook, version, findings=[_finding("critical")], trigger="manual",
              trigger_detail={"a": 1}, summary="S", rule_count=3),
        _failed(db, user, playbook, version),
        _raw_check(db, user, state="superseded", finished_at=_ago(1)),
        _raw_check(db, user, state="completed", unsaved=True, body="U {company} and {counterparty}.", findings=[]),
    ]


def test_a_check_serializes_to_exactly_the_contracts_summary_and_full_shapes(db):
    """Every key is always present (null when empty) and none is extra: the page is typed against
    these field for field."""
    user, playbook, version = _ready(db)
    for check in _every_kind_of_check(db, user, playbook, version):
        summary = checks.serialize_check(db, check, full=False)
        schemas.TemplateCheckSummaryOut.model_validate(summary)
        assert set(summary) == set(schemas.TemplateCheckSummaryOut.model_fields)
        full = checks.serialize_check(db, check, full=True)
        schemas.TemplateCheckOut.model_validate(full)
        assert set(full) == set(schemas.TemplateCheckOut.model_fields)
        assert full["id"] == check.id and full["state"] == check.status


def test_a_check_says_who_asked_and_whether_the_system_acted_for_them(db):
    """FR-18: a save's check is the system's, on behalf of the person; Check now is the person's own."""
    user, _, _ = _ready(db)
    saved = _raw_check(db, user, trigger="saved")
    manual = _raw_check(db, user, state="completed", findings=[], trigger="manual", finished_at=_ago(1))

    a, b = (checks.serialize_check(db, c, full=False) for c in (saved, manual))

    assert (a["requested_by"], a["on_behalf"]) == ("Checker", True)
    assert (b["requested_by"], b["on_behalf"]) == ("Checker", False)


def test_a_completed_checks_counts_come_from_its_findings_and_a_failed_one_has_none(db):
    user, playbook, version = _ready(db)
    done = _done(db, user, playbook, version, findings=[_finding("low", 0), _finding("critical", 1)])
    failed = _failed(db, user, playbook, version)

    a, b = (checks.serialize_check(db, c, full=False) for c in (done, failed))

    assert (a["finding_count"], a["highest_severity"]) == (2, "critical")
    assert (b["finding_count"], b["highest_severity"], b["error_code"]) == (None, None, "ai_unavailable")
