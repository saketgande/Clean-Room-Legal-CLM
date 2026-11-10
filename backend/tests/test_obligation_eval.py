"""The obligation answer key and its free scorer (eval/obligations). No database, no network, no model.

These pin the instrument itself: a key that rots when its text changes, a scorer bug no real model would
reveal, and the rules that decide whether a row or an atom counts."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

import app.models  # noqa: F401  (register every mapper)
from eval.obligations import adapters, gold, score
from eval.obligations.gold import Atom, Gold, NotDuty

KEYS = gold.key_files()
PARTIES = {"us": {"names": ["Acme Ltd"], "roles": ["Customer"]},
           "them": {"names": ["Supplier Inc"], "roles": ["Provider"]}}


def _tiny(text: str, atoms=(), not_duties=(), anchors=None) -> Gold:
    return Gold(id="tiny", title="tiny", text=text, text_sha256="x", status={"state": "draft"}, provenance={},
                parties=PARTIES, anchors=anchors or {}, atoms=list(atoms), not_duties=list(not_duties))


def _atom(text: str, quote: str, ident="A1", kind="duty", owed="them", timing=None) -> Atom:
    start = text.index(quote)
    return Atom(ident, "1.1", quote, owed, kind, "t", timing or {"kind": "ongoing"}, start, start + len(quote))


def test_there_is_at_least_one_key():
    """Guards the checks passing because the folder is empty."""
    assert KEYS


@pytest.mark.parametrize("path", KEYS, ids=lambda p: p.name)
def test_every_gold_file_passes_check(path):
    """Guards a key quietly rotting when its text file is edited, a quote or clause number going stale,
    or a duty sentence nobody classified."""
    assert gold.problems(path) == []
    assert gold.lint(gold.load(path)) == []


def test_a_key_whose_text_changed_fails_the_check(tmp_path):
    """Guards scoring against a text the key was not written for."""
    raw = json.loads(KEYS[0].read_text(encoding="utf-8"))
    raw["text_sha256"] = "0" * 64
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(raw), encoding="utf-8")
    assert any("text_sha256" in issue for issue in gold.problems(changed))


def test_lint_flags_a_duty_sentence_with_no_atom_or_not_duty():
    """Guards the lawyer skipping a sentence: a duty verb outside every atom is a hole in the key."""
    text = "1. The Supplier shall deliver reports monthly. The sky is blue.\n"
    assert gold.lint(_tiny(text)) == ["The Supplier shall deliver reports monthly."]
    covered = _tiny(text, atoms=[_atom(text, "The Supplier shall deliver reports monthly")])
    assert gold.lint(covered) == []


@pytest.mark.parametrize("key", [gold.load(p) for p in KEYS], ids=lambda k: k.id)
def test_gold_scored_as_an_extraction_is_perfect(key):
    """The oracle. Guards a scorer bug that no real model would ever reveal."""
    result = score.score(key, adapters.rows_from_gold(key))
    assert result["recall"] == 1 and result["precision"] == 1
    assert result["side"]["value"] == 1 and result["invented_dates"] == []
    assert result["timing"]["kind"]["value"] in (1, None)


@pytest.mark.parametrize("key", [gold.load(p) for p in KEYS], ids=lambda k: k.id)
def test_empty_extraction_scores_zero_recall_and_no_invented_dates(key):
    """Guards an empty answer reading as a pass."""
    result = score.score(key, [])
    assert result["recall"] == 0 and result["precision"] is None and result["invented_dates"] == []
    assert len(result["missed"]) == result["atoms"]


def test_a_row_quoting_a_whole_clause_is_credited_but_shows_as_bundling():
    """Guards bundling hiding behind a good recall: one row over a clause with three duties finds all three,
    and says so (atoms per true row above 1), so a redesign can be judged either way."""
    text = "1. Supplier shall pay fees. Supplier shall keep records. Supplier shall give notice.\n"
    atoms = [_atom(text, q, f"A{i}") for i, q in enumerate(
        ["Supplier shall pay fees.", "Supplier shall keep records.", "Supplier shall give notice."])]
    rows = [{"quote": text.strip(), "responsible_party": "Supplier Inc"}]
    result = score.score(_tiny(text, atoms), rows)
    assert (result["found"], result["atoms"], result["rows"]) == (3, 3, 1)
    assert result["atoms_per_true_row"] == 3


def test_a_date_on_a_duty_the_key_says_needs_a_person_is_counted_as_invented():
    """Guards a model-made or wrongly counted date passing silently: the key's own date is the only truth."""
    text = "1. Supplier shall deliver the report promptly.\n"
    key = _tiny(text, [_atom(text, "Supplier shall deliver the report promptly.")])
    rows = [{"quote": "Supplier shall deliver the report promptly.", "responsible_party": "Supplier Inc",
             "due_date": "2026-12-01"}]
    result = score.score(key, rows)
    assert result["invented_dates"] == [{"row": 0, "field": "due_date", "row_value": "2026-12-01",
                                         "key_value": None, "kind": "invented"}]


