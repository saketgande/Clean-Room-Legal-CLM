"""The lawyer-confirmed baseline: what "confirmed" means, the sheet a lawyer signs, the named baseline's gates and what
`--check` re-verifies. Everything runs on a COPY of the draft key in a temporary folder and on a fake model: nothing here
can reach the network, spend money or change the committed key."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from html import unescape
from pathlib import Path

import anthropic
import httpx
import pytest

import app.models  # noqa: F401  (register every mapper)
from app.ai.gateway.features import feature_registry
from app.core import database
from app.core.config import settings
from app.integrations import claude
from eval.exam import runner as exam_runner
from eval.exam.env import ExamRefused
from eval.obligations import adapters, cli, gold, sheet
from eval.obligations import runner as rehearsal
from tests.test_ai_gateway import FakeProvider, _response
from tests.test_exam_gate import OWNER, Typist, make_live, scratch_database

REAL_ROOT = gold.ROOT
KEY_ID = "msa-small"
LAWYER = "Asha Rao"


# --- a copy of the key to work on ------------------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path, monkeypatch):
    """The committed draft key and its text, copied into a temporary folder that `gold` is pointed at."""
    folder = tmp_path / "eval-obligations"
    (folder / "gold").mkdir(parents=True)
    (folder / "texts").mkdir()
    shutil.copy(REAL_ROOT / "gold" / f"{KEY_ID}.json", folder / "gold" / f"{KEY_ID}.json")
    shutil.copy(REAL_ROOT / "texts" / f"{KEY_ID}.txt", folder / "texts" / f"{KEY_ID}.txt")
    monkeypatch.setattr(gold, "ROOT", folder)
    return folder


@pytest.fixture
def confirmed(root):
    """The same copy, confirmed by a lawyer through the file write the command uses."""
    path = root / "gold" / f"{KEY_ID}.json"
    gold.write_confirmation(path, by=LAWYER, at="2026-10-28", fingerprint=gold.load(path).fingerprint)
    return root


def _key(root_folder=None) -> gold.Gold:
    return gold.load(gold.ROOT / "gold" / f"{KEY_ID}.json")


def _edit_key(mutate) -> None:
    """Change the key file the way a person editing an atom would, leaving the status block alone."""
    path = gold.ROOT / "gold" / f"{KEY_ID}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    mutate(raw)
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")


# --- what "confirmed" means ------------------------------------------------------------------------------------------


def test_a_draft_key_is_a_draft_and_says_why(root):
    key = _key()
    assert (key.confirmed, key.effective_status) == (False, "draft")
    assert key.status_problem == "no lawyer has confirmed it yet"


def test_a_confirmed_key_is_confirmed_until_anything_it_covers_changes(confirmed):
    """Guards a confirmation outliving the key it was given to: the fingerprint covers the text, parties, anchors, atoms
    and not-duties, so a later edit to any of them makes the key a draft again without anyone having to say so."""
    assert _key().confirmed and _key().effective_status == "lawyer_confirmed" and _key().status_problem is None

    _edit_key(lambda raw: raw["atoms"][0].update(note="reworded after the lawyer signed"))

    key = _key()
    assert not key.confirmed and key.effective_status == "draft"
    assert "edited after it was confirmed" in key.status_problem


def test_a_change_to_the_prompt_metadata_does_not_unconfirm_the_key(confirmed):
    """The metadata changes what the model is shown, not what the right answer is (it is outside the fingerprint)."""
    _edit_key(lambda raw: raw["metadata"].update(title="A different title"))
    assert _key().confirmed


@pytest.mark.parametrize("field", ["confirmed_by", "confirmed_at"])
def test_a_confirmation_without_a_name_or_a_date_does_not_count(confirmed, field):
    """Guards hand-setting the state to "lawyer_confirmed" and nothing else: a person and a day must be on it."""
    _edit_key(lambda raw: raw["status"].update({field: ""}))
    assert not _key().confirmed and "who confirmed it and when" in _key().status_problem


def test_the_state_alone_is_not_a_confirmation(root):
    _edit_key(lambda raw: raw["status"].update(state="lawyer_confirmed"))
    assert not _key().confirmed


def test_the_scorecard_prints_the_effective_status_not_the_one_in_the_file(confirmed):
    from eval.obligations import score

    assert score.score(_key(), adapters.rows_from_gold(_key()))["key_status"] == "lawyer_confirmed"
    _edit_key(lambda raw: raw["atoms"][0].update(note="edited"))
    assert score.score(_key(), adapters.rows_from_gold(_key()))["key_status"] == "draft"


def test_writing_a_confirmation_touches_only_the_status_block(root):
    """Guards reformatting a hand-laid-out key file: a confirmation must be a small diff a reviewer can read."""
    path = root / "gold" / f"{KEY_ID}.json"
    before = path.read_text(encoding="utf-8")
    key = gold.load(path)

    gold.write_confirmation(path, by=LAWYER, at="2026-10-28", fingerprint=key.fingerprint)

    after = path.read_text(encoding="utf-8")
    start = before.index('"status"')
    end_before = before.index('"provenance"')
    end_after = after.index('"provenance"')
    assert before[:start] == after[:start] and before[end_before:] == after[end_after:]
    assert json.loads(after)["status"]["confirmed_fingerprint"] == key.fingerprint


def test_a_name_that_cannot_be_written_leaves_the_key_file_untouched(root):
    """Guards losing the only copy of a hand-built key: write_text truncates the file before it encodes, so a name with a
    lone surrogate (stdin can hold one) used to leave an empty key. The text is encoded first and the file replaced whole."""
    path = root / "gold" / f"{KEY_ID}.json"
    before = path.read_bytes()

    with pytest.raises(UnicodeEncodeError):
        gold.write_confirmation(path, by="Jos\udce9", at="2026-10-28", fingerprint=gold.load(path).fingerprint)

    assert path.read_bytes() == before and not list(path.parent.glob("*.tmp"))


def test_a_key_file_with_windows_line_endings_keeps_them(root):
    path = root / "gold" / f"{KEY_ID}.json"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    gold.write_confirmation(path, by=LAWYER, at="2026-10-28", fingerprint=gold.load(path).fingerprint)

    data = path.read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n") > 20 and gold.load(path).confirmed


# --- the confirm command ---------------------------------------------------------------------------------------------


def _typed(*answers):
    queue = list(answers)
    return lambda prompt: queue.pop(0)


def test_confirming_needs_a_person_at_a_terminal(root, capsys):
    """Guards the AI (or a script, or CI) confirming its own key: under pytest there is no terminal, which is what an
    agent's shell looks like too."""
    assert cli.confirm(KEY_ID, ask=_typed(LAWYER, KEY_ID, "x")) == 1
    assert "needs a person at a terminal" in capsys.readouterr().out
    assert not _key().confirmed


