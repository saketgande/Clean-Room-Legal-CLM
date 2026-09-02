"""The reminder sweep has no per-reminder table — it decides what to send by
comparing a notice's current milestone against the most urgent one already
stamped on it. That comparison is the whole idempotency mechanism, so these pin
both halves: the milestone thresholds, and the rank ordering that stops the
daily job re-sending (or, worse, walking backwards from 'overdue' to 't7').
"""

import pytest

from app.notices.models import REMINDER_STAGE_RANK, REMINDER_STAGES
from app.notices.service import AT_RISK_DAYS, reminder_stage_for


# --- thresholds ------------------------------------------------------------

@pytest.mark.parametrize(
    "days,expected",
    [
        (30, None),      # far out — not worth chasing yet
        (8, None),       # just outside the first milestone
        (7, "t7"),
        (5, "t7"),
        (4, "t7"),
        (AT_RISK_DAYS, "t3"),   # 3 — boundary belongs to the more urgent stage
        (1, "t3"),
        (0, "due"),
        (-1, "overdue"),
        (-90, "overdue"),
    ],
)
def test_milestone_thresholds(days, expected):
    assert reminder_stage_for(days) == expected


def test_no_deadline_never_produces_a_milestone():
    assert reminder_stage_for(None) is None


def test_t3_boundary_matches_the_registers_at_risk_window():
    """The email and the amber 'at risk' badge must agree — if these drift, the
    register shows a warning nobody was told about, or vice versa."""
    assert reminder_stage_for(AT_RISK_DAYS) == "t3"
    assert reminder_stage_for(AT_RISK_DAYS + 1) == "t7"


# --- rank ordering (the idempotency guard) ---------------------------------

def test_ranks_are_strictly_escalating():
    ranks = [REMINDER_STAGE_RANK[s] for s in REMINDER_STAGES]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)  # no ties — a tie would let one stage
                                          # re-send forever or skip another


def test_never_reminded_ranks_below_every_stage():
    for stage in REMINDER_STAGES:
        assert REMINDER_STAGE_RANK[None] < REMINDER_STAGE_RANK[stage]


def test_resending_the_same_stage_is_suppressed():
    # The sweep's condition is: send only if rank(current) > rank(last).
    for stage in REMINDER_STAGES:
        assert not REMINDER_STAGE_RANK[stage] > REMINDER_STAGE_RANK[stage]


def test_a_notice_already_chased_as_overdue_is_never_re_chased_at_a_lower_stage():
    """Guards the ordering bug that would matter most: once we've said 'you
    missed it', nothing may downgrade that to 'due in 7 days'."""
    for earlier in ("t7", "t3", "due"):
        assert not REMINDER_STAGE_RANK[earlier] > REMINDER_STAGE_RANK["overdue"]


def test_escalation_still_fires_when_a_notice_moves_up_a_stage():
    # A notice chased at t7 must still be chased again at t3, due, and overdue.
    assert REMINDER_STAGE_RANK["t3"] > REMINDER_STAGE_RANK["t7"]
    assert REMINDER_STAGE_RANK["due"] > REMINDER_STAGE_RANK["t3"]
    assert REMINDER_STAGE_RANK["overdue"] > REMINDER_STAGE_RANK["due"]


def test_a_notice_filed_close_to_its_deadline_skips_straight_to_the_right_stage():
    """Filing something due tomorrow must not send a retroactive 't7' first —
    the rank check jumps it straight to t3."""
    assert reminder_stage_for(1) == "t3"
    assert REMINDER_STAGE_RANK["t3"] > REMINDER_STAGE_RANK[None]
