"""The exam's reports: the public numbers-only report, the private detail file, and which two runs
may stand together as a pair.

specs/005-ai-eval-baseline T014 / T021 (FR-26, FR-27, FR-28, FR-36, FR-40, FR-41; AC-26, AC-27,
AC-28, AC-36). Pure and free: runs are built in memory with the builders from ``test_exam_score``
and ``test_exam_compare``, every folder the report module writes to or reads from is a temp
folder, and nothing here touches a database, the network or a model.
"""

import hashlib
import json

import pytest

from eval.exam import report as r
from eval.exam import score as s
from eval.exam.content import OWNER_FILES
from tests.test_exam_compare import review_content, review_run, stamped
from tests.test_exam_score import (
    call,
    compliant,
    extraction_content,
    extraction_result,
    flag,
    item,
    key,
    make_content,
    make_doc,
    plant,
    results,
    review_item,
    review_result,
)

OWNER_SENTENCE = (
    "the supplier shall indemnify the customer against every third party claim arising "
    "from any breach of this agreement"
)


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    """Every folder the report module touches, moved under ``tmp_path``: a test must never write
    into the committed reports folder or read the owner's real texts."""
    reports, runs, owner = tmp_path / "reports", tmp_path / "runs", tmp_path / "owner_texts"
    owner.mkdir()
    monkeypatch.setattr(r, "REPORTS_DIR", reports)
    monkeypatch.setattr(r, "RUNS_DIR", runs)
    monkeypatch.setattr(r, "OWNER_TEXTS_DIR", owner)
    monkeypatch.setattr(r, "SUPERSEDED_DIR", tmp_path / "superseded")
    (tmp_path / "superseded").mkdir()
    (tmp_path / "superseded" / "baseline-sample-2026-09-28.json").write_text("{}")
    return tmp_path


def pair(a=(20, 5), b=(21, 4), **extra):
    return s.score_pair(
        review_run(*a, "a", **extra), review_run(*b, "b", **extra), review_content(), texts={}
    )


def severity_content():
    """One review document: a critical and a high finding, a medium one, and a compliant rule."""
    rules = [
        plant("p0", "critical"),
        plant("p1", "high"),
        plant("p2", "medium"),
        compliant("c0"),
    ]
    return make_content(documents=[make_doc("d1")], review=[review_item("d1", rules)])


def severity_pair(flags_a, flags_b):
    content = severity_content()
    run_a = stamped(results([review_result("d1", flags_a)], run_id="a"), content)
    run_b = stamped(results([review_result("d1", flags_b)], run_id="b"), content)
    return s.score_pair(run_a, run_b, content, texts={})


# --- the public report (FR-26) ---------------------------------------------------------------


def test_every_measure_is_in_the_report_with_n_a_plain_line_and_its_noise_band(dirs):
    """AC-26: a score without its item count, a plain-language line or its band cannot be read
    by the owner. Every measure the pair holds must show all three, and the header must name the
    delivery mode and the earlier results as history."""
    obj = r.build_report(pair(), kind="baseline", label="baseline-test")
    path = r.write_public_report(obj, stem="baseline-test")
    text = path.read_text()
    assert path == dirs / "reports" / "baseline-test.md"
    saved = json.loads((dirs / "reports" / "baseline-test.json").read_text())
    assert saved["kind"] == "baseline"
    for m in obj["measures"]:
        assert m["label"] in text and m["explanation"] in text
        assert f"| {m['label']} | {m['n']} |" in text or m["role"] != s.HEADLINE
    f1 = next(m for m in obj["measures"] if m["key"] == "review_f1")
    assert f"| Review F1 | {f1['n']} | 0.800 | 0.840 | 0.820 | ±0.040 |" in text
    assert "Delivery mode: batch" in text
    assert obj["history"] == [
        {"file": "superseded/baseline-sample-2026-09-28.json", "label": "earlier, superseded"}
    ]
    assert "`superseded/baseline-sample-2026-09-28.json` — earlier, superseded" in text


