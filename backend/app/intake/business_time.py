"""Business-hours arithmetic for the intake SLA clock.

The SLA used to be measured in wall-clock time, so a request filed at 17:00 on
Friday with a 24-hour SLA was overdue by 17:00 on Saturday — when nobody was
working. Every "overdue" and "at risk" figure on the board inflated over every
weekend and every night.

Two operations, and the SLA needs both: how much working time has passed
between two instants, and the instant at which a given amount of working time
will have passed. The first drives the status; the second is what a timeline
plots as the breach point.

Pure functions on purpose — no database, no request object — so the awkward
cases (a window opening mid-day, a weekend, a DST boundary) are testable
directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings

# A request open for longer than this has a broken calendar behind it, not a
# long deadline; the walk stops rather than spinning.
_MAX_DAYS = 3650


@dataclass(frozen=True)
class BusinessCalendar:
    enabled: bool
    tz: ZoneInfo
    start_hour: int
    end_hour: int
    working_days: frozenset[int]  # Monday=0 … Sunday=6

    @property
    def usable(self) -> bool:
        """A calendar with no working days, or one that closes before it opens,
        would make every deadline unreachable. Callers fall back to wall-clock
        rather than reporting nothing as ever due."""
        return bool(self.working_days) and self.end_hour > self.start_hour


def calendar_from_settings() -> BusinessCalendar:
    try:
        tz = ZoneInfo(settings.intake_business_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    days = frozenset(
        int(part) for part in str(settings.intake_business_days).split(",")
        if part.strip().isdigit() and 0 <= int(part) <= 6
    )
    return BusinessCalendar(
        enabled=bool(settings.intake_business_hours_enabled),
        tz=tz,
        start_hour=int(settings.intake_business_day_start_hour),
        end_hour=int(settings.intake_business_day_end_hour),
        working_days=days,
    )


def _aware(dt: datetime) -> datetime:
    """Naive timestamps in this codebase are UTC (the columns are timezone-aware
    but SQLite hands them back naive in tests)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _windows(cal: BusinessCalendar, start: datetime, end: datetime):
    """Yield each working window between `start` and `end`, clipped to both.

    Windows are built in the calendar's own timezone so a DST change shortens
    or lengthens the day exactly as it does for the people working it.
    """
    local_start = start.astimezone(cal.tz)
    local_end = end.astimezone(cal.tz)
    day = local_start.date()
    for _ in range(_MAX_DAYS):
        if day > local_end.date():
            return
        if day.weekday() in cal.working_days:
            opens = datetime.combine(day, time(cal.start_hour), tzinfo=cal.tz)
            closes = datetime.combine(day, time(cal.end_hour), tzinfo=cal.tz)
            lo = max(opens, local_start)
            hi = min(closes, local_end)
            if hi > lo:
                yield lo, hi
        day += timedelta(days=1)


def business_ms_between(start: datetime, end: datetime, cal: BusinessCalendar | None = None) -> float:
    """Working milliseconds elapsed between two instants."""
    cal = cal or calendar_from_settings()
    start, end = _aware(start), _aware(end)
    if end <= start:
        return 0.0
    if not cal.enabled or not cal.usable:
        return (end - start).total_seconds() * 1000.0
    return sum((hi - lo).total_seconds() * 1000.0 for lo, hi in _windows(cal, start, end))


def business_deadline(start: datetime, budget_ms: float, cal: BusinessCalendar | None = None) -> datetime:
    """The instant at which `budget_ms` of working time will have passed.

    The inverse of `business_ms_between`, and the reason it exists separately:
    a timeline plots a breach as a point on the wall clock, and
    `submitted + sla_hours` is only that point when the team works round the
    clock.
    """
    cal = cal or calendar_from_settings()
    start = _aware(start)
    if budget_ms <= 0:
        return start
    if not cal.enabled or not cal.usable:
        return start + timedelta(milliseconds=budget_ms)

    remaining = budget_ms
    local = start.astimezone(cal.tz)
    day = local.date()
    for _ in range(_MAX_DAYS):
        if day.weekday() in cal.working_days:
            opens = datetime.combine(day, time(cal.start_hour), tzinfo=cal.tz)
            closes = datetime.combine(day, time(cal.end_hour), tzinfo=cal.tz)
            lo = max(opens, local)
            if closes > lo:
                available = (closes - lo).total_seconds() * 1000.0
                if remaining <= available:
                    return (lo + timedelta(milliseconds=remaining)).astimezone(UTC)
                remaining -= available
        day += timedelta(days=1)
    # ponytail: the cap is a guard, not a policy. A budget this large means the
    # calendar is misconfigured; returning the wall-clock projection keeps the
    # timeline drawable instead of returning nothing.
    return start + timedelta(milliseconds=budget_ms)
