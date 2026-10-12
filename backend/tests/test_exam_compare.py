"""Comparing runs: the noise band between two baseline runs and the verdict on a change.

specs/005-ai-eval-baseline T011 (FR-13, FR-27, FR-28, FR-41; D-10, D-12). Pure and free: runs are
built in memory with the builders from ``test_exam_score``, and most verdict cases use hand-built
pair objects so each rule is tested on its own. ``check_pair`` (which runs are fit to be a pair:
same code, both full and complete, the 1% failed-call rule) belongs to ``eval/exam/report.py``
and is tested with it.
"""

import json

import pytest

from eval.exam import score as s
from eval.exam.content import QAItem, SourceCategory, content_fingerprint
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

# --- hand-built pairs for the verdict rules ---------------------------------------------------


def fake_pair(
    over=None, *, delivery="batch", fingerprint="fp", mode="full", label=None, target=None
) -> dict:
    """A pair object shaped like ``score_pair``'s. ``over`` maps a measure key to its
    (average, noise band); everything else sits at 0.90 (cost and time at 10) with a 0.01 band.
    ``target`` is the target area both runs recorded (a baseline pair has none)."""
    over = over or {}
    measures = []
    for name, spec in s.SPECS.items():
        average, band = over.get(name, (10.0 if spec["role"] == s.COST_TIME else 0.9, 0.01))
        measures.append(
            {
                "key": name,
                **{k: spec[k] for k in ("label", "explanation", "direction", "role")},
                "n": 10,
                "n_a": 10,
                "n_b": 10,
                "values": {
                    "run_a": average,
                    "run_b": average,
                    "average": average,
                    "noise_band": None if average is None else band,
                },
                "splits": {},
            }
        )
    return {
        "content_fingerprint": fingerprint,
        "delivery": delivery,
        "label": label,
        "runs": [{"mode": mode, "target_area": target}, {"mode": mode, "target_area": target}],
        "measures": measures,
    }


def judge(base=None, change=None, *, target="review", **pair_args):
    """A verdict on a change whose runs recorded ``target`` when they were approved."""
    return s.verdict(fake_pair(base, **pair_args), fake_pair(change, target=target, **pair_args))


def result_of(verdict, key):
    return next(m for m in verdict["per_measure"] if m["key"] == key)["result"]


def test_a_change_passes_when_the_main_measure_beats_its_band_and_nothing_else_worsens():
    """AC-28: baseline F1 runs 0.80 and 0.84 (average 0.82, band 0.04); the change averages 0.87
    and every other measure moved less than its own band. Pass."""
    got = judge(
        {"review_f1": (0.82, 0.04)}, {"review_f1": (0.87, 0.04), "quote_validity": (0.895, 0.01)}
    )
    assert got["verdict"] == "pass"
    assert got["failed_measures"] == []
    assert got["main_measure"] == "review_f1"
    main = next(m for m in got["per_measure"] if m["key"] == "review_f1")
    assert (main["result"], main["passed"], main["decides"]) == ("improved", True, True)
    assert main["delta"] == pytest.approx(0.05)


def test_a_change_that_moves_the_main_measure_only_within_the_noise_fails_and_names_it():
    """FR-28: an improvement inside the noise band is luck, not a result. The measure that did
    not clear its band must be named as the failure."""
    got = judge({"review_f1": (0.82, 0.04)}, {"review_f1": (0.85, 0.04)})
    assert got["verdict"] == "fail"
    assert got["failed_measures"] == ["review_f1"]
    assert result_of(got, "review_f1") == "within_band"


def test_exactly_one_noise_band_of_improvement_does_not_beat_the_band():
    """FR-28 says "more than" the band. 0.86 against 0.82 with a 0.04 band is exactly one band
    (and 0.04000000000000004 in floating point): float noise must not tip it into a pass."""
    got = judge({"review_f1": (0.82, 0.04)}, {"review_f1": (0.86, 0.04)})
    assert result_of(got, "review_f1") == "within_band"
    assert got["verdict"] == "fail"


def test_a_worse_quality_measure_fails_the_change_and_is_named_even_if_the_main_one_improved():
    """AC-28: quote validity fell by more than its band. A change that wins its target by making
    quotes less honest must not ship on the target's improvement alone."""
    got = judge(
        {"review_f1": (0.82, 0.04), "quote_validity": (0.99, 0.005)},
        {"review_f1": (0.90, 0.04), "quote_validity": (0.97, 0.005)},
    )
    assert got["verdict"] == "fail"
    assert got["failed_measures"] == ["quote_validity"]
    assert result_of(got, "quote_validity") == "worsened"