def test_a_lawyers_name_the_key_id_and_the_sheets_fingerprint_typed_by_hand_confirm_the_key(root):
    assert cli.confirm(KEY_ID, ask=_typed(LAWYER, KEY_ID, _key().fingerprint), interactive=True,
                       today="2026-10-28") == 0

    key = _key()
    assert key.confirmed and key.status["confirmed_by"] == LAWYER and key.status["confirmed_at"] == "2026-10-28"


@pytest.mark.parametrize("answers", [("", KEY_ID, "FP"), (LAWYER, "msa", "FP"), (LAWYER, "", "FP")])
def test_without_a_name_or_the_right_id_nothing_changes(root, answers):
    """Each case gives the RIGHT fingerprint ("FP"), so the refusal can only come from the name or the id."""
    before = (root / "gold" / f"{KEY_ID}.json").read_text(encoding="utf-8")
    typed = [_key().fingerprint if a == "FP" else a for a in answers]
    assert cli.confirm(KEY_ID, ask=_typed(*typed), interactive=True) == 1
    assert (root / "gold" / f"{KEY_ID}.json").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("typed", ["", "0123456789abcdef", "08d8a971"])
def test_a_confirmation_is_bound_to_the_sheet_the_lawyer_signed(root, capsys, typed):
    """Guards confirming a key that is not the one on the signed sheet: the sheet's footer carries the fingerprint of the
    key it was made from, and typing it is what ties this confirmation to what the lawyer actually read (a key edited since
    has a different one; a partial one does not count)."""
    before = (root / "gold" / f"{KEY_ID}.json").read_text(encoding="utf-8")

    assert cli.confirm(KEY_ID, ask=_typed(LAWYER, KEY_ID, typed), interactive=True) == 1

    assert "made from a different version of this key" in capsys.readouterr().out
    assert (root / "gold" / f"{KEY_ID}.json").read_text(encoding="utf-8") == before


