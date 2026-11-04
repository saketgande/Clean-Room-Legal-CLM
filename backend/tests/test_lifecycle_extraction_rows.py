"""The Active stage's obligation row tells the truth about extraction (app/workflows/lifecycle_rows.py, from the one
state in app/obligations/extraction_audit.py): tracked, none found, extracting, failed or never read (red, with a
Retry where a retry can help), or from an earlier version. Real rows in one rolled-back transaction."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.core.enums import JobStatus
from app.workflows import lifecycle_rows as rows
from tests.test_extraction_state import (
    SECRET,
    _db_job,
    _obligation,
    _with_text,
    new_world,
    rolled_back_session,
)


@pytest.fixture
def world():
    with rolled_back_session() as db:
        yield new_world(db)


def _first(w) -> dict:
    return rows._active(w.db, w.contract)[0]


def test_a_failed_extraction_is_a_red_row_with_its_plain_reason_and_a_retry(world):
    """Guards 'Extract obligations: waiting' for ever on a contract whose extraction failed, and the raw error text
    reaching the page (this job's error_message holds the contract's own words)."""
    version = _with_text(world)
    _db_job(world, version, JobStatus.FAILED, attempt_count=3, finished_at=datetime(2026, 10, 20, tzinfo=UTC),
            error_message=f"INSERT ... {SECRET}", meta={"failure": {"reason": "The AI's answer was cut off."}})

    row = _first(world)

    assert (row["name"], row["status"]) == ("Obligation extraction failed", "failed")
    assert "cut off" in row["detail"] and "Tried 3 times, last on 20 Oct 2026." in row["detail"]
    assert SECRET not in repr(row)
    assert row["action"] == {"kind": "extract_obligations", "label": "Retry", "contract_id": world.contract.id}


def test_a_failure_that_would_fail_the_same_way_again_shows_no_retry(world):
    """Guards the button paying for another identical failure: a truncated answer says retryable=false (Step 3's
    outcomes), so the row tells a person to ask an administrator instead of offering Retry."""
    version = _with_text(world)
    _db_job(world, version, JobStatus.FAILED, attempt_count=1, finished_at=datetime(2026, 10, 20, tzinfo=UTC),
            meta={"failure": {"reason": "The AI's answer was too long to fit in one pass.", "retryable": False}})

    row = _first(world)

    assert (row["name"], row["status"], row["action"]) == ("Obligation extraction failed", "failed", None)
    assert "too long to fit" in row["detail"] and "would fail the same way" in row["detail"]


def test_a_contract_nobody_read_is_red_with_a_retry(world):
    _with_text(world)
    row = _first(world)
    assert (row["name"], row["status"], row["action"]["label"]) == ("Obligations not read", "failed", "Retry")


def test_no_readable_text_is_red_but_offers_no_retry(world):
    """Guards a button that can only answer 409: with nothing to read, pressing it cannot help."""
    row = _first(world)
    assert (row["name"], row["status"], row["action"]) == ("Obligations not read", "failed", None)
    assert "no readable text" in row["detail"]


def test_tracked_obligations_count_only_live_rows(world):
    """Guards the old count including rows a person deleted ('Obligations tracked: 5' with 2 live) and a deleted row
    being named as the next thing due."""
    version = _with_text(world)
    _db_job(world, version, JobStatus.SUCCEEDED, attempt_count=1)
    _obligation(world, description="Pay the invoice", due_date=date(2026, 12, 1))
    _obligation(world, description="Deliver the report", due_date=date(2026, 11, 1))
    _obligation(world, description="Deleted duty", due_date=date(2026, 10, 30), deleted_at=datetime.now(UTC))

    row = _first(world)

    assert (row["name"], row["status"], row["action"]) == ("Obligations tracked", "done", None)
    assert row["detail"].startswith("2 obligations") and "Deliver the report" in row["detail"]
    assert "Deleted duty" not in row["detail"]


def test_none_found_is_a_done_row_with_the_models_note_and_no_alarm(world):
    """Guards a contract that legitimately has no duties being shown as a failure; the model's own words and a
    poor-text warning let a person judge the answer."""
    from app.ai.models import AISkillRun

    version = _with_text(world, poor=True)
    job = _db_job(world, version, JobStatus.SUCCEEDED, attempt_count=1)
    world.db.add(AISkillRun(
        org_id=world.org.id, skill_name="obligation_extraction", skill_version="2.0.0", execution_mode="job",
        status="succeeded", job_id=job.id, prompt_key="obligation_extraction", prompt_version="2.0.0",
        prompt_hash="0" * 64, model="m", input_payload={},
        output_payload={"obligations": [], "extraction_notes": "Only a cover page was found."}))
    world.db.flush()

    row = _first(world)

    assert (row["name"], row["status"], row["action"]) == ("No obligations found", "done", None)
    assert "Only a cover page was found." in row["detail"] and "read poorly" in row["detail"]


def test_a_job_in_flight_is_waiting_with_no_button(world):
    """Guards a second click stacking on a running job: nothing is offered while it works."""
    _db_job(world, _with_text(world), JobStatus.QUEUED, created_at=datetime.now(UTC))
    row = _first(world)
    assert (row["name"], row["status"], row["action"]) == ("Extracting obligations", "waiting", None)


def test_obligations_from_an_earlier_version_are_flagged_with_a_re_read(world):
    """Guards the Globex NDA case: edited after signing, still showing the signed copy's obligations as current."""
    old = _with_text(world, number=1)
    _db_job(world, old, JobStatus.SUCCEEDED, attempt_count=1)
    _obligation(world)
    _with_text(world, number=2)

    row = _first(world)

    assert (row["name"], row["status"], row["action"]["label"]) == ("Obligations come from an earlier version", "waiting", "Re-read")


def test_nothing_alarms_before_the_contract_is_active(world):
    """Guards a contract still in Review being shown a red failure for work nobody has asked for yet."""
    world.contract.lifecycle_stage = "review"
    _with_text(world)
    row = _first(world)
    assert (row["name"], row["status"], row["action"]) == ("Extract obligations", "planned", None)


def test_a_contract_not_yet_active_with_obligations_on_file_just_shows_them(world):
    """Guards the extraction audit reaching back before Active: a contract in Review that already has rows (from an
    earlier version, or put there by hand) is tracked, not 'stale' or failed, because nothing is expected of it yet."""
    world.contract.lifecycle_stage = "review"
    _with_text(world)
    _obligation(world)
    row = _first(world)
    assert (row["name"], row["status"], row["action"]) == ("Obligations tracked", "done", None)
    assert row["detail"].startswith("1 obligation")


def test_a_closed_contract_still_shows_a_failed_extraction(world):
    world.contract.lifecycle_stage = "closed"
    _with_text(world)
    assert _first(world)["status"] == "failed"