def test_truncation_rate_is_lower_is_better_so_a_rise_is_worsening_and_a_fall_is_not():
    """AC-28: truncation is the one lower-is-better quality measure. Reading it the usual way
    round would pass a change that cuts off more answers."""
    base = {"review_f1": (0.82, 0.04), "truncation_rate": (0.02, 0.005)}
    rose = judge(base, {"review_f1": (0.9, 0.04), "truncation_rate": (0.03, 0.005)})
    assert result_of(rose, "truncation_rate") == "worsened"
    assert rose["failed_measures"] == ["truncation_rate"]
    moved = next(m for m in rose["per_measure"] if m["key"] == "truncation_rate")
    assert moved["delta"] < 0  # worse, whichever way the raw rate moved
    assert moved["difference"] > 0  # the plain change: it rose

    fell = judge(base, {"review_f1": (0.9, 0.04), "truncation_rate": (0.0, 0.005)})
    assert result_of(fell, "truncation_rate") == "improved"
    assert fell["verdict"] == "pass"


def test_cost_and_time_are_shown_beside_the_scores_but_never_decide():
    """D-10: reading the whole document costs more. A change whose cost doubled, with every
    quality measure fine, still passes, and the owner sees the difference to weigh."""
    got = judge(
        {"review_f1": (0.82, 0.04), "cost_usd": (10.0, 0.1), "wall_time_s": (100.0, 5.0)},
        {"review_f1": (0.9, 0.04), "cost_usd": (20.0, 0.1), "wall_time_s": (400.0, 5.0)},
    )
    assert got["verdict"] == "pass"
    costs = {c["key"]: c for c in got["cost_time"]}
    assert costs["cost_usd"] == {
        "key": "cost_usd",
        "baseline": 10.0,
        "change": 20.0,
        "difference": 10.0,
    }
    assert costs["wall_time_s"]["difference"] == 300.0
    assert "cost_usd" not in {m["key"] for m in got["per_measure"]}


def test_coverage_and_consistency_dropping_beyond_their_band_never_fail_a_change():
    """D-12: coverage and run-to-run consistency are reported, not decisive. A change that reads
    less of the document is judged on the quality that results, not on the reading itself."""
    got = judge(
        {"review_f1": (0.82, 0.04), "coverage_avg": (1.0, 0.01), "consistency": (0.95, 0.02)},
        {"review_f1": (0.9, 0.04), "coverage_avg": (0.4, 0.01), "consistency": (0.5, 0.02)},
    )
    assert got["verdict"] == "pass"
    shown = {r["key"]: r for r in got["report_only"]}
    assert shown["coverage_avg"]["delta"] == pytest.approx(-0.6)
    assert shown["consistency"]["change_average"] == 0.5


def test_a_zero_noise_band_is_used_as_measured_not_widened():
    """Spec edge case: if the two baseline runs agreed exactly the band is 0 and a change need
    only beat 0; the scorer must not invent a minimum band."""
    base = {"review_f1": (0.8, 0.0), "quote_validity": (0.99, 0.0)}
    better = judge(base, {"review_f1": (0.801, 0.0), "quote_validity": (0.99, 0.0)})
    assert better["verdict"] == "pass"
    unchanged = judge(base, base)
    assert unchanged["verdict"] == "fail"  # the main measure did not improve
    assert unchanged["failed_measures"] == ["review_f1"]
    slightly_worse = judge(base, {"review_f1": (0.9, 0.0), "quote_validity": (0.989, 0.0)})
    assert slightly_worse["failed_measures"] == ["quote_validity"]


def test_a_measure_that_could_not_be_measured_does_not_fail_the_change():
    """AC-21: routing is out of date and so has no value. A measure with no data on either side
    is not a regression, though a main measure with no data means there is nothing to judge."""
    quiet = judge(
        {"review_f1": (0.8, 0.04), "routing_accuracy": (None, 0)},
        {"review_f1": (0.9, 0.04), "routing_accuracy": (None, 0)},
    )
    assert quiet["verdict"] == "pass"
    assert result_of(quiet, "routing_accuracy") == "not_measured"

    blind = judge(
        {"routing_accuracy": (None, 0)}, {"routing_accuracy": (None, 0)}, target="routing"
    )
    assert blind["verdict"] == "no_verdict"
    assert blind["no_verdict_reason"] == "main measure not measured"
    assert blind["failed_measures"] == []


