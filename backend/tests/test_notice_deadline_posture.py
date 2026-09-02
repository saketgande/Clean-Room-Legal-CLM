"""Deadline posture is the one piece of the notice register with real branching,
and it's the piece a lawyer actually relies on — "am I about to blow a statutory
deadline?". It's deliberately derived on read rather than stored, so these pin
the arithmetic and, more importantly, the two cases that are easy to get wrong:

  * a notice we've already answered must stop reporting 'overdue' forever;
  * a notice with no deadline must report 'none', not a false 'on_track', so it
    never inflates the register's overdue/at-risk counts.
"""

from datetime import date

from app.notices.models import Notice
from app.notices.service import AT_RISK_DAYS, deadline_posture

TODAY = date(2026, 8, 7)


def _notice(**kwargs) -> Notice:
    defaults = {
        "subject": "Demand notice",
        "counterparty_name": "Northwind Traders GmbH",
        "status": "open",
        "responded_at": None,
        "response_due_date": None,
    }
    return Notice(**{**defaults, **kwargs})


def test_no_due_date_is_none_not_on_track():
    posture, days = deadline_posture(_notice(), today=TODAY)
    assert posture == "none"
    assert days is None


def test_future_deadline_is_on_track():
    posture, days = deadline_posture(
        _notice(response_due_date=date(2026, 9, 1)), today=TODAY
    )
    assert posture == "on_track"
    assert days == 25


def test_deadline_inside_the_window_is_at_risk():
    # Exactly on the boundary — at_risk is inclusive, so AT_RISK_DAYS out counts.
    boundary = date(2026, 8, 7 + AT_RISK_DAYS)
    posture, days = deadline_posture(
        _notice(response_due_date=boundary), today=TODAY
    )
    assert posture == "at_risk"
    assert days == AT_RISK_DAYS


def test_deadline_just_outside_the_window_is_on_track():
    posture, _ = deadline_posture(
        _notice(response_due_date=date(2026, 8, 7 + AT_RISK_DAYS + 1)), today=TODAY
    )
    assert posture == "on_track"


def test_today_is_at_risk_not_overdue():
    posture, days = deadline_posture(_notice(response_due_date=TODAY), today=TODAY)
    assert posture == "at_risk"
    assert days == 0


def test_past_deadline_is_overdue_with_negative_days():
    posture, days = deadline_posture(
        _notice(response_due_date=date(2026, 7, 28)), today=TODAY
    )
    assert posture == "overdue"
    assert days == -10


def test_responded_notice_is_met_even_when_past_due():
    """The regression that matters: answering a notice must silence the alarm."""
    posture, _ = deadline_posture(
        _notice(
            response_due_date=date(2026, 7, 28),
            responded_at=date(2026, 7, 20),
            status="responded",
        ),
        today=TODAY,
    )
    assert posture == "met"


def test_closed_notice_is_met_even_when_past_due_and_never_answered():
    """Closed without a reply (withdrawn, settled, superseded) must also stop
    reporting overdue — otherwise the register accumulates permanent red rows."""
    posture, _ = deadline_posture(
        _notice(response_due_date=date(2026, 7, 28), status="closed"), today=TODAY
    )
    assert posture == "met"
