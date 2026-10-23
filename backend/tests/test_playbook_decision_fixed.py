"""The "fixed" playbook decision: we held our position and our wording is in.

A decision describes the counterparty's deviation: accepted / accepted_fallback /
waived concede it, rejected / escalated hold it. The Review tab used to record
"accepted" when the lawyer accepted OUR suggested wording, so insights read every
enforced rule as conceded and recommended loosening it.
"""

import asyncio

from app.playbooks.models import Playbook, PlaybookDeviation
from app.playbooks.schemas import PlaybookDecisionCreate
from app.playbooks.service import MIN_DECIDED_FOR_INSIGHTS, PlaybooksService
from tests.test_nda_fast_lane import _nda, add_review, add_version, db  # noqa: F401


def test_the_close_issue_post_accepts_fixed():
    """The Review tab's "Close issue" posts "fixed" through decide_deviation; without it in
    the schema the post is a 422 and the issue never closes."""
    assert PlaybookDecisionCreate(decision="fixed").decision == "fixed"


def test_insights_count_fixed_as_held_not_conceded(db):  # noqa: F811
    """A rule the lawyers enforced was counted as conceded and fed to the recommendations
    model as a reason to weaken it."""
    contract = _nda(db)
    run = add_review(db, contract, add_version(db, contract, 1))
    for _ in range(3):
        db.add(PlaybookDeviation(
            org_id=contract.org_id, playbook_run_id=run.id, contract_id=contract.id,
            clause_type="confidentiality", severity="high", issue="One-way", status="fixed",
        ))
    db.flush()
    playbook = db.get(Playbook, run.playbook_id)
    # Below the gate, so no AI call is made; the stats are still returned.
    assert 3 < MIN_DECIDED_FOR_INSIGHTS
    result = asyncio.run(
        PlaybooksService(db).compute_playbook_insights(playbook=playbook, user=None)
    )
    assert result["ready"] is False
    [stats] = result["stats"]
    assert (stats["held"], stats["concessions"]) == (3, 0)