def test_no_verdict_without_a_target_recorded_before_the_runs():
    """FR-28: the targeted area must be named before the change's runs are made, so a result
    cannot be judged afterwards by whichever measure happens to look good."""
    got = judge({"review_f1": (0.8, 0.04)}, {"review_f1": (0.99, 0.04)}, target=None)
    assert got["verdict"] == "no_verdict"
    assert got["no_verdict_reason"] == "target not recorded before runs"
    unknown = judge(target="vibes")
    assert unknown["verdict"] == "no_verdict"
    assert "vibes" in unknown["no_verdict_reason"]


def test_the_target_is_read_from_the_runs_and_cannot_be_chosen_when_judging():
    """FR-28: a target accepted as an argument could be picked after seeing which measure moved.
    The verdict takes it only from what both change runs recorded before they ran, and a run
    that recorded none leaves the whole comparison without a verdict."""
    base, change = fake_pair({"review_f1": (0.8, 0.04)}), fake_pair({"review_f1": (0.9, 0.04)})
    with pytest.raises(TypeError):
        s.verdict(base, change, target="review")

    recorded = fake_pair({"review_f1": (0.9, 0.04)}, target="review")
    assert s.verdict(base, recorded)["target_area"] == "review"

    one_missing = fake_pair({"review_f1": (0.9, 0.04)}, target="review")
    one_missing["runs"][1]["target_area"] = None
    got = s.verdict(base, one_missing)
    assert (got["verdict"], got["no_verdict_reason"]) == (
        "no_verdict",
        "target not recorded before runs",
    )


def test_a_change_whose_two_runs_recorded_different_targets_is_refused():
    """FR-28: two runs that each named a different area are not one change. Judging them by
    either target (or the better of the two) would let the choice follow the scores."""
    change = fake_pair({"review_f1": (0.9, 0.04)}, target="review")
    change["runs"][1]["target_area"] = "extraction"
    with pytest.raises(s.ComparisonRefused, match="different target areas"):
        s.verdict(fake_pair(), change)


@pytest.mark.parametrize(
    ("target", "measure"),
    [
        ("review", "review_f1"),
        ("extraction", "extraction_accuracy"),
        ("qa", "qa_evidence_found"),
        ("routing", "routing_accuracy"),
        ("portfolio", "portfolio_completeness"),
    ],
)
def test_each_target_area_is_judged_on_its_own_main_measure(target, measure):
    """A-1: review -> F1, extraction -> per-field accuracy, question answering -> evidence-found,
    routing -> routing accuracy, portfolio -> completeness."""
    got = judge({measure: (0.5, 0.02)}, {measure: (0.6, 0.02)}, target=target)
    assert got["main_measure"] == measure
    assert got["verdict"] == "pass"


def test_a_comparison_against_different_exam_content_is_refused():
    """FR-28: scores on different questions or keys are not comparable; a verdict across them
    would be meaningless and must not be produced."""
    with pytest.raises(s.ComparisonRefused, match="different exam content"):
        s.verdict(fake_pair(fingerprint="old"), fake_pair(fingerprint="new", target="review"))


def test_a_comparison_across_delivery_modes_is_refused():
    """AC-41: batch and normal delivery can differ in more than price. A baseline made in batch
    mode and a change run normally must not be compared."""
    with pytest.raises(s.ComparisonRefused, match="batch.*sync|delivery"):
        s.verdict(fake_pair(delivery="batch"), fake_pair(delivery="sync", target="review"))


def test_a_sample_run_is_never_compared_with_the_baseline():
    """FR-30: sample results are labelled not comparable. The verdict refuses them even if a
    caller forgot to check."""
    with pytest.raises(s.ComparisonRefused, match="sample"):
        s.verdict(fake_pair(), fake_pair(mode="sample", target="review"))


def test_the_verdict_names_the_baseline_and_the_change_when_they_are_labelled():
    """The report header says which baseline a change was judged against."""
    got = judge(label="baseline-2026-10")
    assert got["baseline"] == "baseline-2026-10"
    assert got["delivery"] == "batch"


# --- real runs: the noise band and consistency -------------------------------------------------

PLANTED = [f"p{i}" for i in range(25)]
CLEAN = [f"c{i}" for i in range(5)]


def review_content():
    rules = [plant(rule) for rule in PLANTED] + [compliant(rule) for rule in CLEAN]
    return make_content(documents=[make_doc("d1")], review=[review_item("d1", rules)])


def stamped(run: dict, content) -> dict:
    """The run as the runner records it: carrying the fingerprint of the content it was asked."""
    return {**run, "versions": {"content_fingerprint": content_fingerprint(content)}}


def review_run(found, false_alarms, run_id, **extra):
    """A review run that finds ``found`` of the 25 planted changes and raises ``false_alarms``."""
    flags = [flag(r) for r in PLANTED[:found]] + [flag(r) for r in CLEAN[:false_alarms]]
    run = results([review_result("d1", flags)], run_id=run_id, **extra)
    return stamped(run, review_content())


