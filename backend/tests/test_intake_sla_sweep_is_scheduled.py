"""The intake SLA sweep must run on a schedule, not only when an admin presses a button.

Until it was scheduled, "Breaches (7d)" on the SLA page stayed at 0 while the live "Overdue" figure
climbed, and overdue requests were never escalated or reported to their assignees.
"""

from __future__ import annotations

from unittest.mock import patch

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.jobs import tasks
from app.jobs.celery_app import celery_app


def test_the_sweep_is_on_the_beat_schedule_and_points_at_a_real_task():
    entry = celery_app.conf.beat_schedule["intake-sla-sweep"]

    assert entry["task"] == "app.jobs.tasks.run_intake_sla_sweep"
    assert entry["task"] in celery_app.tasks or hasattr(tasks, "run_intake_sla_sweep")


def test_the_task_runs_the_sweep_for_every_organisation_and_returns_its_counts():
    with patch("app.intake.service.IntakeService.run_sla_sweep", return_value={"breached": 2}) as sweep:
        result = tasks.run_intake_sla_sweep()

    sweep.assert_called_once_with()
    assert result == {"breached": 2}


def test_a_failing_sweep_is_reported_not_raised_into_the_scheduler():
    with patch("app.intake.service.IntakeService.run_sla_sweep", side_effect=RuntimeError("db down")):
        assert tasks.run_intake_sla_sweep() == {"error": True}
