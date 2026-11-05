"""A failed AI task is shown in the contract's Activity by default, with why, and a restarted extraction is a
visible person's action. A failure nobody can see is a failure nobody fixes (app/intake/activity.py)."""

from __future__ import annotations

from app.core.database import utcnow
from app.core.models import ResourceTimelineEvent
from app.intake import activity


def _event(event_type: str, title: str, details: dict | None = None) -> ResourceTimelineEvent:
    return ResourceTimelineEvent(id="e-1", org_id="o-1", resource_type="contract", resource_id="c-1", event_type=event_type,
                                 title=title, details=details or {}, created_at=utcnow())


def test_a_failed_ai_task_is_not_hidden_behind_show_system_details():
    """Guards the failure being filed with the start and finish noise: the Activity list hid it by default."""
    item = activity._event_item(_event("ai.skill_failed", "AI skill failed: obligation_extraction",
                                       {"skill_name": "obligation_extraction", "reason": "The AI's answer was cut off."}),
                                "contract", {})
    assert item["system"] is False
    assert item["sentence"] == "AI could not finish: Obligation extraction - The AI's answer was cut off."


def test_an_older_failure_without_a_reason_still_reads_plainly():
    item = activity._event_item(_event("ai.skill_failed", "AI skill failed: obligation_extraction"), "contract", {})
    assert item["system"] is False and item["sentence"] == "AI could not finish: Obligation extraction"


def test_the_start_and_finish_of_a_task_stay_hidden_by_default():
    """Guards the change being wider than asked: only failures were un-hidden."""
    for kind in ("ai.skill_started", "ai.skill_succeeded"):
        assert activity._event_item(_event(kind, "AI skill started: x", {"skill_name": "x"}), "contract", {})["system"] is True


def test_a_restarted_extraction_is_a_visible_action_by_a_person():
    item = activity._event_item(_event("contract.obligations_extraction_requested", "Obligation extraction restarted"),
                                "contract", {})
    assert (item["system"], item["sentence"]) == (False, "Obligation extraction restarted")