def test_the_noise_band_is_the_difference_between_the_two_runs():
    """AC-27, AC-28: F1 of 0.80 and 0.84 gives average 0.82 and band 0.04 (the baseline's own
    wobble), shown per run."""
    pair = s.score_pair(review_run(20, 5, "a"), review_run(21, 4, "b"), review_content(), texts={})
    f1 = next(m for m in pair["measures"] if m["key"] == "review_f1")
    assert f1["values"]["run_a"] == pytest.approx(0.80)
    assert f1["values"]["run_b"] == pytest.approx(0.84)
    assert f1["values"]["average"] == pytest.approx(0.82)
    assert f1["values"]["noise_band"] == pytest.approx(0.04)
    assert [r["run_id"] for r in pair["runs"]] == ["a", "b"]
    json.dumps(pair)  # plain JSON all the way down: the report writes it as is
    assert pair["delivery"] == "batch"


def test_a_real_change_is_judged_end_to_end_against_the_baseline():
    """AC-28 through the real scorer: baseline 0.80 / 0.84, change averaging 0.871. The change
    beats the 0.04 band, nothing else moved, so it passes; the same change run in normal
    delivery is refused (AC-41)."""
    content = review_content()
    base = s.score_pair(review_run(20, 5, "b1"), review_run(21, 4, "b2"), content, texts={})
    change = s.score_pair(
        review_run(22, 3, "c1", target_area="review"),
        review_run(22, 4, "c2", target_area="review"),
        content,
        texts={},
    )
    got = s.verdict(base, change)
    assert got["verdict"] == "pass"
    assert got["target_area"] == "review"
    assert result_of(got, "review_f1") == "improved"

    unnamed = s.score_pair(review_run(22, 3, "u1"), review_run(22, 4, "u2"), content, texts={})
    assert s.verdict(base, unnamed)["verdict"] == "no_verdict"

    sync = s.score_pair(
        review_run(22, 3, "s1", delivery="sync", target_area="review"),
        review_run(22, 4, "s2", delivery="sync", target_area="review"),
        content,
        texts={},
    )
    with pytest.raises(s.ComparisonRefused):
        s.verdict(base, sync)


def test_two_runs_on_different_content_or_delivery_do_not_form_a_pair():
    """FR-27: a noise band between runs of different questions or different delivery modes would
    measure the difference in the exam, not the AI's wobble."""
    other_content = review_run(20, 5, "b")
    other_content["versions"] = {"content_fingerprint": "someone-edited-a-key"}
    with pytest.raises(s.ComparisonRefused, match="different exam content"):
        s.score_pair(review_run(20, 5, "a"), other_content, review_content(), texts={})
    with pytest.raises(s.ComparisonRefused, match="delivery"):
        s.score_pair(
            review_run(20, 5, "a"),
            review_run(20, 5, "b", delivery="sync"),
            review_content(),
            texts={},
        )


def test_a_run_cannot_be_paired_with_itself():
    """FR-27: one run scored against itself has a noise band of exactly 0, so any later change
    that moved a measure at all would clear it. The pair must be two different runs."""
    run = review_run(20, 5, "only-run")
    with pytest.raises(s.ComparisonRefused, match="same run"):
        s.score_pair(run, dict(run), review_content(), texts={})
    with pytest.raises(s.ComparisonRefused, match="same run"):
        s.score_pair(
            run, {**review_run(21, 4, "other"), "run_id": "only-run"}, review_content(), texts={}
        )


def test_runs_scored_against_content_edited_since_they_were_made_are_refused():
    """Two runs that agree with each other can still both predate an edit to the keys. Scoring
    their old answers against the new keys would make a baseline of nothing; the pair is
    refused, and so is a run that never recorded which content it was asked."""
    run_a, run_b = review_run(20, 5, "a"), review_run(21, 4, "b")
    edited = review_content()
    edited.review[0].rules.append(compliant("added-later"))
    with pytest.raises(s.ComparisonRefused, match="changed since"):
        s.score_pair(run_a, run_b, edited, texts={})

    unrecorded = {**run_b, "versions": {}}
    with pytest.raises(s.ComparisonRefused, match="did not record"):
        s.score_pair({**run_a, "versions": {}}, unrecorded, review_content(), texts={})