def test_the_fingerprint_may_be_typed_in_capitals(root):
    assert cli.confirm(KEY_ID, ask=_typed(LAWYER, KEY_ID, _key().fingerprint.upper()), interactive=True) == 0


def test_a_key_with_a_hole_cannot_be_confirmed(root, capsys):
    """Guards a lawyer signing a key that still has a duty sentence nobody labelled."""
    text_path = root / "texts" / f"{KEY_ID}.txt"
    extra = "\nProvider shall deliver a quarterly report to the Customer."
    text = text_path.read_text(encoding="utf-8") + extra
    text_path.write_text(text, encoding="utf-8")
    _edit_key(lambda raw: raw.update(text_sha256=hashlib.sha256(text.encode()).hexdigest()))

    assert cli.confirm(KEY_ID, ask=_typed(LAWYER, KEY_ID, "x"), interactive=True) == 1
    assert "cannot be confirmed yet" in capsys.readouterr().out and not _key().confirmed


def test_an_unknown_key_and_an_already_confirmed_one_are_said_so(confirmed, capsys):
    assert cli.confirm("nope", ask=_typed(), interactive=True) == 1
    assert cli.confirm(KEY_ID, ask=_typed(), interactive=True) == 0
    assert "already confirmed by Asha Rao" in capsys.readouterr().out


def test_check_says_when_a_confirmed_key_was_edited(confirmed, capsys):
    _edit_key(lambda raw: raw["atoms"][0].update(note="edited"))
    cli.check()
    out = capsys.readouterr().out
    assert "status draft" in out and "NOT confirmed any more: it was edited after it was confirmed" in out


# --- the lawyer's sheet ----------------------------------------------------------------------------------------------


def _visible_text(page: str) -> str:
    """The marked-up text from the sheet's <pre>, back to plain text."""
    body = re.search(r'<pre class="text">(.*?)</pre>', page, re.DOTALL).group(1)
    return unescape(re.sub(r"<sup>.*?</sup>|</?mark[^>]*>", "", body))


def test_the_sheet_carries_the_whole_text_every_row_and_the_sign_off(root):
    """Guards the long pole: a lawyer must be able to read the full text with what we labelled marked, tick every row,
    add what we missed and sign, on paper or in a browser, with nothing to install."""
    key = _key()
    page = sheet.render_sheet(key)

    assert _visible_text(page) == key.text  # every character of the contract, once, nothing dropped by the marking
    for atom in key.atoms:
        assert f"<td class='id'>{atom.id}</td>" in page
    for not_duty in key.not_duties:
        assert f"<td class='id'>{not_duty.id}</td>" in page
    assert key.fingerprint in page and "DRAFT" in page and "Sign-off" in page and "Duties the key missed" in page
    assert "<script" not in page and "http" not in page.replace("https://", "http://").split("<body>")[1]
    assert page.count("<mark") == page.count("</mark>")


def test_the_sheet_is_the_same_every_time_and_says_when_a_key_is_confirmed(confirmed):
    key = _key()
    assert sheet.render_sheet(key) == sheet.render_sheet(key)
    page = sheet.render_sheet(key)
    assert "Lawyer-confirmed by Asha Rao on 2026-10-28" in page and "DRAFT" not in page


def test_the_sheet_escapes_everything_from_the_key(root):
    """Guards markup in a quote, a note or a party name reaching the page as markup."""
    _edit_key(lambda raw: (raw["atoms"][0].update(note="<img src=x onerror=alert(1)>"),
                           raw["parties"]["us"].update(names=["Aegis Audit Test Entity Pvt Ltd"])))
    page = sheet.render_sheet(_key())
    assert "<img" not in page and "&lt;img src=x onerror=alert(1)&gt;" in page


