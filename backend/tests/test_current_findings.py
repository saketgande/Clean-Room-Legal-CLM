"""One list of open findings: the review that counts for the CURRENT text.

Before this, the Review tab and the request page listed every open issue of every
review ever run on the contract, while the risk band (contracts.risk.contract_risk)
counted only the newest review of the current text. After a second review the
header said "2 findings" and the list held 4 (each real problem once from the old
review and once from the new). Everything here reads the same set of runs now.

Runs against Postgres inside one rolled-back transaction; the AI answer is a fake,
so nothing is spent.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai.schemas import PlaybookDeviationOutput, PlaybookReviewOutput
from app.auth.models import User
from app.contract_files import issue_fixes
from app.contract_files.models import ContractTextSnapshot
from app.contracts import routes as contract_routes
from app.contracts.risk import contract_risk
from app.contracts.service import ContractService
from app.core.enums import PlaybookStatus
from app.playbooks.models import Playbook, PlaybookRule, PlaybookVersion
from app.playbooks.service import PlaybooksService, execute_playbook_run
from tests import test_nda_fast_lane
from tests.test_nda_fast_lane import _nda, add_version

db = test_nda_fast_lane.db  # the same rolled-back-transaction fixture

TEXT_1 = (
    "MUTUAL NDA\n"
    "1. Term. This agreement lasts five (5) years from the date of signature.\n"
    "2. Liability. Liability of each party is unlimited.\n"
    "3. Governing law. This agreement is governed by the laws of Mars.\n"
)
TEXT_2 = TEXT_1.replace("five (5) years", "two (2) years")


class World:
    def __init__(
        self, db: Session, *, status: str = PlaybookStatus.PUBLISHED, rules: bool = True,
        samples: dict[str, str] | None = None,
    ):
        self.db = db
        self.contract = _nda(db)
        self.user = db.get(User, self.contract.owner_user_id)
        self.playbook = Playbook(org_id=self.contract.org_id, name="NDA playbook", status=status)
        db.add(self.playbook)
        db.flush()
        self.pv = PlaybookVersion(
            org_id=self.contract.org_id, playbook_id=self.playbook.id, version_number=1, status=status
        )
        db.add(self.pv)
        db.flush()
        self.playbook.current_version_id = self.pv.id
        if rules:
            for clause in ("term", "liability", "governing_law"):
                db.add(PlaybookRule(
                    org_id=self.contract.org_id, playbook_version_id=self.pv.id, clause_type=clause,
                    rule_type="standard_position", risk_level="medium",
                    sample_clause=(samples or {}).get(clause),
                ))
        else:
            db.add(PlaybookRule(
                org_id=self.contract.org_id, playbook_version_id=self.pv.id, clause_type="liability",
                rule_type="prohibited_language", prohibited_language="unlimited", risk_level="high",
            ))
        db.flush()
        self.version = None

    def text(self, text: str, number: int):
        """Make `text` the contract's current authoritative text."""
        if self.version is not None:
            self.version.is_authoritative = False
            self.db.flush()
        version = add_version(self.db, self.contract, number)
        version.is_authoritative = True
        snap = ContractTextSnapshot(
            org_id=self.contract.org_id, contract_id=self.contract.id, contract_version_id=version.id,
            extraction_method="text", text=text,
        )
        self.db.add(snap)
        self.db.flush()
        version.text_snapshot_id = snap.id
        self.contract.current_authoritative_version_id = version.id
        self.db.flush()
        self.version = version
        self.current_text = text
        return version

    def dev(self, rule_index: int, clause: str, severity: str, issue: str, quote: str):
        start = self.current_text.index(quote)
        return PlaybookDeviationOutput.model_validate({
            "rule_index": rule_index, "clause_type": clause, "severity": severity, "issue": issue,
            "original_text": quote, "suggested_fix": f"Fix {clause}",
            "citations": [{"quote": quote, "start_char": start, "end_char": start + len(quote)}],
        })

    def review(self, *deviations, ai: bool = True, redline: bool = False):
        out = PlaybookReviewOutput(deviations=list(deviations)) if ai else None
        return execute_playbook_run(
            self.db, user=self.user, playbook=self.playbook, version=self.pv, contract=self.contract,
            create_redline=redline, ai_output=out,
        )

    def decide(self, deviation, decision: str):
        PlaybooksService(self.db).record_deviation_decision(
            deviation=deviation, decision=decision, rationale="Because I said so.", user=self.user
        )

    def listed(self) -> list[dict]:
        svc = SimpleNamespace(get_contract_for_user=lambda **_k: self.contract)
        return contract_routes.get_contract_deviations(
            contract_id=self.contract.id, db=self.db, service=svc, current_user=self.user
        )