def test_the_missed_critical_section_says_how_many_there_were_to_miss(dirs):
    """FR-26: "1 missed" means little without how many high and critical findings the keys hold.
    The section needs its n and a plain line like every measure, in the markdown and in the
    JSON; the list itself names the item, rule, severity and the run that missed it."""
    got = severity_pair([flag("p0")], [flag("p0"), flag("p1")])
    obj = r.build_report(got, kind="baseline", label="x")
    assert obj["missed_high_critical_n"] == 2  # p0 and p1; the medium one is not counted
    assert obj["missed_high_critical_explanation"] == r.MISSED_HIGH_CRITICAL_EXPLANATION
    assert [(m["rule"], m["severity"], m["runs_missed"]) for m in obj["missed_high_critical"]] == [
        ("p1", "high", ["a"])
    ]
    text = r.render_markdown(obj)
    section = text.split("## Missed high and critical findings")[1].split("\n## ")[0]
    assert r.MISSED_HIGH_CRITICAL_EXPLANATION in section and "(n = 2)" in section
    assert "| rv-d1 | p1 | high | A |" in section
    saved = json.loads(r.write_public_report(obj, stem="x").with_suffix(".json").read_text())
    assert saved["missed_high_critical_n"] == 2


def test_the_missed_critical_section_keeps_its_n_when_nothing_was_missed(dirs):
    """FR-26: "None." with no n would not say whether nothing was missed or nothing was asked."""
    got = severity_pair([flag("p0"), flag("p1")], [flag("p0"), flag("p1")])
    obj = r.build_report(got, kind="baseline", label="x")
    section = r.render_markdown(obj).split("## Missed high and critical findings")[1]
    section = section.split("\n## ")[0]
    assert "(n = 2)" in section and "None." in section


def test_flags_and_the_sample_label_mark_runs_that_cannot_be_trusted():
    """FR-30, FR-40: a sample run is labelled not comparable; a run with failed calls says
    "contains errors" and an unfinished one "incomplete", even under the 1% a baseline allows."""
    assert r.run_flags({"mode": "full", "outcome": "complete", "failed_call_rate": 0}) == []
    sample = r.run_flags({"mode": "sample", "outcome": "complete", "failed_call_rate": 0})
    assert sample == ["sample"]
    got = r.run_flags({"mode": "full", "outcome": "incomplete", "failed_call_rate": 0.005})
    assert got == ["contains errors", "incomplete"]
    obj = r.build_report(pair(), kind="baseline", label="x")
    obj["runs"][0].update(
        mode="sample", outcome="incomplete", failed_call_rate=0.004, contains_errors=True
    )
    text = r.render_markdown(obj)
    assert r.SAMPLE_LABEL in text
    assert "Run A: **contains errors and incomplete** (0.4% of AI calls failed)." in text


def test_a_comparison_report_shows_the_verdict_names_failures_and_puts_cost_beside(dirs):
    """AC-28: the verdict, every failed measure by name, and cost and time beside the scores. A
    change that lost review F1 must read FAIL and say which measure failed, and a change with no
    target on its runs must read NO VERDICT with the reason."""
    base = pair((20, 5), (21, 4))
    base["label"] = "baseline-x"
    better = pair((22, 3), (22, 4), target_area="review")
    better["label"] = "change-x"
    obj = r.build_report(better, kind="comparison", label="c", comparison=s.verdict(base, better))
    text = r.write_public_report(obj, stem="compare-pass").read_text()
    assert "## Verdict: PASS" in text and "(main measure)" in text
    assert "Baseline: baseline-x · change: change-x · target area: review" in text
    assert "Cost and time, beside the scores" in text

    worse = pair((15, 9), (16, 8), target_area="review")
    failed = s.verdict(base, worse)
    text = r.render_markdown(
        r.build_report(worse, kind="comparison", label="c", comparison=failed)
    )
    assert "## Verdict: FAIL" in text and "Measures that failed: **Review F1**" in text

    unnamed = pair((22, 3), (22, 4))
    text = r.render_markdown(
        r.build_report(
            unnamed, kind="comparison", label="c", comparison=s.verdict(base, unnamed)
        )
    )
    assert "NO VERDICT" in text and "target not recorded before runs" in text


