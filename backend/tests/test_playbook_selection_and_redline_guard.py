"""A review must use the right playbook, and only rules may rewrite a contract.

Guards the REQ-4249 corruption: the NDA playbook was auto-selected for an MSA
(the picker fell back to an arbitrary published playbook), the model correctly
reported "wrong playbook — cannot evaluate", and the redline builder then applied
that commentary as a document edit — striking the title and parties clause and
inserting "Apply the correct playbook for Master Services Agreements…" while
leaving all six genuinely risky clauses untouched.
"""

from app.playbooks.service import _type_tokens


class _Rule:
    """Stand-in for PlaybookRule — the guard only tests presence, not content."""

    id = "rule-1"


class _Dev:
    def __init__(self, rule):
        self.rule = rule


def _applicable(evaluated):
    """Mirrors the call-site guard in run_playbook_review."""
    return any(ev.rule is not None for ev in evaluated)


def test_abbreviation_and_long_form_pick_the_same_playbook():
    # "Master Services Agreement" vs "Standard MSA Playbook" shared no substring,
    # so the picker fell through to an arbitrary playbook.
    msa_playbook = _type_tokens("Standard MSA Playbook")
    assert _type_tokens("Master Services Agreement") & msa_playbook
    assert _type_tokens("MSA") & msa_playbook
    assert _type_tokens("Master Service Agreement") & msa_playbook


def test_nda_forms_all_match_the_nda_playbook():
    nda_playbook = _type_tokens("NDA Negotiation Playbook")
    for ct in ("NDA", "Non-Disclosure Agreement", "Mutual Non-Disclosure Agreement"):
        assert _type_tokens(ct) & nda_playbook, ct


def test_unrelated_contract_types_do_not_match_the_nda_playbook():
    # The actual damage: a DPA, SoW or amendment reviewed against NDA rules.
    nda_playbook = _type_tokens("NDA Negotiation Playbook")
    for ct in (
        "Data Processing Agreement",
        "DPA",
        "Statement of Work",
        "Amendment",
        "Business Process Outsourcing Agreement",
    ):
        assert not (_type_tokens(ct) & nda_playbook), ct


def test_generic_words_alone_never_constitute_a_match():
    # Nearly every contract type ends in "Agreement" and every playbook is a
    # "Playbook"; if those counted, everything would match everything.
    assert _type_tokens("Agreement") == set()
    assert _type_tokens("Standard Playbook") == set()
    assert _type_tokens(None) == set()


def test_deviation_without_a_backing_rule_cannot_rewrite_the_document():
    # "contract_type_mismatch" is model commentary, not contract language: it has
    # no playbook rule behind it, so it must not produce a redline.
    assert _applicable([_Dev(rule=None)]) is False


def test_rule_backed_deviation_still_produces_a_redline():
    assert _applicable([_Dev(rule=_Rule())]) is True
    # A real finding alongside commentary must still be applied.
    assert _applicable([_Dev(rule=None), _Dev(rule=_Rule())]) is True