def test_the_text_itself_is_escaped_and_overlapping_labels_still_nest_properly(root):
    """Guards markup inside the contract text reaching the page as markup, and two rows covering the same words (an atom
    inside a not-duty, two atoms on one clause) breaking the page's marking: the text is cut at every boundary."""
    text = "1. Provider <b>shall</b> pay Fees & Taxes within 5 days. Customer may terminate for convenience.\n"
    (root / "texts" / "mini.txt").write_text(text, encoding="utf-8")
    atom = {"id": "A01", "clause": "1", "quote": "Provider <b>shall</b> pay Fees & Taxes within 5 days", "owed_by": "them",
            "kind": "duty", "type": "payment", "timing": {"kind": "ongoing"}}
    inner = {**atom, "id": "A02", "quote": "pay Fees & Taxes"}
    right = {"id": "N01", "clause": "1", "quote": "Customer may terminate for convenience.", "reason": "right"}
    (root / "gold" / "mini.json").write_text(json.dumps({
        "schema": 1, "id": "mini", "title": "Mini", "text_file": "texts/mini.txt",
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "status": {"state": "draft"},
        "parties": {"us": {"names": ["Us"], "roles": []}, "them": {"names": ["Them"], "roles": []}},
        "anchors": {}, "atoms": [atom, inner], "not_duties": [right]}), encoding="utf-8")

    page = sheet.render_sheet(gold.load(root / "gold" / "mini.json"))

    assert "<b>shall</b>" not in page and "&lt;b&gt;shall&lt;/b&gt;" in page
    assert _visible_text(page) == text
    assert page.count("<mark") == page.count("</mark>") >= 3
    assert "title=\"A01, A02\"" in page  # the words both rows cover say so


@pytest.mark.parametrize("timing, words", [
    ({"kind": "ongoing"}, "Ongoing: no single due date"),
    ({"kind": "condition"}, "A condition, or no fixed time"),
    ({"kind": "condition", "period_number": 30, "period_unit": "days", "direction": "after",
      "anchor": "notice_received", "stated_in_text": True}, "A condition: 30 days after receipt of the notice"),
    ({"kind": "ongoing", "period_number": 3, "period_unit": "years", "direction": "after", "anchor": "termination",
      "stated_in_text": True}, "Ongoing: 3 years after termination"),
    ({"kind": "condition", "period_number": 30, "period_unit": "days", "direction": "before", "anchor": "termination"},
     "A condition: 30 days before termination. Not stated in this contract: left for a person"),
    ({"kind": "deadline", "period_number": 60, "period_unit": "days", "direction": "after", "anchor": "invoice_received",
      "anchor_text": "receipt", "stated_in_text": True}, "60 days after receipt of the invoice (“receipt”)"),
    ({"kind": "deadline", "period_number": 30, "period_unit": "business_days", "direction": "before",
      "anchor": "expiration_date", "clear_days": True, "stated_in_text": True},
     "30 business days (clear days) before the expiry date"),
    ({"kind": "deadline", "period_number": 5, "period_unit": "days", "anchor": "effective_date"},
     "5 days after the effective date. Not stated in this contract: left for a person"),
    ({"kind": "deadline", "anchor": "other"}, "A deadline tied to the event the clause names; the period is not stated"),
    ({"kind": "deadline", "explicit_date": "2027-01-31"}, "On 2027-01-31"),
])
def test_timing_is_put_in_the_words_a_lawyer_would_use(timing, words):
    assert sheet.timing_words(timing) == words


def test_the_sheet_says_what_the_app_would_do_in_the_apps_own_terms(root):
    """Guards "the app would leave the date for a person" on rows where it expects no date at all (an ongoing duty, a
    condition): that is no gap, and a lawyer who read it as one would see a problem on nearly every row."""
    page = sheet.render_sheet(_key())

    assert "The app would show no fixed date: none is expected" in page
    assert "The app would wait for the event to be logged, then count" in page  # 60 days after the invoice is received
    assert "A condition: 30 days after receipt of the notice" in page  # a condition's period is no longer hidden


def test_a_date_that_needs_a_person_is_said_so(root, monkeypatch):
    atom = _key().atoms[0]
    monkeypatch.setattr(gold, "atom_deadline", lambda key, a: {"facts": {"kind": "deadline"}, "due": None, "ends_on": None,
                                                              "reason": "period not stated in the contract - needs a person"})
    assert sheet._computed(_key(), atom) == "The app would leave the date for a person"


def test_the_sheet_keeps_its_colours_and_marks_on_paper(root):
    """Guards the printed sheet losing its marking: browsers leave background colours off when printing, so every passage
    also carries a border, the colours ask to be printed, and the table headers repeat on each page."""
    page = sheet.render_sheet(_key())

    assert "print-color-adjust:exact" in page and "border-bottom:2px solid" in page
    assert page.count("<thead>") == 2 and "thead{display:table-header-group}" in page