def test_per_field_extraction_accuracy_is_split_before_and_past_25k_with_n():
    """FR-26: per-field accuracy before and past 25,000 characters, each with its item count, in
    the object and in the markdown. A past-25k figure with no n would read as more than it is."""
    early, late = "This Agreement starts on 1 April 2024.", "Governed by the laws of Delaware."
    text = early + " " + "z" * (26_000 - len(early) - 1) + late
    content = extraction_content(
        key("effective_date", "date", "2024-04-01", early),
        key("governing_law", "jurisdiction", "delaware", late),
    )

    def run(date, run_id):
        fields = {"effective_date": date, "governing_law": "State of Delaware"}
        return stamped(results([extraction_result("d1", fields)], run_id=run_id), content)

    got = s.score_pair(run("2024-04-01", "a"), run("2025-01-01", "b"), content, texts={"d1": text})
    obj = r.build_report(got, kind="baseline", label="x")
    date = obj["fields"]["effective_date"]
    assert date["all"] == {"n": 1, "run_a": 1.0, "run_b": 0.0, "average": 0.5, "noise_band": 1.0}
    assert date["before_25k"]["n"] == 1 and date["past_25k"]["n"] == 0
    assert obj["fields"]["governing_law"]["past_25k"]["average"] == 1.0
    markdown = r.render_markdown(obj)
    assert "## Extraction accuracy, per field" in markdown
    assert "| effective_date | 1 | 1.000 | 0.000 | 0.500 | ±1.000 | 0.500 | 1 | — | 0 |" in markdown
    assert "(lower is better). (lower" not in markdown
    json.dumps(obj)


# --- what a public report may never hold (FR-36) -----------------------------------------------


def test_owner_text_an_owner_file_name_or_a_private_key_stops_the_write(dirs):
    """FR-36: eight words of an owner contract, an owner file name, or a field that holds quotes
    must refuse the whole report and write nothing: the owner's contracts must never reach
    version control. Seven shared words are still fine."""
    (dirs / "owner_texts" / ("a" * 64 + ".txt")).write_text(
        "Preamble words here. " + OWNER_SENTENCE.capitalize() + ". More text."
    )
    obj = r.build_report(pair(), kind="baseline", label="x")
    assert r.write_public_report(obj, stem="fine").exists()

    leaked = {**obj, "label": "a " + OWNER_SENTENCE.upper()}
    with pytest.raises(r.ReportRefused, match="owner contract"):
        r.write_public_report(leaked, stem="leaky")
    seven = " ".join(OWNER_SENTENCE.split()[:7])
    r.write_public_report({**obj, "label": seven}, stem="seven-words-ok")
    named = {**obj, "label": f"from {OWNER_FILES[2]}"}
    with pytest.raises(r.ReportRefused, match="owner file name"):
        r.write_public_report(named, stem="named")
    with pytest.raises(r.ReportRefused, match="private fields"):
        r.write_public_report({**obj, "counts": {"quotes": ["x"]}}, stem="quoting")
    with pytest.raises(r.ReportRefused, match="usable report name"):
        r.write_public_report(obj, stem="../escape")
    assert sorted(p.name for p in (dirs / "reports").iterdir()) == [
        "fine.json",
        "fine.md",
        "seven-words-ok.json",
        "seven-words-ok.md",
    ]


def test_quotes_from_the_runs_never_reach_the_public_report(dirs):
    """FR-36: a quote the AI returned is contract text. It sits under the pair's ``private`` key
    for the detail file; the report built from the pair must carry numbers and names only."""
    run_a = review_run(20, 5, "a")
    quote = {"text": "SECRET INVENTED QUOTE", "word_for_word": False, "align_score": 40.0}
    run_a["calls"] = [call(quotes=[quote])]
    got = s.score_pair(run_a, review_run(21, 4, "b"), review_content(), texts={})
    obj = r.build_report(got, kind="baseline", label="x")
    path = r.write_public_report(obj, stem="clean")
    assert "SECRET" not in path.read_text() + path.with_suffix(".json").read_text()