def test_a_pair_keeps_everything_private_under_one_key():
    """FR-36: the committed report is built from the pair, so owner party names and quote text
    from the two runs' scores must sit under ``private`` and nowhere else in the pair."""
    run_a = review_run(20, 5, "a")
    run_a["calls"] = [
        call(quotes=[{"text": "SECRET quote", "word_for_word": False, "align_score": 50.0}])
    ]
    pair = s.score_pair(run_a, review_run(21, 4, "b"), review_content(), texts={})
    public = {k: v for k, v in pair.items() if k != "private"}
    assert "SECRET" not in json.dumps(public)
    assert pair["private"]["scores"]["a"]["private"]["invalid_quotes"][0]["text"] == "SECRET quote"


def test_consistency_is_the_share_of_items_answered_the_same_in_both_runs():
    """AC-13: four questions, one answered differently the second time: 75% consistent. An item
    that failed in one run is not compared, so an outage does not read as inconsistency."""
    qa = [
        QAItem(
            item_id=f"qa-{i}",
            doc_id="d1",
            category=SourceCategory.PUBLIC,
            source="s",
            question=f"q{i}",
            answer="a",
            passages=["The Supplier shall indemnify the Customer."],
        )
        for i in range(5)
    ]
    content = make_content(documents=[make_doc("d1")], qa=qa)
    hit = ["The Supplier shall indemnify the Customer."]

    def qa_run(cited, run_id, failed=()):
        items = []
        for i in range(5):
            prediction = {"citations": cited[i], "sources": [{"text": hit[0]}], "context_chars": 9}
            status = "failed" if i in failed else "scored"
            items.append(item(f"qa-{i}", "qa", "d1", prediction, status=status))
        return stamped(results(items, run_id=run_id), content)

    first = qa_run([hit, hit, [], hit, hit], "a")
    second = qa_run([hit, hit, [], [], hit], "b", failed=(4,))
    pair = s.score_pair(first, second, content, texts={})
    consistency = next(m for m in pair["measures"] if m["key"] == "consistency")
    assert consistency["n"] == 4  # the failed item is in neither comparison
    assert consistency["values"]["average"] == 0.75
    assert consistency["splits"]["qa"]["average"] == 0.75
    assert consistency["role"] == s.REPORT_ONLY


def test_the_same_amount_written_with_and_without_trailing_zeros_is_one_answer():
    """FR-13: run A answers "INR 2,500,000.00" and run B "INR 2,500,000". Both are the same
    amount and both score correct, so calling them different answers would show a model that
    only reformats as inconsistent and widen the report-only consistency figure for nothing."""
    content = extraction_content(key("value", "amount", "INR 2,500,000"))

    def run(answer, run_id):
        return stamped(results([extraction_result("d1", {"value": answer})], run_id=run_id), content)

    pair = s.score_pair(
        run("INR 2,500,000.00", "a"), run("INR 2,500,000", "b"), content, texts={}
    )
    consistency = next(m for m in pair["measures"] if m["key"] == "consistency")
    assert consistency["values"]["average"] == 1.0
    different = s.score_pair(run("INR 2,500,000.00", "a"), run("INR 250,000", "b"), content, texts={})
    assert next(m for m in different["measures"] if m["key"] == "consistency")["values"][
        "average"
    ] == 0.0


def test_a_finding_missed_in_either_run_is_listed_with_the_runs_that_missed_it():
    """FR-22: the owner reads the missed high and critical findings by name; a finding missed
    in only one run is still worth seeing, and the report says which run."""
    rules = [plant("both", "critical"), plant("only_b", "high"), plant("neither", "high")]
    content = make_content(documents=[make_doc("d1")], review=[review_item("d1", rules)])
    run_a = results([review_result("d1", [flag("only_b"), flag("neither")])], run_id="a")
    run_b = results([review_result("d1", [flag("neither")])], run_id="b")
    run_a, run_b = stamped(run_a, content), stamped(run_b, content)
    pair = s.score_pair(run_a, run_b, content, texts={})
    missed = {(m["rule"], tuple(m["runs_missed"])) for m in pair["missed_high_critical"]}
    assert missed == {("both", ("a", "b")), ("only_b", ("b",))}


def test_the_hand_built_pairs_have_the_shape_the_real_pairs_have():
    """The verdict tests above use fake_pair; if score_pair's measure shape drifted from it, those
    tests would pass against a shape the real scorer no longer produces."""
    pair = s.score_pair(review_run(20, 5, "a"), review_run(21, 4, "b"), review_content(), texts={})
    real = pair["measures"][0]
    fake = fake_pair()["measures"][0]
    assert set(fake) <= set(real)
    assert set(fake["values"]) == set(real["values"])
    keys = [m["key"] for m in pair["measures"]]
    assert keys == [k for k in s.SPECS if k in keys]  # report order is the SPECS order