def test_the_review_sheet_command_writes_one_file_per_key_and_says_where(root, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sheet, "REVIEW_DIR", tmp_path / "sheets")

    assert cli.review_sheets(KEY_ID) == 0

    assert (tmp_path / "sheets" / f"{KEY_ID}.html").is_file()
    out = capsys.readouterr().out
    assert f"{KEY_ID}.html" in out and "git-ignored" in out
    assert cli.review_sheets("nope") == 1


def test_the_review_folder_is_git_ignored():
    """Guards the contract text (of an origin nobody traced) reaching the repository through the sheet."""
    path = Path(__file__).resolve().parents[2] / ".gitignore"
    if not path.is_file():
        pytest.skip("the repository root is not visible from here (the container mounts backend/ only)")
    assert "backend/eval/obligations/review/" in path.read_text(encoding="utf-8")


# --- the named baseline ----------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scratch_engine():
    with scratch_database() as engine:
        yield engine


@pytest.fixture
def live(scratch_engine):
    return make_live(scratch_engine)


@pytest.fixture(autouse=True)
def _real_model_off_and_prompts_from_the_test_database(monkeypatch):
    monkeypatch.setattr(settings, "mock_claude", False)
    monkeypatch.setattr(claude, "exam_meter", None)
    real = rehearsal.get_active_prompt_bundle

    def bundle(_db, **kwargs):
        with database.SessionLocal() as db:
            return real(db, **kwargs)

    monkeypatch.setattr(rehearsal, "get_active_prompt_bundle", bundle)


def _answer(key: gold.Gold) -> dict:
    return {"obligations": [
        {"obligation_type": r["type"], "description": r["description"], "responsible_party": r["responsible_party"],
         "timing": r["timing"], "citations": [{"quote": r["quote"]}]}
        for r in adapters.rows_from_gold(key)]}


class Provider(FakeProvider):
    """A model that gives the key's own answer, and fails on the calls whose number is in `fail_on`."""

    def __init__(self, key, *, fail_on=()):
        tool = feature_registry.get(rehearsal.SKILL).tool_name
        super().__init__(_response(tool=tool, tool_input=_answer(key), stop="tool_use"))
        self.fail_on = set(fail_on)

    async def complete_structured(self, **kwargs):
        if len(self.calls) + 1 in self.fail_on:
            self.calls.append(("structured", kwargs))
            raise anthropic.APIStatusError(
                "overloaded", response=httpx.Response(529, request=httpx.Request("POST", "https://x")), body=None)
        return await super().complete_structured(**kwargs)


def _baseline(live, tmp_path, provider, *, name="first", runs=2, ask=None):
    return asyncio.run(rehearsal.run_obligations(
        live=live, owner=OWNER, ask=ask or Typist("5"), runs=runs, label=None, provider=provider,
        results_dir=tmp_path / "scratch", baseline=name, baselines_dir=tmp_path / "results"))


def test_a_baseline_is_refused_while_any_key_is_a_draft_and_nothing_is_sent(live, root, tmp_path):
    """Guards naming a baseline from a key no lawyer confirmed: the refusal comes before the estimate, names the key and
    says why, and nothing is sent."""
    provider = Provider(_key())

    with pytest.raises(ExamRefused, match=f"{KEY_ID}: no lawyer has confirmed it yet"):
        _baseline(live, tmp_path, provider)

    assert provider.calls == [] and not (tmp_path / "results").exists()


def test_a_key_edited_after_its_confirmation_is_refused_too(live, confirmed, tmp_path):
    _edit_key(lambda raw: raw["atoms"][0].update(note="edited"))
    with pytest.raises(ExamRefused, match="edited after it was confirmed"):
        _baseline(live, tmp_path, Provider(_key()))


@pytest.mark.parametrize("runs", [1, 3])
def test_a_baseline_is_exactly_two_passes(live, confirmed, tmp_path, runs):
    with pytest.raises(ExamRefused, match="exactly 2 passes"):
        _baseline(live, tmp_path, Provider(_key()), runs=runs)


@pytest.mark.parametrize("name", ["", "ab", "Has Capitals", "x" * 41, "-lead", "trail-", "a b"])
def test_a_baseline_name_is_a_short_slug(live, confirmed, tmp_path, name):
    with pytest.raises(ExamRefused, match="3 to 40 lowercase"):
        _baseline(live, tmp_path, Provider(_key()), name=name)


