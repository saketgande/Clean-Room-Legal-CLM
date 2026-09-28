"""The intake SLA clock counts working time, not wall-clock time.

A request filed at 17:00 on Friday with a 24-hour SLA used to be overdue by
17:00 on Saturday, when nobody had been at a desk. Every "overdue" and "at
risk" figure on the board inflated over every weekend and every night — and a
Critical escalation carried the same hardcoded 24 hours as a routine NDA.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.intake.business_time import (
    BusinessCalendar,
    business_deadline,
    business_ms_between,
)
from app.intake.service import _elapsed_and_window_ms, posture, sla_pct

HOUR = 3_600_000.0

LONDON = BusinessCalendar(
    enabled=True, tz=ZoneInfo("Europe/London"),
    start_hour=9, end_hour=17, working_days=frozenset({0, 1, 2, 3, 4}),
)
ALWAYS_ON = BusinessCalendar(
    enabled=False, tz=ZoneInfo("UTC"),
    start_hour=9, end_hour=17, working_days=frozenset({0, 1, 2, 3, 4}),
)


def _at(text: str) -> datetime:
    """A wall-clock instant in the calendar's own timezone."""
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo("Europe/London"))


# --- counting working time --------------------------------------------------


def test_a_weekend_costs_nothing():
    """The headline case. Friday 17:00 to Monday 09:00 is 64 wall-clock hours
    and zero working hours."""
    assert business_ms_between(_at("2026-09-18T17:00"), _at("2026-09-21T09:00"), LONDON) == 0


def test_only_the_working_part_of_a_day_counts():
    # 08:00 → 12:00 spans four hours, but the desk opens at 09:00.
    assert business_ms_between(_at("2026-09-16T08:00"), _at("2026-09-16T12:00"), LONDON) == 3 * HOUR
    # 16:00 → 22:00 spans six, but the desk closes at 17:00.
    assert business_ms_between(_at("2026-09-16T16:00"), _at("2026-09-16T22:00"), LONDON) == 1 * HOUR


def test_a_full_working_day_is_the_configured_window():
    assert business_ms_between(_at("2026-09-16T00:00"), _at("2026-09-17T00:00"), LONDON) == 8 * HOUR


def test_a_night_between_two_days_costs_nothing():
    # Wednesday 16:00 → Thursday 10:00: one hour before close, one after open.
    assert business_ms_between(_at("2026-09-16T16:00"), _at("2026-09-17T10:00"), LONDON) == 2 * HOUR


def test_switching_the_calendar_off_restores_wall_clock():
    """A team that genuinely runs a round-the-clock desk turns the switch off
    and gets the old behaviour, deliberately."""
    start, end = _at("2026-09-18T17:00"), _at("2026-09-21T09:00")
    assert business_ms_between(start, end, ALWAYS_ON) == (end - start).total_seconds() * 1000


def test_a_calendar_with_no_working_days_falls_back_rather_than_stopping_time():
    """Guards a misconfiguration turning every SLA off silently: with no
    working days, nothing would ever be due."""
    broken = BusinessCalendar(enabled=True, tz=ZoneInfo("UTC"), start_hour=9,
                              end_hour=17, working_days=frozenset())
    start, end = _at("2026-09-16T09:00"), _at("2026-09-16T17:00")
    assert business_ms_between(start, end, broken) == 8 * HOUR


def test_naive_timestamps_are_read_as_utc():
    """SQLite hands back naive datetimes in the test suite; treating them as
    local would shift every SLA by the timezone offset."""
    # Naive on purpose — that is the input this test exists to pin.
    naive = datetime(2026, 9, 16, 9, 0)  # noqa: DTZ001
    aware = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
    assert business_ms_between(naive, aware, LONDON) == 0


# --- projecting a deadline --------------------------------------------------


def test_a_deadline_lands_on_a_working_instant():
    """The timeline plots the breach as a point on the wall clock, so the
    working-time budget has to be projected onto it."""
    # Filed Friday 16:00 with an 8-working-hour budget: one hour before close
    # on Friday, then seven from Monday's 09:00 — 16:00 Monday. Wall-clock
    # arithmetic would have said 00:00 Saturday.
    breach = business_deadline(_at("2026-09-18T16:00"), 8 * HOUR, LONDON)
    assert breach.astimezone(ZoneInfo("Europe/London")).isoformat()[:16] == "2026-09-21T16:00"


def test_deadline_and_elapsed_agree():
    """The two functions are inverses; if they drift, the status badge and the
    timeline tell a user different things about the same request."""
    start = _at("2026-09-16T14:30")
    budget = 11 * HOUR
    breach = business_deadline(start, budget, LONDON)
    assert business_ms_between(start, breach, LONDON) == pytest.approx(budget)


# --- what the request actually reports --------------------------------------


def _request(**overrides):
    base = dict(
        submitted_at=_at("2026-09-18T17:00"),  # Friday, end of day
        closed_at=None, status="open", sla_hours=24,
        paused_at=None, paused_ms_total=0, created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_friday_evening_request_is_not_overdue_on_saturday(monkeypatch):
    """The defect this file exists for."""
    monkeypatch.setattr("app.intake.service.calendar_from_settings", lambda: LONDON)
    saturday = _at("2026-09-19T17:00")

    assert sla_pct(_request(), now=saturday) == 0
    assert posture(_request(), now=saturday) == "on_track"


def test_the_clock_resumes_on_monday(monkeypatch):
    """The positive control: working time must still accrue, or nothing is
    ever late."""
    monkeypatch.setattr("app.intake.service.calendar_from_settings", lambda: LONDON)
    # Friday 17:00 → Monday 13:00 is four working hours of a 24-hour budget.
    elapsed, window = _elapsed_and_window_ms(_request(), _at("2026-09-21T13:00"))
    assert elapsed == 4 * HOUR
    assert window == 24 * HOUR


def test_a_pause_is_subtracted_in_working_time_too(monkeypatch):
    """Guards a mismatch of units: subtracting wall-clock pause from a
    business-time elapsed would credit hours the clock never charged."""
    monkeypatch.setattr("app.intake.service.calendar_from_settings", lambda: LONDON)
    paused_since = _at("2026-09-21T10:00")  # Monday mid-morning
    request = _request(paused_at=paused_since)

    elapsed, _ = _elapsed_and_window_ms(request, _at("2026-09-21T13:00"))
    # 09:00→13:00 is four working hours; three of them were paused.
    assert elapsed == 1 * HOUR


# --- the budget -------------------------------------------------------------


def test_a_request_type_sets_its_own_sla():
    """Guards the hardcoded 24: a Critical escalation and a routine NDA shared
    one deadline because `sla_hours=24` was a literal in create_request."""
    import inspect

    from app.intake.service import create_request

    source = inspect.getsource(create_request)
    assert "sla_hours=24" not in source
    assert "rtype.sla_hours" in source
    assert "intake_default_sla_hours" in source
