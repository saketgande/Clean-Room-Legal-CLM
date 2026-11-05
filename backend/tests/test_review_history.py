"""The review history keeps the earlier review and its decisions on the version they were made on.

Failure mode guarded: after a new version arrives the risk band reads "not reviewed" and the earlier
review and its decisions vanish from the page, so nobody can see what was already decided, or on which
version. Also that the "not reviewed" note names the two versions instead of saying only "changed".
"""

from app.contract_files.models import ContractEdit
from app.contracts.review_history import review_history
from app.contracts.risk import contract_risk
from app.playbooks.models import PlaybookDecision, PlaybookDeviation
from tests.test_one_risk_signal import (  # noqa: F401  (db is the rolled-back session fixture)
    World,
    db,
)


def test_earlier_review_and_decisions_stay_on_their_version(db):  # noqa: F811
    w = World(db)
    v1 = w.version()
    run = w.run(v1, devs=(("high", "rejected"),))
    dev = db.query(PlaybookDeviation).filter_by(playbook_run_id=run.id).one()
    db.add(PlaybookDecision(org_id=w.org_id, playbook_deviation_id=dev.id, decision="rejected",
                            rationale="Not acceptable", decided_by_user_id=w.contract.owner_user_id))
    db.add(ContractEdit(org_id=w.org_id, contract_id=w.contract.id, contract_version_id=v1.id,
                        edit_type="playbook_redline", status="rejected", original_text="Unlimited"))
    v2 = w.version("MUTUAL NDA\n1. Liability. Capped.\n")
    db.flush()

    hist = review_history(db, w.contract)
    assert [h["version_number"] for h in hist] == [2, 1]
    assert hist[0]["is_current"] and hist[0]["reviews"] == [] and hist[0]["edits"] == []
    old = hist[1]
    assert old["version_id"] == v1.id and not old["is_current"]
    finding = old["reviews"][0]["findings"][0]
    assert (finding["decision"], finding["rationale"]) == ("rejected", "Not acceptable")
    assert old["edits"][0]["status"] == "rejected"

    note = contract_risk(db, w.contract)["note"]
    assert f"version {v2.version_number}" in note and f"version {v1.version_number}" in note