def _two_reviews(w: World):
    """Review 1 on the old text finds 3 problems; the person fixes the term one; the accepted
    text becomes current; review 2 finds the two untouched problems again, worded differently."""
    w.text(TEXT_1, 1)
    art = w.review(
        w.dev(1, "term", "medium", "Term is five years", "five (5) years"),
        w.dev(2, "liability", "high", "Liability is unlimited", "Liability of each party is unlimited"),
        w.dev(3, "governing_law", "low", "Governing law is Mars", "laws of Mars"),
    )
    w.decide(art.deviations[0], "fixed")
    w.text(TEXT_2, 2)
    w.review(
        w.dev(2, "liability", "high", "Unlimited liability exposure", "Liability of each party is unlimited"),
        w.dev(3, "governing_law", "low", "Governing law is not a real jurisdiction", "laws of Mars"),
    )
    return art


def test_the_list_holds_only_the_review_of_the_current_text(db):
    w = World(db)
    _two_reviews(w)

    listed = w.listed()

    assert sorted(d["issue"] for d in listed) == [
        "Governing law is not a real jurisdiction",
        "Unlimited liability exposure",
    ]


def test_the_list_and_the_risk_band_count_the_same_findings(db):
    w = World(db)
    _two_reviews(w)

    counts = contract_risk(db, w.contract)["counts"]

    assert len(w.listed()) == sum(counts.values()) == 2


def test_the_checklist_counts_the_same_findings(db):
    w = World(db)
    _two_reviews(w)

    status = ContractService(db).compute_review_status(contract=w.contract)
    issues = next(item for item in status["checklist"] if item["key"] == "issues")

    assert issues["count"] == 2
    assert issues["detail"] == "1 high/critical · advisory"


def test_draft_fixes_for_all_skips_findings_from_older_reviews(db):
    w = World(db)
    _two_reviews(w)

    waiting = issue_fixes.issues_needing_change(db, contract=w.contract)

    assert sorted(d.issue for d in waiting) == [
        "Governing law is not a real jurisdiction",
        "Unlimited liability exposure",
    ]


def test_while_the_text_is_unreviewed_the_old_findings_are_not_shown(db):
    """Right after Accept, before the re-check lands, the old issues describe text that is gone."""
    w = World(db)
    w.text(TEXT_1, 1)
    w.review(w.dev(2, "liability", "high", "Liability is unlimited", "Liability of each party is unlimited"))
    w.text(TEXT_2, 2)

    assert w.listed() == []
    assert contract_risk(db, w.contract)["band"] == "unknown"


def test_a_signed_copy_with_the_same_text_keeps_the_review(db):
    """A new version with byte-identical text did not change what was reviewed."""
    w = World(db)
    w.text(TEXT_1, 1)
    w.review(w.dev(2, "liability", "high", "Liability is unlimited", "Liability of each party is unlimited"))
    w.text(TEXT_1, 2)

    assert [d["issue"] for d in w.listed()] == ["Liability is unlimited"]


def test_a_keyword_scan_of_the_current_text_is_still_listed(db):
    """No AI review qualifies (the band is unknown), but the scan's findings are the only ones there are."""
    w = World(db, rules=False)
    w.text(TEXT_1, 1)
    w.review(ai=False)

    assert contract_risk(db, w.contract)["band"] == "unknown"
    assert [d["clause_type"] for d in w.listed()] == ["liability"]


def test_a_keyword_scan_of_older_text_is_not_listed(db):
    w = World(db, rules=False)
    w.text(TEXT_1, 1)
    w.review(ai=False)
    w.text(TEXT_2, 2)

    assert w.listed() == []


def test_a_test_run_against_a_draft_playbook_is_not_a_finding_of_this_contract(db):
    w = World(db, status=PlaybookStatus.DRAFT, rules=False)
    w.text(TEXT_1, 1)
    w.review(ai=False)

    assert w.listed() == []


def test_a_decided_finding_of_the_current_review_leaves_the_list(db):
    w = World(db)
    w.text(TEXT_1, 1)
    art = w.review(
        w.dev(2, "liability", "high", "Liability is unlimited", "Liability of each party is unlimited"),
        w.dev(3, "governing_law", "low", "Governing law is Mars", "laws of Mars"),
    )
    w.decide(art.deviations[1], "waived")

    assert [d["issue"] for d in w.listed()] == ["Liability is unlimited"]


def test_a_contract_no_playbook_covers_still_lists_the_findings_of_a_run_on_its_text(db):
    """A person can run a playbook by hand on a contract whose type no playbook is named for."""
    w = World(db)
    w.contract.contract_type = "zzz-unmatched"
    w.text(TEXT_1, 1)
    w.review(w.dev(2, "liability", "high", "Liability is unlimited", "Liability of each party is unlimited"))

    assert contract_risk(db, w.contract)["band"] == "unknown"
    assert [d["issue"] for d in w.listed()] == ["Liability is unlimited"]
