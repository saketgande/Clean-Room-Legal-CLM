"""A review must not propose a change that changes nothing.

A review that flags a clause already holding the playbook's own wording used to get a change card
anyway: strike the clause, insert the same sentence (dropping only its "1. Term." label). After a
person accepted the playbook's wording, the automatic re-check flagged it again and offered the
same change, so the Review tab never emptied. The finding stays (the person decides what to do
with it); only the pointless change is not proposed.

Runs against Postgres inside one rolled-back transaction; the AI answer is a fake.
"""

from __future__ import annotations

from sqlalchemy import select

import app.models  # noqa: F401  (register every mapper)
from app.contract_files.models import ContractEdit
from app.playbooks.models import PlaybookDeviation
from app.playbooks.service import is_already_the_wording
from tests import test_nda_fast_lane
from tests.test_current_findings import World

db = test_nda_fast_lane.db  # the same rolled-back-transaction fixture

SAMPLE = "This agreement lasts two (2) years from the date of signature."
OTHER = "2. Liability. Each party is liable for direct damages only."


def _text(term_clause: str) -> str:
    return f"MUTUAL NDA\n\n{term_clause}\n\n{OTHER}\n"


def _review(db, term_clause: str, *, also_liability: bool = False):
    w = World(db, samples={"term": SAMPLE, "liability": "Liability is limited to direct damages."})
    w.text(_text(term_clause), 1)
    devs = [w.dev(1, "term", "medium", "Term should match the playbook", term_clause)]
    if also_liability:
        devs.append(w.dev(2, "liability", "medium", "Liability should match the playbook", OTHER))
    art = w.review(*devs, redline=True)
    edits = list(db.scalars(select(ContractEdit).where(ContractEdit.contract_id == w.contract.id)))
    findings = list(db.scalars(select(PlaybookDeviation).where(PlaybookDeviation.contract_id == w.contract.id)))
    return art, edits, findings


def test_a_clause_that_already_says_the_playbook_wording_gets_no_change(db):
    art, edits, findings = _review(db, f"1. Term. {SAMPLE}")
    assert edits == []
    assert art.redline_version is None and art.contract_edit is None
    assert len(findings) == 1 and findings[0].status == "open"  # the finding stays for a person to close


def test_it_makes_no_difference_whether_the_clause_carries_a_label(db):
    for clause in (SAMPLE, f"1. {SAMPLE}", f"1.1 Term of Agreement: {SAMPLE}", f"(a) {SAMPLE}", f"Section 4. {SAMPLE}"):
        _, edits, _ = _review(db, clause)
        assert edits == [], clause


def test_spacing_case_and_the_closing_full_stop_do_not_matter(db):
    _, edits, _ = _review(db, "1. Term. This   agreement lasts TWO (2) years\nfrom the date of signature")
    assert edits == []


def test_a_clause_that_differs_from_the_playbook_still_gets_its_change(db):
    art, edits, _ = _review(db, "1. Term. This agreement lasts five (5) years from the date of signature.")
    assert len(edits) == 1 and edits[0].replacement_text == SAMPLE
    assert art.redline_version is not None


def test_extra_wording_before_the_playbook_wording_is_still_a_change(db):
    """The clause says more than the playbook does, so it is not "already the wording"."""
    _, edits, _ = _review(db, f"1. Term. Either party may terminate this agreement at will. {SAMPLE}")
    assert len(edits) == 1


def test_a_short_sentence_before_it_is_not_mistaken_for_a_heading(db):
    _, edits, _ = _review(db, f"1. Term. Liability is unlimited. {SAMPLE}")
    assert len(edits) == 1


def test_extra_wording_after_the_playbook_wording_is_still_a_change(db):
    _, edits, _ = _review(db, f"1. Term. {SAMPLE} Either party may extend it by notice.")
    assert len(edits) == 1


def test_only_the_changes_that_change_something_are_proposed(db):
    """Two findings, one clause already right: one change, and the proposal text carries only that one."""
    art, edits, findings = _review(db, f"1. Term. {SAMPLE}", also_liability=True)
    assert [e.replacement_text for e in edits] == ["Liability is limited to direct damages."]
    assert len(findings) == 2
    assert art.redline_version is not None


def test_wording_that_is_already_in_the_contract_is_not_inserted_a_second_time(db):
    """A "missing clause" finding (no passage to strike) for wording that is already a clause."""
    w = World(db, samples={"term": SAMPLE})
    w.text(_text(f"1. Term. {SAMPLE}"), 1)
    missing = w.dev(1, "term", "medium", "No term clause", "MUTUAL NDA")
    missing.original_text = None
    missing.citations = []
    art = w.review(missing, redline=True)
    assert list(db.scalars(select(ContractEdit).where(ContractEdit.contract_id == w.contract.id))) == []
    assert art.redline_version is None


class TestAlreadyTheWording:
    def test_the_rule_in_one_place(self):
        assert is_already_the_wording(f"1. Term. {SAMPLE}", SAMPLE)
        assert is_already_the_wording(SAMPLE, SAMPLE)
        assert not is_already_the_wording("1. Term. This agreement lasts five (5) years.", SAMPLE)
        assert not is_already_the_wording("", SAMPLE)
        assert not is_already_the_wording(SAMPLE, "")
        # a label may be numbering and a short Title Case heading, nothing more
        assert is_already_the_wording(f"12.3 Limitation of Liability. {SAMPLE}", SAMPLE)
        assert is_already_the_wording(f"ARTICLE 4 TERM. {SAMPLE}", SAMPLE)
        assert not is_already_the_wording(f"1. Term. Time is of the essence. {SAMPLE}", SAMPLE)
        assert not is_already_the_wording(f"Notwithstanding anything else. {SAMPLE}", SAMPLE)