def test_a_published_report_is_never_overwritten(dirs):
    """FR-27: a baseline is a record, and "earlier baselines SHALL be kept". Writing the same
    name again is refused and leaves both published files exactly as they were; the report has no
    way to ask for a replacement, and a half-published name (only the .json) is also protected."""
    obj = r.build_report(pair(), kind="baseline", label="first")
    path = r.write_public_report(obj, stem="baseline")
    before = (path.read_bytes(), path.with_suffix(".json").read_bytes())

    again = r.build_report(pair((10, 10), (11, 9)), kind="baseline", label="second")
    with pytest.raises(r.ReportRefused, match="already exists"):
        r.write_public_report(again, stem="baseline")
    with pytest.raises(TypeError):
        r.write_public_report(again, stem="baseline", replace=True)
    assert (path.read_bytes(), path.with_suffix(".json").read_bytes()) == before

    (dirs / "reports" / "half.json").write_text("{}")
    with pytest.raises(r.ReportRefused, match="already exists"):
        r.write_public_report(again, stem="half")
    assert not (dirs / "reports" / "half.md").exists()


# --- check_pair: may these two runs be a baseline? (FR-27, D-13, FR-41) -----------------------


def finished(dirs, run_id, **over):
    """The ``eval.run_finished`` record of a clean full run, with its results file on disk."""
    folder = dirs / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"run_id": run_id}).encode()
    (folder / "results.json").write_bytes(body)
    record = {
        "run_id": run_id,
        "kind": "exam",
        "mode": "full",
        "delivery": "batch",
        "outcome": "complete",
        "incomplete_reason": None,
        "failed_call_rate": 0.0,
        "results_sha256": hashlib.sha256(body).hexdigest(),
        "versions": {
            "app_code_sha256": "code",
            "models": ["m1", "m2"],
            "prompts": [
                {"prompt_key": "a", "version": "1", "hash": "h1"},
                {"prompt_key": "b", "version": "1", "hash": "h2"},
            ],
            "content_fingerprint": "fp",
        },
    }
    record.update(over)
    return record


def test_a_clean_pair_is_accepted_and_one_percent_failed_calls_is_the_line(dirs):
    """D-13: at 1.0% failed AI calls a run may be accepted (with its "contains errors" flag);
    at 1.1% it may not. Model order is not a difference."""
    a, b = finished(dirs, "run-aaaaaaaa"), finished(dirs, "run-bbbbbbbb")
    assert r.check_pair(a, b) == []
    at_line = finished(dirs, "run-cccccccc", failed_call_rate=0.01)
    assert r.check_pair(a, at_line) == []
    assert r.run_flags(at_line) == ["contains errors"]
    over = finished(dirs, "run-dddddddd", failed_call_rate=0.011)
    problems = r.check_pair(a, over)
    assert len(problems) == 1 and "1.1%" in problems[0] and "Run B" in problems[0]
    reordered = finished(dirs, "run-eeeeeeee")
    reordered["versions"] = {**reordered["versions"], "models": ["m2", "m1"]}
    assert r.check_pair(a, reordered) == []


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"mode": "sample"}, "sample run"),
        ({"outcome": "incomplete", "incomplete_reason": "ceiling_reached"}, "ceiling_reached"),
        ({"kind": "ocr"}, "not an exam run"),
        ({"delivery": "sync"}, "delivery mode"),
        ({"failed_call_rate": None}, "did not record how many"),
    ],
)
def test_runs_that_are_not_full_complete_exam_runs_on_one_delivery_mode_are_refused(
    dirs, over, needle
):
    """AC-27, AC-41: a sample, an unfinished run, a different kind of run, a batch run paired
    with a normal-delivery one, or a run that never recorded its failed calls cannot be half of
    a baseline; each says why in words."""
    a, b = finished(dirs, "run-aaaaaaaa"), finished(dirs, "run-bbbbbbbb", **over)
    assert any(needle in p for p in r.check_pair(a, b)), r.check_pair(a, b)