def test_a_baseline_is_never_overwritten(live, confirmed, tmp_path):
    (tmp_path / "results" / "baseline-first").mkdir(parents=True)
    provider = Provider(_key())

    with pytest.raises(ExamRefused, match="never overwritten"):
        _baseline(live, tmp_path, provider)

    assert provider.calls == []


def test_a_ceiling_above_five_dollars_is_refused_and_nothing_is_spent(live, confirmed, tmp_path):
    """Guards the plan's limit (about $2, ceiling $5): the typed amount is capped, and over it counts as no go-ahead."""
    provider = Provider(_key())

    with pytest.raises(ExamRefused, match="go-ahead was not given"):
        _baseline(live, tmp_path, provider, ask=Typist("6"))

    assert provider.calls == []


@pytest.mark.parametrize("typed", ["$1,000", "10,000", "1,5", "5.01", "1e1"])
def test_no_way_of_writing_an_amount_over_five_dollars_gets_past_the_cap(live, confirmed, tmp_path, typed):
    """Guards the cap reading the typed amount differently from the gate behind it: the exam's own parser drops thousands
    commas, so "$1,000" is 1000 to it, and a cap that did not understand it passed it on as "not a number" and approved a
    $1000 ceiling on a named baseline."""
    provider = Provider(_key())

    with pytest.raises(ExamRefused, match="go-ahead was not given"):
        _baseline(live, tmp_path, provider, ask=Typist(typed))

    assert provider.calls == []


@pytest.mark.parametrize("typed", ["abc", "-3", "0", "nan"])
def test_an_amount_that_is_not_a_positive_number_is_still_refused_by_the_gate(live, confirmed, tmp_path, typed):
    with pytest.raises(ExamRefused, match="go-ahead was not given"):
        _baseline(live, tmp_path, Provider(_key()), ask=Typist(typed))


def test_two_good_passes_over_confirmed_keys_name_the_baseline_and_check_re_verifies_it(live, confirmed, tmp_path):
    key = _key()
    provider = Provider(key)

    result = _baseline(live, tmp_path, provider)

    assert result["named"] is True and len(provider.calls) == 2
    folder = tmp_path / "results" / "baseline-first"
    assert sorted(p.name for p in folder.iterdir()) == ["manifest.json", "results.json", "run-a.json", "run-b.json",
                                                         "scorecard.txt"]
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["results_sha256"] == hashlib.sha256((folder / "results.json").read_bytes()).hexdigest()
    assert manifest["keys"] == [{"id": KEY_ID, "fingerprint": key.fingerprint, "confirmed_by": LAWYER,
                                 "confirmed_at": "2026-10-28"}]
    card = (folder / "scorecard.txt").read_text(encoding="utf-8")
    assert card.splitlines()[0] == "BASELINE first" and f"KEY STATUS: lawyer-confirmed ({KEY_ID}: Asha Rao, 2026-10-28)" in card
    assert "PROVISIONAL" not in card and "Limits:" in card
    assert [c["run"] for c in json.loads((folder / "run-a.json").read_text())] == [0]
    assert [c["run"] for c in json.loads((folder / "run-b.json").read_text())] == [1]
    assert cli.check_baselines(tmp_path / "results") is False
    assert not (tmp_path / "scratch").exists()  # nothing was saved as a rehearsal as well


def test_a_pass_that_got_no_answer_is_not_named_and_is_kept_as_a_rehearsal(live, confirmed, tmp_path):
    """Guards a baseline with a hole in it: one key with no answer in one pass would be a smaller, flattering set."""
    result = _baseline(live, tmp_path, Provider(_key(), fail_on={2}))

    assert result["named"] is False and not (tmp_path / "results").exists()
    card = (tmp_path / "scratch" / result["run_id"] / "scorecard.txt").read_text(encoding="utf-8")
    assert "NOT NAMED: 1 call(s) got no answer" in card.splitlines()[0]
    assert "PROVISIONAL REHEARSAL" in card.splitlines()[0]


def test_a_run_stopped_by_the_ceiling_is_not_named(live, confirmed, tmp_path):
    stopper = Provider(_key())
    stopper.error = exam_runner.CeilingReached("spending ceiling reached")

    result = _baseline(live, tmp_path, stopper)

    assert result["named"] is False and result["outcome"] == "incomplete"
    assert not (tmp_path / "results").exists()