def test_a_row_is_classed_by_what_its_quote_touches():
    """Guards precision: a right, a condition, a promise and a made-up quote must not count as true duties."""
    text = ("1. Supplier shall pay fees. Buyer may withhold disputed sums. Notice must precede termination. "
            "Supplier grants a licence to the Buyer.\n")
    key = _tiny(
        text,
        atoms=[_atom(text, "Supplier shall pay fees."),
               _atom(text, "Notice must precede termination.", "C1", "condition"),
               _atom(text, "Supplier grants a licence to the Buyer.", "K1", "commitment")],
        not_duties=[NotDuty("N1", "1.1", "Buyer may withhold disputed sums.", "right",
                            text.index("Buyer may withhold"), text.index("Buyer may withhold") + 33)],
    )
    rows = [{"quote": "Supplier shall pay fees."}, {"quote": "Buyer may withhold disputed sums."},
            {"quote": "Notice must precede termination."}, {"quote": "Supplier grants a licence to the Buyer."},
            {"quote": "Something the contract never says at all."}]
    result = score.score(key, rows)
    assert result["row_classes"] == {"duty": 1, "not_a_duty": 1, "condition_only": 1, "commitment_only": 1,
                                     "quote_not_found": 1}
    assert result["precision"] == pytest.approx(0.2)


def test_the_key_due_date_is_what_production_counts():
    """Guards drift between the key and the code: the due date of an atom is production's obligation_deadline
    over the atom's timing and the key's anchors, never a typed value."""
    from app.ai.schemas import ObligationTiming
    from app.core.deadlines import Anchor
    from app.obligations.service import obligation_deadline

    text = "1. Supplier shall report within twelve (12) months of the Effective Date.\n"
    timing = {"kind": "deadline", "period_number": 12, "period_unit": "months", "direction": "after",
              "anchor": "effective_date", "stated_in_text": True}
    atom = _atom(text, "Supplier shall report within twelve (12) months of the Effective Date.", timing=timing)
    key = _tiny(text, [atom], anchors={"effective_date": "2026-10-01"})
    direct = obligation_deadline(
        ObligationTiming(**timing),
        {"effective_date": Anchor("effective_date", date(2026, 10, 1), "user"),
         "expiration_date": Anchor("expiration_date", None, "user"),
         "signature_date": Anchor("signature_date", None, "user")},
        quote=atom.quote)
    assert gold.atom_deadline(key, atom) == direct and direct["due"] == "2027-10-01"


def test_locate_tries_exact_then_spacing_then_fuzzy():
    """Guards a quote that differs only in spacing or a typo being counted as not found."""
    text = "Supplier  shall pay\nthe fees within thirty (30) days."
    assert score.locate("shall pay\nthe fees", text)[2] == "exact"
    assert score.locate("shall pay the fees", text)[2] == "spacing"
    assert score.locate("Supplier shall pay the fees within thirty (30) dayz.", text)[2] == "fuzzy"
    assert score.locate("nothing like it", text) is None
    assert score.locate("", text) is None


@pytest.mark.parametrize("said, side", [
    ("Supplier Inc (Provider)", "them"), ("Acme Ltd", "us"), ("The Customer", "us"),
    ("Either Party (Acme Ltd or Supplier Inc)", "both"), ("each party", "both"), ("Mutual", "both"),
    ("The Auditor", None), ("", None), (None, None)])
def test_side_of_reads_party_names_roles_and_both_words(said, side):
    """Guards the side check: a responsible_party maps to us, them, both or unknown."""
    assert score.side_of(said, PARTIES) == side


def test_adapters_give_stored_rows_and_a_model_answer_the_same_plain_rows():
    """Guards the scorer seeing different shapes from different pipelines: both adapters make the same keys."""
    stored = SimpleNamespace(
        source_citation={"quote": "Supplier shall pay."}, responsible_party="Supplier Inc", description="Pay",
        obligation_type="payment", due_date=date(2026, 12, 1),
        metadata_json={"deadline": {"facts": {"kind": "deadline", "period_number": 30}, "ends_on": None}})

    class FakeDb:
        def scalars(self, _stmt):
            return SimpleNamespace(all=lambda: [stored])

    from_stored = adapters.rows_from_stored(FakeDb(), "c-1")[0]
    from_output = adapters.rows_from_output({"obligations": [{
        "description": "Pay", "responsible_party": "Supplier Inc", "obligation_type": "payment",
        "timing": {"kind": "ongoing"}, "citations": [{"quote": "Supplier shall pay."}]}]})[0]
    assert from_stored.keys() == from_output.keys()
    assert from_stored["due_date"] == "2026-12-01" and from_stored["timing"]["period_number"] == 30
    assert from_output["quote"] == "Supplier shall pay." and from_output["due_date"] is None