def test_pairs_that_differ_in_versions_or_are_one_run_or_have_a_changed_results_file_are_refused(
    dirs,
):
    """AC-27: a different code version, model set, prompt version or exam content means the two
    runs measure different things; the same run twice has no noise band; a results file edited
    after the run (or gone) no longer backs the numbers; a run with no record is refused."""
    a = finished(dirs, "run-aaaaaaaa")
    for field, value, needle in [
        ("app_code_sha256", "other", "app code version"),
        ("models", ["m1"], "models"),
        ("prompts", [{"prompt_key": "a", "version": "2", "hash": "h1"}], "prompt versions"),
        ("content_fingerprint", "other", "exam content"),
    ]:
        b = finished(dirs, "run-bbbbbbbb")
        b["versions"] = {**b["versions"], field: value}
        assert any(needle in p for p in r.check_pair(a, b)), field
    assert any("same run" in p for p in r.check_pair(a, dict(a)))

    b = finished(dirs, "run-bbbbbbbb")
    (dirs / "runs" / "run-bbbbbbbb" / "results.json").write_text('{"edited": true}')
    assert any("changed since the run finished" in p for p in r.check_pair(a, b))
    (dirs / "runs" / "run-bbbbbbbb" / "results.json").unlink()
    assert any("results file is missing" in p for p in r.check_pair(a, b))
    assert any("no finished-run record" in p for p in r.check_pair(a, None))
    assert any("no usable run id" in p for p in r.check_pair(a, {**b, "run_id": "../x"}))


# --- the private detail file (FR-36, FR-40) ----------------------------------------------------


def detail_inputs():
    content = review_content()
    run = review_run(20, 5, "a")
    quote = {"text": "SECRET INVENTED QUOTE", "word_for_word": False, "align_score": 40.0}
    run["calls"] = [
        call(call_id="c1", quotes=[quote]),
        call(call_id="c2", status="failed", stop_reason="error", item_ids=["rv-d1"]),
    ]
    failure = {"reason": "upstream: boom"}
    run["items"].append(item("rv-d9", "review", "d1", None, status="failed", failure=failure))
    return run, s.score_run(run, content, texts={}), content


def test_the_detail_lists_failures_invalid_quotes_and_stays_in_the_private_runs_folder(dirs):
    """FR-40, FR-36: the owner's detail file names every failed call and item and quotes the
    invalid text, so it is written only under the private runs folder."""
    run, scores, content = detail_inputs()
    out = dirs / "runs" / "a" / "detail.md"
    r.write_detail(run, scores, out, content=content)
    text = out.read_text()
    assert "SECRET INVENTED QUOTE" in text and "c2" in text and "upstream: boom" in text
    assert "## Every measure" in text and "Review F1" in text


@pytest.mark.parametrize(
    "where",
    [
        pytest.param(lambda d: r.EXAM_DIR / "content" / "detail.md", id="committed-exam-tree"),
        pytest.param(lambda d: r.REPORTS_DIR / "detail.md", id="committed-reports-folder"),
        pytest.param(lambda d: d / "elsewhere" / "detail.md", id="anywhere-else"),
        pytest.param(lambda d: d / "runs" / ".." / "detail.md", id="dot-dot-out-of-runs"),
        pytest.param(lambda d: d / "runs", id="the-runs-folder-itself"),
    ],
)
def test_a_detail_file_is_refused_anywhere_but_under_the_private_runs_folder(dirs, where):
    """FR-36: detail.md quotes contract text. Refusing only the committed tree would still let a
    typo'd path leave it in an unignored folder; the one allowed place is ``eval/private/runs``,
    and a path that merely starts there but climbs out of it (``..``) is not under it."""
    run, scores, _ = detail_inputs()
    target = where(dirs)
    try:
        with pytest.raises(r.ReportRefused, match="eval/private/runs"):
            r.write_detail(run, scores, target)
    finally:
        if target.is_file():  # a broken guard must not leave contract text in the repository
            target.unlink()