def test_a_rehearsal_is_unchanged_by_all_of_this(live, root, tmp_path):
    """Guards the plain rehearsal: draft keys, one pass, a scratch folder and the provisional stamp, as before."""
    provider = Provider(_key())

    result = asyncio.run(rehearsal.run_obligations(
        live=live, owner=OWNER, ask=Typist("5"), runs=1, label="x", provider=provider, results_dir=tmp_path))

    assert result["named"] is False
    card = (tmp_path / result["run_id"] / "scorecard.txt").read_text(encoding="utf-8")
    assert card.startswith(rehearsal.BANNER) and "KEY STATUS: draft" in card
    assert not card.startswith("BASELINE ")


# --- what --check re-verifies ----------------------------------------------------------------------------------------


@pytest.fixture
def named(live, confirmed, tmp_path):
    _baseline(live, tmp_path, Provider(_key()))
    return tmp_path / "results"


def _rewrite(path, mutate):
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def test_check_passes_for_an_untouched_baseline(named, capsys):
    assert cli.check_baselines(named) is False
    assert "ok   baseline-first" in capsys.readouterr().out


def test_check_catches_results_changed_after_the_baseline_was_named(named, capsys):
    _rewrite(named / "baseline-first" / "results.json", lambda d: d.update(label="tampered"))
    assert cli.check_baselines(named) is True
    assert "results.json was changed after the baseline was named" in capsys.readouterr().out


def test_check_catches_run_files_that_disagree_with_the_results(named, capsys):
    _rewrite(named / "baseline-first" / "run-a.json", lambda d: d.clear())
    assert cli.check_baselines(named) is True
    assert "do not hold the same calls" in capsys.readouterr().out


def test_check_catches_a_key_edited_after_the_baseline_was_named(named, capsys):
    """Guards comparing a later result against a key that is no longer the one the baseline was scored with."""
    _edit_key(lambda raw: raw["atoms"][0].update(note="edited"))
    assert cli.check_baselines(named) is True
    assert "was edited since this baseline was named" in capsys.readouterr().out


def test_check_catches_a_key_that_is_no_longer_confirmed(named, capsys):
    _edit_key(lambda raw: raw["status"].update(confirmed_by=""))
    assert cli.check_baselines(named) is True
    assert "no longer lawyer-confirmed" in capsys.readouterr().out


def test_check_catches_a_scorer_that_now_says_something_else(named, monkeypatch, capsys):
    """Guards a scorer fix quietly flattering (or hurting) a later comparison: every answered call is re-scored from
    its stored rows with today's scorer and must give the stored numbers."""
    from eval.obligations import score

    real = score.score
    monkeypatch.setattr(score, "score", lambda key, rows: {**real(key, rows), "recall": 0.123})
    assert cli.check_baselines(named) is True
    assert "no longer re-scores to the stored numbers" in capsys.readouterr().out


def test_check_catches_a_key_that_was_deleted(named, root, capsys):
    (root / "gold" / f"{KEY_ID}.json").unlink()
    assert cli.check_baselines(named) is True
    assert "key msa-small no longer exists" in capsys.readouterr().out


def test_the_committed_keys_and_baselines_pass_the_free_check():
    """Guards CI being blind to a broken committed key or baseline: `--check` is free, so the suite runs it on the real tree
    (the keys under eval/obligations/gold and any results/baseline-*), and a stale committed baseline fails the build."""
    assert cli.check() == 0


@pytest.mark.parametrize("baselines_failed, code", [(False, 0), (True, 1)])
def test_a_failing_baseline_fails_the_free_command(monkeypatch, baselines_failed, code):
    monkeypatch.setattr(cli, "check_baselines", lambda folder=None: baselines_failed)
    assert cli.check() == code


def test_check_runs_the_baseline_check_as_part_of_the_free_command(named, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli, "check_baselines", lambda folder=None: seen.append(folder) or False)
    cli.check()
    assert seen == [None]


def test_the_command_takes_a_baseline_name_and_the_run_flag():
    from eval.exam.__main__ import _parser

    args = _parser().parse_args(["obligations", "--baseline", "first", "--runs", "2"])
    assert args.baseline == "first" and args.runs == 2
