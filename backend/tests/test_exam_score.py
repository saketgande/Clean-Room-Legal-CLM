"""Exam scorer: review, extraction, question answering, portfolio, routing and the label-free checks.

specs/005-ai-eval-baseline T011 (FR-9..FR-14, FR-19, FR-20, FR-22..FR-26). Pure: results and
content are built in memory from invented text, so nothing here touches a database, the network
or a real contract, and nothing can spend money. The pair and verdict logic lives in
``test_exam_compare.py``, which reuses the builders below.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from eval.exam import score as s
from eval.exam.content import (
    OWNER_FILES,
    ExamContent,
    ExamDocument,
    ExtractionItem,
    FieldKey,
    OwnerFactKey,
    OwnerManifestEntry,
    PlantedChange,
    PortfolioQuestion,
    QAItem,
    ReviewItem,
    ReviewRuleKey,
    RoutingKey,
    RoutingRequest,
    SourceCategory,
    content_fingerprint,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 6, tzinfo=UTC)
FILE_SHA, TEXT_SHA = "f" * 64, "e" * 64


# --- builders (shared with test_exam_compare.py) -----------------------------------------


def make_doc(doc_id, category=SourceCategory.CONSTRUCTED, **extra) -> ExamDocument:
    fields = {
        "dataset": None,
        "file": f"{doc_id}.txt",
        "mime_type": "text/plain",
        "file_sha256": "0" * 64,
        "title": doc_id,
        "ai_steps": ["metadata"],
    }
    return ExamDocument(doc_id=doc_id, category=category, **{**fields, **extra})


def plant(rule, severity="medium", start=100, status="deviation") -> ReviewRuleKey:
    return ReviewRuleKey(
        rule=rule,
        status=status,
        severity=severity,
        planted=PlantedChange(
            rule=rule,
            status=status,
            original_text="before",
            changed_text="after",
            anchor_text="a" * 60,
            char_start=start,
            severity=severity,
            severity_source=f"Test Playbook / {rule}",
        ),
    )


def compliant(rule) -> ReviewRuleKey:
    return ReviewRuleKey(rule=rule, status="compliant")


def review_item(doc_id, rules, *, control=False, playbook="cp_msa", category=None) -> ReviewItem:
    return ReviewItem(
        item_id=f"rv-{doc_id}",
        doc_id=doc_id,
        category=category or SourceCategory.CONSTRUCTED,
        source="constructed",
        playbook=playbook,
        control=control,
        rules=rules,
    )


def flag(rule, status="deviation", **extra) -> dict:
    return {
        "rule": rule,
        "status": status,
        "severity": "medium",
        "quote": None,
        "attributed_by": "rule_index",
        **extra,
    }


def item(item_id, task, doc_id, prediction=None, *, status="scored", failure=None) -> dict:
    return {
        "item_id": item_id,
        "task": task,
        "doc_id": doc_id,
        "status": status,
        "failure": failure,
        "call_ids": [],
        "prediction": prediction,
    }


def review_result(doc_id, flags) -> dict:
    return item(f"rv-{doc_id}", "review", doc_id, {"flags": flags})


def extraction_result(doc_id, fields, item_id=None) -> dict:
    return item(item_id or f"ex-{doc_id}", "extraction", doc_id, {"fields": fields})


def make_content(**parts) -> ExamContent:
    return ExamContent(**parts)


def results(items=(), calls=(), **extra) -> dict:
    return {
        "schema": "aegis-exam-results/1",
        "run_id": "run-a",
        "kind": "exam",
        "mode": "full",
        "delivery": "batch",
        "outcome": "complete",
        "contains_errors": False,
        "failed_call_rate": 0.0,
        "versions": {"content_fingerprint": "fp"},
        "estimate": {},
        "excluded": {},
        "batches": [],
        "items": list(items),
        "calls": list(calls),
        "wall_time_s": 10.0,
        **extra,
    }


def call(call_type="playbook_review", **extra) -> dict:
    return {
        "call_id": "c",
        "item_ids": [],
        "doc_id": "d1",
        "call_type": call_type,
        "batched": False,
        "model": "claude-sonnet-4-6",
        "status": "succeeded",
        "stop_reason": "tool_use",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "latency_ms": None,
        "cost_usd": 0.0,
        "chars_seen": None,
        "chars_total": None,
        "quotes": [],
        **extra,
    }


def score(res, content, texts=None) -> dict:
    """Every test passes ``texts`` so the scorer never reads fixtures from disk."""
    return s.score_run(res, content, texts={} if texts is None else texts)


def value(scores, key):
    return scores["measures"][key]["value"]


def text_with(quote, at) -> str:
    """Invented document text with ``quote`` starting at character ``at``."""
    pad = ("lorem ipsum dolor sit amet. " * (at // 28 + 2))[:at]
    return pad + quote + " and some trailing text."


# --- contract review (FR-22, FR-5, FR-7) --------------------------------------------------


def test_review_precision_recall_f1_and_the_missed_critical_finding_by_name():
    """AC-22: 10 planted changes, 8 found, 2 false alarms. A scorer that counted units wrongly
    or dropped the severity would misreport F1 or lose the critical miss the owner must see."""
    rules = [plant(f"p{i}", "critical" if i == 3 else "medium") for i in range(10)]
    rules += [compliant("c0"), compliant("c1")]
    content = make_content(documents=[make_doc("d1")], review=[review_item("d1", rules)])
    flags = [flag(f"p{i}") for i in range(10) if i not in (3, 4)] + [flag("c0"), flag("c1")]
    scores = score(results([review_result("d1", flags)]), content)
    m = scores["measures"]
    assert m["review_precision"]["value"] == pytest.approx(0.8)
    assert m["review_recall"]["value"] == pytest.approx(0.8)
    assert m["review_f1"]["value"] == pytest.approx(0.8)
    assert m["review_f1"]["n"] == 12  # 8 found + 2 missed + 2 false alarms
    assert scores["missed_high_critical"] == [
        {"item_id": "rv-d1", "rule": "p3", "severity": "critical"}
    ]  # p4 was missed too, but it is medium
    assert {a["rule"] for a in scores["false_alarms"]} == {"c0", "c1"}


def test_the_count_behind_the_missed_critical_list_is_every_high_or_critical_finding_scored():
    """FR-26: "2 missed" means little without how many there were to miss. The count holds the
    high and critical findings in scored review documents, found or missed, and nothing else:
    not medium ones, not false alarms, not a scanned copy (a side line, never in the headline)."""
    rules = [
        plant("crit_found", "critical"),
        plant("high_missed", "high"),
        plant("crit_missed", "critical"),
        plant("medium_missed", "medium"),
        compliant("clean"),
    ]
    content = make_content(
        documents=[make_doc("d1"), make_doc("d1-scan", twin_of="d1")],
        review=[review_item("d1", rules)],
    )
    flags = [flag("crit_found"), flag("clean")]
    scores = score(
        results([review_result("d1", flags), review_result("d1-scan", [])]),
        content,
    )
    assert scores["counts"]["review"]["high_critical_findings"] == 3
    assert [m["rule"] for m in scores["missed_high_critical"]] == ["high_missed", "crit_missed"]


def test_the_amount_signature_ignores_trailing_zeros_and_digit_grouping():
    """FR-13: run-to-run consistency compares these signatures. "2,500,000.00", "2,500,000" and
    "₹25,00,000" are one amount; the Decimal's own text ("2500000.00") would call them three."""
    truth = s._Truth("value", "amount", "INR 2,500,000", None, None)
    signature = {
        got: s._normalised(truth, {"value": got})
        for got in ("INR 2,500,000.00", "INR 2,500,000", "₹25,00,000", "Rs. 25 lakh")
    }
    assert set(map(repr, signature.values())) == {repr(["2500000", "INR"])}
    assert s._normalised(truth, {"value": "INR 2,500.50"}) == ["2500.5", "INR"]
    assert s._normalised(truth, {"value": "USD 0.00"}) == ["0", "USD"]


def test_a_flag_on_a_control_document_is_a_false_alarm():
    """AC-5: the unchanged control has nothing to find. If its flags were ignored, a model that
    cries wolf on clean paper would score no worse than one that stays quiet."""
    content = make_content(
        documents=[make_doc("ctl")],
        review=[review_item("ctl", [compliant("r1")], control=True)],
    )
    scores = score(results([review_result("ctl", [flag("r1"), flag("anything_else")])]), content)
    assert value(scores, "review_precision") == 0.0
    assert [a["rule"] for a in scores["false_alarms"]] == ["r1", "anything_else"]

    # A control is a control whatever its rule list says: nothing on it is ever a finding.
    odd = make_content(
        documents=[make_doc("ctl")], review=[review_item("ctl", [plant("r1")], control=True)]
    )
    got = score(results([review_result("ctl", [flag("r1")])]), odd)
    assert got["false_alarms"] == [{"item_id": "rv-ctl", "rule": "r1"}]
    assert got["measures"]["review_recall"]["n"] == 0


def test_a_flag_the_runner_could_not_attribute_to_a_rule_is_a_false_alarm():
    """FR-22: a finding that matches no rule must not be quietly dropped, or a model could pad
    its output with free-text findings at no cost to precision."""
    content = make_content(documents=[make_doc("d1")], review=[review_item("d1", [plant("p0")])])
    lost = {"rule": None, "status": "deviation", "quote": "x", "attributed_by": "none"}
    scores = score(results([review_result("d1", [flag("p0"), lost])]), content)
    assert value(scores, "review_precision") == 0.5
    assert scores["false_alarms"] == [{"item_id": "rv-d1", "rule": None}]


def test_a_public_label_has_no_severity_so_its_miss_is_never_called_critical():
    """FR-5: a severity is only ever the playbook's, set when a contract was built. Inventing
    one for a lawyer-labelled public item would put a made-up name on the missed list."""
    rule = ReviewRuleKey(rule="uncapped_liability", status="deviation", evidence_quote="q")
    content = make_content(
        documents=[make_doc("cuad-1", SourceCategory.PUBLIC)],
        review=[review_item("cuad-1", [rule], category=SourceCategory.PUBLIC)],
    )
    scores = score(results([review_result("cuad-1", [])]), content)
    assert value(scores, "review_recall") == 0.0
    assert scores["missed_high_critical"] == []


def test_review_is_split_by_source_category_and_by_where_the_finding_sits():
    """FR-2, FR-7: the headline hides the past-25k weakness unless recall is split by position
    (a planted change by its recorded offset, a public label by locating its quote)."""
    quote = "Liability is unlimited for every claim of any kind."
    public = ReviewRuleKey(rule="uncapped", status="deviation", evidence_quote=quote)
    content = make_content(
        documents=[make_doc("pub", SourceCategory.PUBLIC), make_doc("con")],
        review=[
            review_item("pub", [public], category=SourceCategory.PUBLIC),
            review_item("con", [plant("early", start=100), plant("late", start=30_000)]),
        ],
    )
    run = results([review_result("pub", [flag("uncapped")]), review_result("con", [flag("early")])])
    scores = score(run, content, texts={"pub": text_with(quote, 26_000)})
    splits = scores["measures"]["review_recall"]["splits"]
    assert splits["past_25k"] == {"n": 2, "value": 0.5}  # public label found, late plant missed
    assert splits["before_25k"] == {"n": 1, "value": 1.0}
    assert splits["public"]["value"] == 1.0
    assert splits["constructed"]["value"] == 0.5


def test_a_scanned_copy_is_scored_beside_its_original_and_kept_out_of_the_headline():
    """FR-8: the scan twin shares its original's answer record. Counting it in the headline
    would double-weight 6 documents; hiding it would hide what the scan path costs."""
    rules = [plant("p0"), plant("p1")]
    content = make_content(
        documents=[make_doc("d1"), make_doc("scan-d1", mime_type="application/pdf", twin_of="d1")],
        review=[review_item("d1", rules)],
    )
    run = results(
        [
            review_result("d1", [flag("p0"), flag("p1")]),
            item("rv-scan-d1", "review", "scan-d1", {"flags": []}),
        ]
    )
    scores = score(run, content)
    assert value(scores, "review_f1") == 1.0
    assert scores["measures"]["review_f1"]["n"] == 2  # the twin's 2 findings are not in it
    twins = scores["measures"]["review_f1_scan_twins"]
    assert twins["value"] == 0.0
    assert twins["splits"]["text_original"]["value"] == 1.0
    assert twins["splits"]["scanned"]["value"] == 0.0


def test_nda_verdict_accuracy_is_three_way_on_the_nda_checklist_only():
    """FR-4b: ContractNLI verdicts are supported / contradicted / not mentioned, not just
    flagged or not, so a flagged-but-wrong-kind answer must count as wrong."""
    rules = [
        ReviewRuleKey(rule="h1", status="compliant"),
        ReviewRuleKey(rule="h2", status="deviation", evidence_quote="x"),
        ReviewRuleKey(rule="h3", status="missing"),
    ]
    content = make_content(
        documents=[make_doc("nda", SourceCategory.PUBLIC)],
        review=[
            review_item("nda", rules, playbook="nda_checklist", category=SourceCategory.PUBLIC)
        ],
    )
    flags = [flag("h2", "deviation"), flag("h3", "deviation")]  # h3 should have been "missing"
    scores = score(results([review_result("nda", flags)]), content)
    assert value(scores, "nda_verdict_accuracy") == pytest.approx(2 / 3)
    assert scores["measures"]["nda_verdict_accuracy"]["n"] == 3


def test_failed_and_not_attempted_items_are_listed_and_never_scored():
    """AC-40, FR-40: a failed item must not be scored as an answer. If its (empty or fallback)
    prediction were scored, an outage would read as 'the AI found nothing'."""
    content = make_content(
        documents=[make_doc("d1"), make_doc("d2"), make_doc("d3")],
        review=[review_item(f"d{i}", [plant("p0")]) for i in (1, 2, 3)],
    )
    run = results(
        [
            review_result("d1", [flag("p0")]),
            item(
                "rv-d2",
                "review",
                "d2",
                {"flags": []},
                status="failed",
                failure={"call_id": "c9", "reason": "provider overloaded"},
            ),
            item("rv-d3", "review", "d3", None, status="not_attempted"),
        ]
    )
    scores = score(run, content)
    assert value(scores, "review_recall") == 1.0
    assert scores["measures"]["review_recall"]["n"] == 1
    assert {(f["item_id"], f["status"]) for f in scores["failed_items"]} == {
        ("rv-d2", "failed"),
        ("rv-d3", "not_attempted"),
    }
    assert scores["failed_items"][0]["reason_code"] == "provider overloaded"


def test_a_failure_reason_with_detail_is_public_as_a_code_only():
    """FR-36: the runner's reason can carry the model's output or contract text (a validation
    error quotes the offending value). Only the label before the colon may sit outside
    ``private``; a reason that is not a plain label is reported as "other"."""
    content = make_content(
        documents=[make_doc("d1"), make_doc("d2")],
        review=[review_item("d1", [plant("p0")]), review_item("d2", [plant("p0")])],
    )
    run = results(
        [
            item(
                "rv-d1",
                "review",
                "d1",
                None,
                status="failed",
                failure={"reason": "upstream: SECRET clause text echoed in the error"},
            ),
            item(
                "rv-d2",
                "review",
                "d2",
                None,
                status="failed",
                failure={"reason": "SECRET text with no label at all, just a long sentence."},
            ),
        ]
    )
    scores = score(run, content)
    assert {f["item_id"]: f["reason_code"] for f in scores["failed_items"]} == {
        "rv-d1": "upstream",
        "rv-d2": "other",
    }
    public = {k: v for k, v in scores.items() if k != "private"}
    assert "SECRET" not in json.dumps(public)
    assert "SECRET clause text" in scores["private"]["failure_reasons"]["rv-d1"]


def test_a_run_scored_against_other_content_than_it_answered_is_flagged():
    """A key reloaded after the run means its answers are scored against a different exam. The
    scores say so, so a single-run report is never presented as current by mistake."""
    content = make_content(documents=[make_doc("d1")])
    current = results(versions={"content_fingerprint": content_fingerprint(content)})
    assert score(current, content)["run"]["content_current"] is True
    assert score(results(), content)["run"]["content_current"] is False


# --- field extraction (FR-23) -----------------------------------------------------------


def key(field, kind, value, quote=None) -> FieldKey:
    return FieldKey(field=field, kind=kind, value=value, quote=quote)


def extraction_content(*keys, doc="d1", **parts) -> ExamContent:
    item_ = ExtractionItem(
        item_id=f"ex-{doc}",
        doc_id=doc,
        category=SourceCategory.CONSTRUCTED,
        source="constructed",
        fields=list(keys),
    )
    return make_content(documents=[make_doc(doc)], extraction=[item_], **parts)


def test_parties_and_dates_are_compared_after_normalising_the_formatting():
    """AC-23: "Beta Pvt Ltd, Acme Ltd." and "2024-04-01" are the right answer to a key of
    ["Acme Ltd", "Beta Pvt Ltd"] and "1 April 2024". Comparing raw strings would count
    formatting as error and bury real differences between a baseline and a change."""
    content = extraction_content(
        key("parties", "parties", ["Acme Ltd", "Beta Pvt Ltd"]),
        key("effective_date", "date", "1 April 2024"),
    )
    fields = {"parties": "Beta Pvt Ltd, Acme Ltd.", "effective_date": "2024-04-01"}
    scores = score(results([extraction_result("d1", fields)]), content)
    assert value(scores, "extraction_accuracy") == 1.0
    assert scores["measures"]["extraction_accuracy"]["n"] == 3  # two parties + one date


def test_each_key_party_is_its_own_unit_and_extra_parties_are_not_errors():
    """FR-23: parties are scored as separate parties. One missing party is half a field wrong,
    and an extra one is reported as extra rather than counted against the answer."""
    content = extraction_content(key("parties", "parties", ["Acme Ltd", "Beta Pvt Ltd"]))
    both = ["Beta Pvt. Ltd.", "M/s Acme Limited", "Gamma LLC"]
    scores = score(results([extraction_result("d1", {"parties": both})]), content)
    assert value(scores, "extraction_accuracy") == 1.0
    assert scores["counts"]["extraction"]["extra_parties"] == 1

    half = score(results([extraction_result("d1", {"parties": ["Acme Limited"]})]), content)
    assert value(half, "extraction_accuracy") == 0.5
    assert half["fields"]["parties"] == {
        "n": 2,
        "accuracy": 0.5,
        "before_25k": {"n": 0, "value": None},
        "past_25k": {"n": 0, "value": None},
    }


def test_an_ampersand_or_a_bank_suffix_does_not_split_one_party_into_two():
    """FR-23: "Larsen & Toubro Infotech Limited" was split at "&" and "Credit One Bank, N.A." at
    its comma, so the app's right answer missed both keyed parties. "A and B" still splits."""
    content = extraction_content(
        key("parties", "parties", ["Larsen & Toubro Infotech Limited", "Credit One Bank, N.A."])
    )
    named = "Larsen & Toubro Infotech Limited, Credit One Bank, N.A."
    scores = score(results([extraction_result("d1", {"parties": named})]), content)
    assert value(scores, "extraction_accuracy") == 1.0
    assert scores["counts"]["extraction"]["extra_parties"] == 0
    assert s._split_parties("Acme Pvt. Ltd. and Beta, S.A.") == ["Acme Pvt. Ltd.", "Beta, S.A."]
    for name in ("Wells Fargo Bank, National Association", "Acme, S.p.A.", "Acme, Sdn. Bhd."):
        assert s._split_parties(name) == [name]


def test_a_clause_fact_the_document_does_not_state_is_counted_not_scored():
    """A clause answer is every app clause of the field's kinds (renewal_term includes the term
    and termination clause), so a "not stated" renewal key failed on every contract that has a
    termination clause, whatever the AI did (spec D-16). It is left out and counted. A
    not-stated value field and liability_cap (answered only by its own clause kind) are still
    scored, so an invented cap is still caught."""
    content = extraction_content(
        key("renewal_term", "passage", None),
        key("governing_law", "jurisdiction", None),
        key("liability_cap", "passage", None),
    )
    fields = {
        "renewal_term": ["This Agreement ends three years after signature."],
        "liability_cap": ["Total liability shall not exceed the fees paid."],
    }
    scores = score(results([extraction_result("d1", fields)]), content)
    assert scores["measures"]["extraction_accuracy"]["n"] == 2  # governing law + liability cap
    assert scores["fields"]["liability_cap"]["accuracy"] == 0.0
    assert scores["fields"]["governing_law"]["accuracy"] == 1.0
    assert "renewal_term" not in scores["fields"]
    assert scores["counts"]["extraction"]["not_stated_passages"] == {"renewal_term": 1}


def test_an_answer_past_25k_counts_in_the_past_25k_figure_only():
    """AC-23: accuracy before and past 25k is the point of the split. A fact that sits past
    25,000 characters must not be averaged into the early figure."""
    early, late = "This Agreement starts on 1 April 2024.", "Governed by the laws of Delaware."
    text = early + " " + "z" * (26_000 - len(early) - 1) + late
    content = extraction_content(
        key("effective_date", "date", "2024-04-01", early),
        key("governing_law", "jurisdiction", "delaware", late),
    )
    fields = {"effective_date": "2025-01-01", "governing_law": "State of Delaware"}
    scores = score(results([extraction_result("d1", fields)]), content, texts={"d1": text})
    splits = scores["measures"]["extraction_accuracy"]["splits"]
    assert splits["before_25k"] == {"n": 1, "value": 0.0}
    assert splits["past_25k"] == {"n": 1, "value": 1.0}
    assert value(scores, "extraction_accuracy") == 0.5


@pytest.mark.parametrize(
    ("want", "got", "same"),
    [
        ("INR 2,500,000.00", "₹25,00,000", True),
        ("Rs. 25 lakh", {"amount": "2500000.00", "currency": "INR"}, True),
        ("2 crore", "INR 20000000", True),
        ("USD 5,000", "$5,000", True),
        ("USD 5,000", "INR 5,000", False),
        ("INR 2,500,000", "INR 250,000", False),
    ],
)
def test_amounts_are_compared_as_numbers_and_currencies(want, got, same):
    """FR-23: lakh, crore, Indian digit grouping and currency symbols are formatting. Two
    amounts that differ only in how they are written must match; a different currency must not."""
    content = extraction_content(key("value", "amount", want))
    scores = score(results([extraction_result("d1", {"value": got})]), content)
    assert value(scores, "extraction_accuracy") == (1.0 if same else 0.0)


@pytest.mark.parametrize(
    ("want", "got", "same"),
    [
        ("Delaware", "State of Delaware", True),
        ("delaware", "Delaware, USA", True),
        ("India", "the laws of India", True),
        ("Delaware", "laws of the State of Delaware", True),
        ("New York", "New York City", False),
    ],
)
def test_jurisdictions_use_the_apps_own_normaliser(want, got, same):
    """FR-23: "State of Delaware" and "Delaware, USA" are one jurisdiction, as in the app's own
    graph; "New York" and "New York City" must stay distinct."""
    content = extraction_content(key("governing_law", "jurisdiction", want))
    scores = score(results([extraction_result("d1", {"governing_law": got})]), content)
    assert value(scores, "extraction_accuracy") == (1.0 if same else 0.0)


def test_a_fact_the_document_does_not_state_is_right_only_when_the_answer_is_empty():
    """Spec edge case: "not stated" is a correct answer and an invented value is wrong. If any
    answer matched a not-stated key, hallucinated facts would score as correct."""
    content = extraction_content(key("governing_law", "jurisdiction", None))
    for given, expected in [(None, 1.0), ("Not stated in the document", 1.0), ("Delaware", 0.0)]:
        got = score(results([extraction_result("d1", {"governing_law": given})]), content)
        assert value(got, "extraction_accuracy") == expected


@pytest.mark.parametrize(
    ("term", "answer", "same"),
    [
        ("thirty-six (36) months", "2027-03-31", True),
        ("thirty-six (36) months", "36 months", True),
        ("thirty-six (36) months", "3 years", True),
        ("36 months", "thirty-six (36) months", True),
        ("twenty-four (24) months", "2026-03-31", True),
        ("forty-eight (48) months", "4 years", True),
        ("sixty (60) days", "2024-05-31", True),
        ("thirty (30) days", "2024-05-01", True),
        ("thirty days", "2024-05-01", True),
        ("twenty four months", "2026-04-01", True),
        ("thirty-six (36) months", "2024-10-01", False),
        ("thirty-six (36) months", "2024-06-30", False),
        ("sixty (60) days", "2024-05-01", False),
    ],
)
def test_a_term_written_in_words_and_digits_is_read_as_its_digits(term, answer, same):
    """FR-23: contracts write "thirty-six (36) months". Reading the last number word ("six") or
    giving up on "thirty (30) days" scored correct answers wrong and lowered the headline
    extraction figure for no reason; the bracketed digits are the figure."""
    content = extraction_content(
        key("effective_date", "date", "2024-04-01"), key("end_date_or_term", "date_or_term", term)
    )
    fields = {"effective_date": "2024-04-01", "expiration_date": answer}
    got = score(results([extraction_result("d1", fields)]), content)
    assert value(got, "extraction_accuracy") == (1.0 if same else 0.5)


def test_a_term_key_is_compared_with_start_plus_duration():
    """FR-23: a contract that says "3 years" has an end date the AI states as a date. The two
    agree when start + term lands on it (a day either way), and not otherwise."""
    content = extraction_content(
        key("effective_date", "date", "2024-04-01"),
        key("end_date_or_term", "date_or_term", "three (3) years"),
    )
    ok = {"effective_date": "2024-04-01", "expiration_date": "2027-03-31"}
    off = {"effective_date": "2024-04-01", "end_date_or_term": "2026-04-01"}
    assert value(score(results([extraction_result("d1", ok)]), content), "extraction_accuracy") == 1
    got = score(results([extraction_result("d1", off)]), content)
    assert value(got, "extraction_accuracy") == 0.5


def test_a_passage_field_is_right_when_the_extracted_clause_covers_the_key_sentence():
    """FR-23: renewal, notice and liability are clause text. They match on overlap with the key's
    sentence, not on equal strings; a clause about something else must not match."""
    sentence = "This Agreement renews automatically for successive one year terms unless cancelled."
    content = extraction_content(key("renewal_term", "passage", "auto", sentence))
    same = "The agreement renews automatically for successive one year terms unless cancelled."
    other = "Payment is due within thirty days of the date of each invoice issued by the Supplier."
    assert (
        value(
            score(results([extraction_result("d1", {"renewal_term": same})]), content),
            "extraction_accuracy",
        )
        == 1.0
    )
    assert (
        value(
            score(results([extraction_result("d1", {"renewal_term": other})]), content),
            "extraction_accuracy",
        )
        == 0.0
    )


def test_a_scanned_copy_is_extracted_beside_its_original_and_kept_out_of_the_headline():
    """FR-8: the scan path's effect on extraction is shown next to the text score, and the
    twin's facts never enter the main extraction figure."""
    content = make_content(
        documents=[make_doc("d1"), make_doc("scan-d1", mime_type="application/pdf", twin_of="d1")],
        extraction=[
            ExtractionItem(
                item_id="ex-d1",
                doc_id="d1",
                category=SourceCategory.CONSTRUCTED,
                source="constructed",
                fields=[key("governing_law", "jurisdiction", "india")],
            )
        ],
    )
    run = results(
        [
            extraction_result("d1", {"governing_law": "India"}),
            extraction_result("scan-d1", {"governing_law": None}, item_id="ex-scan-d1"),
        ]
    )
    scores = score(run, content)
    assert value(scores, "extraction_accuracy") == 1.0
    assert scores["measures"]["extraction_accuracy"]["n"] == 1
    twins = scores["measures"]["extraction_accuracy_scan_twins"]
    assert twins["value"] == 0.0
    assert twins["splits"]["text_original"] == {"n": 1, "value": 1.0}


# --- owner-confirmed keys (FR-19, FR-20, FR-23) -------------------------------------------


def owner_key(
    doc_id,
    field,
    value,
    *,
    status="confirmed",
    start=100,
    file_sha=FILE_SHA,
    frozen=TEXT_SHA,
    quote="a sentence",
) -> OwnerFactKey:
    return OwnerFactKey(
        doc_id=doc_id,
        field=field,
        status=status,
        value=value,
        quote=quote,
        char_start=start,
        char_end=None if start is None else start + 10,
        suggested_value=value,
        suggested_quote=quote,
        suggested_by="claude-sonnet-5-5",
        file_sha256=file_sha,
        frozen_text_sha256=frozen,
        sheet_sha256="s" * 64,
        loaded_at=NOW,
    )


def manifest(doc_id="owner-01", kind="text", frozen=TEXT_SHA) -> OwnerManifestEntry:
    return OwnerManifestEntry(
        doc_id=doc_id,
        name=OWNER_FILES[int(doc_id.split("-")[1]) - 1],
        file_sha256=FILE_SHA,
        mime_type="application/pdf",
        pages=3,
        kind=kind,
        frozen_text_sha256=frozen,
        frozen_method="native_text",
        imported_at=NOW,
    )


def owner_docs():
    return [
        make_doc(
            "owner-01", SourceCategory.OWNER, mime_type="application/pdf", text_mode="forced_ocr"
        ),
        make_doc(
            "owner-01-asread",
            SourceCategory.OWNER,
            mime_type="application/pdf",
            as_read_today_of="owner-01",
        ),
    ]


def test_a_stamp_only_scan_is_scored_on_its_full_text_and_again_as_the_app_reads_it():
    """AC-23, FR-23: the main figure uses the full frozen text; the as-read-today score has its
    own line. Mixing them would hide the OCR gap, and dropping either would hide the fix."""
    content = make_content(
        documents=owner_docs(),
        owner_manifest=[manifest(kind="stamp_only_scan")],
        owner_keys=[owner_key("owner-01", "governing_law", "India")],
    )
    run = results(
        [
            extraction_result("owner-01", {"governing_law": "India"}),
            extraction_result("owner-01-asread", {"governing_law": None}),
        ]
    )
    scores = score(run, content)
    assert value(scores, "extraction_accuracy") == 1.0
    assert scores["measures"]["extraction_accuracy"]["n"] == 1
    assert scores["measures"]["extraction_accuracy"]["splits"]["owner"]["value"] == 1.0
    assert value(scores, "extraction_accuracy_asread") == 0.0
    assert scores["measures"]["extraction_accuracy_asread"]["n"] == 1


def test_owner_keys_that_are_unmarked_or_out_of_date_are_left_out_of_scoring():
    """FR-19, FR-20: only rows the owner marked, tied to the file and frozen text the contract
    has now, may score. A key for a changed file or a row the owner could not decide would
    otherwise count against the AI for a question with no trustworthy answer."""
    stale = owner_key("owner-01", "notice_period", "30 days", file_sha="9" * 64)
    content = make_content(
        documents=owner_docs()[:1],
        owner_manifest=[manifest()],
        owner_keys=[
            owner_key("owner-01", "governing_law", "India"),
            owner_key("owner-01", "renewal_term", None, status="unmarked"),
            stale,
            owner_key("owner-01", "effective_date", "2024-04-01"),
        ],
    )
    run = results(
        [extraction_result("owner-01", {"governing_law": "India", "effective_date": "2020-01-01"})],
        excluded={"owner_keys_out_of_date": ["owner-01:effective_date"]},
    )
    scores = score(run, content)
    assert scores["measures"]["extraction_accuracy"]["n"] == 1  # governing law only
    assert scores["counts"]["extraction"]["owner_keys_skipped"] == {"unmarked": 1, "out_of_date": 2}


def test_the_rate_at_which_the_owner_changed_the_prefilled_answer_is_reported():
    """AC-19: how often the owner overruled the pre-fill says how far to trust it. Unmarked rows
    are counted apart and do not dilute the rate."""
    keys = [owner_key("owner-01", "governing_law", "India") for _ in range(3)]
    keys += [owner_key("owner-01", "governing_law", "Delhi", status="corrected")]
    keys += [owner_key("owner-01", "renewal_term", None, status="unmarked") for _ in range(2)]
    scores = score(results(), make_content(documents=owner_docs()[:1], owner_keys=keys))
    owner = scores["counts"]["owner"]
    assert owner == {"rows_marked": 4, "rows_unmarked": 2, "prefill_changed_rate": 0.25}


# --- question answering (FR-24) ---------------------------------------------------------


PASSAGE = "The Supplier shall indemnify the Customer against third party claims of infringement."


def qa_content(passage_at):
    q = QAItem(
        item_id="qa-001",
        doc_id="d1",
        category=SourceCategory.PUBLIC,
        source="LegalBench-RAG / cuad",
        question="Who indemnifies?",
        answer="The Supplier.",
        passages=[PASSAGE],
    )
    return make_content(documents=[make_doc("d1", SourceCategory.PUBLIC)], qa=[q]), {
        "d1": text_with(PASSAGE, passage_at)
    }


def qa_result(sources, citations, context_chars=500):
    prediction = {
        "answer": "x",
        "answer_mode": "rag",
        "citations": citations,
        "context_chars": context_chars,
        "sources": [{"bucket": "semantic", "text": t, "contract_id": None} for t in sources],
    }
    return item("qa-001", "qa", "d1", prediction)


def test_search_found_and_answer_cited_are_separate_and_each_split_by_25k():
    """AC-24: when search finds the passage but the answer does not cite it, the fault is the
    model; when search misses it, the fault is retrieval. Folding them into one number, or
    ignoring where the passage sits, would make a retrieval fix and a prompt fix indistinguishable."""
    content, texts = qa_content(26_000)
    found_not_cited = qa_result([PASSAGE + " More text."], ["Something unrelated entirely."])
    scores = score(results([found_not_cited]), content, texts)
    assert value(scores, "qa_search_found") == 1.0
    assert value(scores, "qa_evidence_found") == 0.0
    assert scores["measures"]["qa_search_found"]["splits"] == {"past_25k": {"n": 1, "value": 1.0}}
    assert scores["measures"]["qa_evidence_found"]["splits"] == {"past_25k": {"n": 1, "value": 0.0}}

    both = qa_result(
        ["unrelated chunk"], ["shall indemnify the Customer against third party claims"]
    )
    early_content, early_texts = qa_content(100)
    got = score(results([both]), early_content, early_texts)
    assert value(got, "qa_search_found") == 0.0
    assert value(got, "qa_evidence_found") == 1.0
    assert got["measures"]["qa_evidence_found"]["splits"] == {"before_25k": {"n": 1, "value": 1.0}}


def test_a_question_answered_with_an_empty_context_is_counted():
    """FR-29 defect 1: the earlier harness sent an empty search context and so tested the model,
    not the app. An empty context must be visible in the report, not silently scored."""
    content, texts = qa_content(100)
    scores = score(results([qa_result([], [], context_chars=0)]), content, texts)
    assert scores["counts"]["qa"]["empty_context"] == 1
    assert value(scores, "qa_evidence_found") == 0.0


def test_overlap_is_containment_or_shared_content_words_and_never_function_words():
    """FR-24: a passage is "cited" when the citation covers it. Containment of a tiny string, or
    sharing only words like "the" and "shall", must not count, or every answer would hit."""
    assert s._overlaps(PASSAGE, "Note: " + PASSAGE.lower() + " (end)")
    assert s._overlaps(
        "Supplier shall indemnify Customer against claims of infringement",
        PASSAGE,
    )
    assert not s._overlaps("the", "the " + PASSAGE)
    assert not s._overlaps(
        "The Customer shall pay the fees within the time stated",
        "The Supplier shall keep the records for the full term",
    )
    assert not s._overlaps("", PASSAGE)


# --- portfolio (FR-9) -------------------------------------------------------------------


def portfolio_content(expected, **parts):
    q = PortfolioQuestion(
        item_id="pq-01",
        question="Which renew automatically?",
        fact="auto_renewal",
        expected_doc_ids=expected,
    )
    return make_content(
        documents=[make_doc(d) for d in [*expected, "pf-09"]], portfolio=[q], **parts
    )


def portfolio_result(named):
    return item(
        "pq-01",
        "portfolio",
        None,
        {"answer": "x", "named_doc_ids": named, "citations": [], "context_chars": 100},
    )


def test_portfolio_reports_found_missed_and_wrongly_included():
    """AC-9: seven right answers, five named correctly and one wrong one named: 5 found, 2
    missed, 1 wrongly included. Completeness without the wrong-name count would reward an AI
    that names every contract."""
    expected = [f"pf-{i:02d}" for i in range(1, 8)]
    content = portfolio_content(expected)
    named = expected[:5] + ["pf-09"]
    scores = score(results([portfolio_result(named)]), content)
    (question,) = scores["portfolio"]["questions"]
    assert (question["found"], question["missed"], question["wrongly_included"]) == (5, 2, 1)
    assert question["missed_doc_ids"] == ["pf-06", "pf-07"]
    assert value(scores, "portfolio_completeness") == pytest.approx(10 / 13)
    assert scores["measures"]["portfolio_completeness"]["n"] == 7


def test_a_sample_run_is_scored_against_its_small_sub_portfolio_only():
    """FR-30: a sample ingests only a few portfolio documents. Scoring it against the full key
    would count every contract that was never loaded as missed."""
    content = portfolio_content(
        ["pf-01", "pf-02", "pf-05"], sample={"portfolio_docs": ["pf-01", "pf-02", "pf-09"]}
    )
    run = results([portfolio_result(["pf-01", "pf-09"])], mode="sample")
    (question,) = score(run, content)["portfolio"]["questions"]
    assert (question["expected"], question["found"], question["missed"]) == (2, 1, 1)
    assert question["wrongly_included"] == 1  # pf-09 is in the sub-portfolio but not the answer


# --- routing (FR-21, FR-25) ---------------------------------------------------------------


def routing_content(fingerprint="fp1", picks=None):
    picks = picks or {"rt-01": "wf-1", "rt-02": "wf-2", "rt-03": None, "rt-04": "wf-3"}
    keys = [
        RoutingKey(
            item_id=i,
            status="unmarked" if w == "?" else "confirmed",
            workflow_id=None if w in (None, "?") else w,
            workflow_fingerprint=fingerprint,
            sheet_sha256="s" * 64,
            loaded_at=NOW,
        )
        for i, w in picks.items()
    ]
    requests = [
        RoutingRequest(
            item_id=i,
            channel="email",
            subject="s",
            body="b",
            drafted_for_workflow_id=wf,
            drafting_model="claude-sonnet-5-5",
        )
        for i, wf in [("rt-01", "wf-1"), ("rt-02", "wf-1"), ("rt-03", "wf-2"), ("rt-04", "wf-2")]
    ]
    snapshot = {
        "fingerprint": "fp1",
        "workflows": [
            {"id": f"wf-{n}", "name": f"Workflow {n}", "enabled": True} for n in (1, 2, 3)
        ],
    }
    return make_content(routing_keys=keys, routing=requests, workflow_snapshot=snapshot)


def routing_result(item_id, workflow_id):
    return item(item_id, "routing", None, {"workflow_id": workflow_id, "needs_human": False})


def test_routing_accuracy_is_the_share_sent_to_the_owners_pick_and_null_matches_null():
    """AC-25: routing is scored against the owner's choice. "No workflow, a person should route
    it" is a real pick, so a null prediction must match a null pick (and only that)."""
    run = results(
        [
            routing_result("rt-01", "wf-1"),  # right
            routing_result("rt-02", "wf-1"),  # wrong
            routing_result("rt-03", None),  # right: the owner picked "no workflow"
            routing_result("rt-04", None),  # wrong
        ]
    )
    scores = score(run, routing_content())
    assert value(scores, "routing_accuracy") == 0.5
    assert scores["measures"]["routing_accuracy"]["n"] == 4


def test_unpicked_requests_are_left_out_and_counted_and_coverage_is_reported():
    """AC-21: a request the owner did not pick is not scored but is counted, and the report says
    how many of today's workflows have a request and how many each has."""
    content = routing_content(picks={"rt-01": "wf-1", "rt-02": "?", "rt-03": "wf-2", "rt-04": "?"})
    scores = score(
        results([routing_result("rt-01", "wf-1"), routing_result("rt-02", "wf-2")]), content
    )
    routing = scores["counts"]["routing"]
    assert scores["measures"]["routing_accuracy"]["n"] == 1
    assert routing["unpicked"] == 2
    assert (routing["workflows_total"], routing["workflows_covered"]) == (3, 2)
    assert routing["requests_per_workflow"]["wf-1"] == {"name": "Workflow 1", "requests": 2}
    assert routing["requests_per_workflow"]["wf-3"]["requests"] == 0


@pytest.mark.parametrize("how", ["the run flagged it", "the workflow list changed since the picks"])
def test_routing_is_not_scored_when_the_workflow_list_changed_since_the_picks(how):
    """AC-21: picks made against an older workflow list no longer apply. Scoring them anyway
    would mark correct routing wrong for workflows that no longer exist."""
    if how == "the run flagged it":
        content, extra = routing_content(), {"excluded": {"routing_out_of_date": True}}
    else:
        content, extra = routing_content(fingerprint="old"), {}
    scores = score(results([routing_result("rt-01", "wf-1")], **extra), content)
    assert value(scores, "routing_accuracy") is None
    assert scores["counts"]["routing"]["out_of_date"] is True


# --- label-free checks, cost and time (FR-10..FR-14) -----------------------------------------


def test_coverage_is_characters_seen_over_total_per_call_and_on_average():
    """AC-10: 25,000 of a 60,000-character document is 42% coverage. A call that sees its whole
    document is 100%. Coverage that is shown only as an average would hide the truncated calls."""
    calls = [
        call(call_id="c1", chars_seen=25_000, chars_total=60_000),
        call(call_id="c2", chars_seen=1_000, chars_total=1_000),
        call("metadata", call_id="c3"),  # no document figures: not part of coverage
    ]
    scores = score(results(calls=calls), make_content())
    per_call = {c["call_id"]: c["coverage"] for c in scores["coverage_per_call"]}
    assert round(per_call["c1"] * 100) == 42
    assert per_call["c2"] == 1.0
    assert value(scores, "coverage_avg") == pytest.approx((25 / 60 + 1) / 2)
    assert scores["measures"]["coverage_avg"]["n"] == 2


def test_truncation_rate_counts_calls_stopped_at_max_tokens_per_call_type():
    """AC-11: a cut-off answer is usually recorded as a failed call, so the denominator must
    include failed calls or the rate would read lower than the truth."""
    calls = [call(stop_reason="max_tokens", status="failed")]
    calls += [call(call_id=f"r{i}") for i in range(3)]
    calls += [call("metadata"), call("metadata", stop_reason="max_tokens")]
    scores = score(results(calls=calls), make_content())
    splits = scores["measures"]["truncation_rate"]["splits"]
    assert splits["playbook_review"] == {"n": 4, "value": 0.25}
    assert splits["metadata"] == {"n": 2, "value": 0.5}
    assert value(scores, "truncation_rate") == pytest.approx(2 / 6)


def test_quote_validity_is_the_word_for_word_share_and_the_bad_quotes_stay_private():
    """AC-12, FR-36: 8 of 10 quotes word for word is 80%, and the 2 that are not are listed, but
    only in the private part: the public numbers must never carry contract text."""
    quotes = [{"text": f"good {i}", "word_for_word": True, "align_score": 100.0} for i in range(8)]
    quotes += [{"text": "SECRET paraphrase one", "word_for_word": False, "align_score": 88.0}]
    quotes += [{"text": "SECRET paraphrase two", "word_for_word": False, "align_score": 60.0}]
    dropped = [{"text": "from a failed call", "word_for_word": False, "align_score": 0.0}]
    calls = [call(quotes=quotes), call(status="failed", quotes=dropped)]
    scores = score(results(calls=calls), make_content())
    assert value(scores, "quote_validity") == 0.8
    assert scores["measures"]["quote_validity"]["n"] == 10  # a failed call's quotes are no answer
    assert value(scores, "quote_alignment_85") == 0.9
    assert {q["text"] for q in scores["private"]["invalid_quotes"]} == {
        "SECRET paraphrase one",
        "SECRET paraphrase two",
    }
    public = {k: v for k, v in scores.items() if k != "private"}
    assert "SECRET" not in json.dumps(public)


def test_cost_tokens_and_time_per_call_type_add_up_to_the_totals():
    """AC-14, FR-33: totals are the sum of the parts, a batched call's list price is double what
    was paid, and the actual cost sits beside the estimate per call type."""
    calls = [
        call(
            "metadata", prompt_tokens=100, completion_tokens=10, cost_usd=0.1234567, latency_ms=900
        ),
        call("metadata", prompt_tokens=50, cache_read_tokens=5, cost_usd=0.2, latency_ms=1100),
        call(
            "playbook_review",
            batched=True,
            prompt_tokens=300,
            completion_tokens=30,
            cache_write_tokens=7,
            cost_usd=0.05,
        ),
    ]
    run = results(
        calls=calls,
        estimate={"usd": 1.0, "by_call_type": {"metadata": {"calls": 2, "usd": 0.4}}},
        batches=[
            {"phase": 1, "submitted_at": "2026-10-06T10:00:00Z", "ended_at": "2026-10-06T11:30:00Z"}
        ],
    )
    scores = score(run, make_content())
    parts = scores["by_call_type"]
    assert scores["totals"]["cost_usd"] == round(sum(p["cost_usd"] for p in parts.values()), 6)
    assert scores["totals"]["cost_usd"] == pytest.approx(0.3734567, abs=1e-6)
    assert scores["totals"]["list_cost_usd"] == pytest.approx(0.4234567, abs=1e-6)
    for field in ("calls", "prompt_tokens", "completion_tokens", "total_tokens"):
        assert scores["totals"][field] == sum(p[field] for p in parts.values())
    assert scores["totals"]["total_tokens"] == 100 + 10 + 50 + 5 + 300 + 30 + 7
    assert scores["totals"]["latency_ms_avg"] == 1000  # the batched call has no latency
    assert scores["time"]["batch_turnaround_s"] == {"1": 5400.0}
    assert scores["cost_vs_estimate"]["total"] == {"estimate_usd": 1.0, "actual_usd": 0.373457}
    assert scores["cost_vs_estimate"]["by_call_type"]["metadata"]["estimate_usd"] == 0.4
    assert scores["cost_vs_estimate"]["by_call_type"]["playbook_review"]["estimate_usd"] is None


def test_every_measure_the_baseline_report_needs_is_present_with_n_and_an_explanation():
    """AC-26, FR-26: the report lists every headline measure with its item count and a plain
    one-line explanation; a measure that silently went missing would make a baseline that can
    never show it."""
    docs = [make_doc("d1"), make_doc("pf-01"), *owner_docs()]
    content = make_content(
        documents=docs,
        review=[review_item("d1", [plant("p0")])],
        extraction=[
            ExtractionItem(
                item_id="ex-d1",
                doc_id="d1",
                category=SourceCategory.CONSTRUCTED,
                source="constructed",
                fields=[key("governing_law", "jurisdiction", "india")],
            )
        ],
        qa=[
            QAItem(
                item_id="qa-001",
                doc_id="d1",
                category=SourceCategory.PUBLIC,
                source="s",
                question="q",
                answer="a",
                passages=[PASSAGE],
            )
        ],
        portfolio=[
            PortfolioQuestion(item_id="pq-01", question="q", fact="f", expected_doc_ids=["pf-01"])
        ],
        routing_keys=routing_content().routing_keys,
    )
    run = results(
        [
            review_result("d1", [flag("p0")]),
            extraction_result("d1", {"governing_law": "India"}),
            qa_result([PASSAGE], [PASSAGE]),
            portfolio_result(["pf-01"]),
            routing_result("rt-01", "wf-1"),
        ],
        calls=[
            call(
                chars_seen=1,
                chars_total=2,
                quotes=[{"text": "t", "word_for_word": True, "align_score": 100.0}],
            )
        ],
    )
    scores = score(run, content)
    required = [
        "review_f1",
        "extraction_accuracy",
        "qa_evidence_found",
        "portfolio_completeness",
        "routing_accuracy",
        "quote_validity",
        "truncation_rate",
        "coverage_avg",
        "cost_usd",
    ]
    for k in required:
        m = scores["measures"][k]
        assert m["n"] >= 1 and m["value"] is not None, k
        assert m["explanation"].strip() and m["label"].strip(), k
    json.dumps(scores)  # the whole scores object is plain JSON: the report writes it as is
    assert set(scores["measures"]) <= set(s.SPECS)
    assert all(
        m["role"] in (s.HEADLINE, s.REPORT_ONLY, s.COST_TIME) for m in scores["measures"].values()
    )


# --- locating key text and importing ------------------------------------------------------


def test_positions_find_exact_reflowed_and_slightly_changed_text_and_give_up_otherwise():
    """FR-23: a key quote is placed in the document to decide before or past 25k. A line break
    inside the quote must not hide it, and a quote that is not there must give no position
    rather than a guess."""
    sentence = "Either party may terminate this Agreement on thirty days written notice."
    text = "Intro. " + sentence.replace("terminate this", "terminate\n  this") + " Outro."
    pos = s._Positions({"d": text, "e": "Unrelated words only."})
    assert pos.start("d", sentence) == 7
    assert 0 <= pos.start("d", sentence.replace("thirty", "thirty (30)")) < 20  # fuzzy, >= 90
    assert pos.start("e", sentence) is None
    assert pos.start("missing", sentence) is None
    assert pos.start("d", "   ") is None
    assert s._bucket(24_999) == "before_25k"
    assert s._bucket(25_000) == "past_25k"
    assert s._bucket(None) == "unplaced"


def test_load_texts_reads_public_and_constructed_plain_text_and_never_owner_files(
    tmp_path, monkeypatch
):
    """FR-36: placing quotes needs fixture text, but real client text must never be read for it.
    PDFs (scan twins) are not text, so they are simply absent."""
    for name in ("pub.txt", "owner.txt", "scan.pdf"):
        (tmp_path / name).write_text(f"text of {name}", encoding="utf-8")
    monkeypatch.setattr(s, "DOCS_DIR", tmp_path)
    content = make_content(
        documents=[
            make_doc("pub", SourceCategory.PUBLIC, file="pub.txt"),
            make_doc("own", SourceCategory.OWNER, file="owner.txt"),
            make_doc("scan", file="scan.pdf", mime_type="application/pdf"),
            make_doc("gone", file="not-there.txt"),
        ]
    )
    assert s.load_texts(content) == {"pub": "text of pub.txt"}


def test_scorer_imports_without_the_database_or_the_app_settings():
    """The exam must repoint the app at its throwaway database before the database module is
    imported (FR-38). A scorer that dragged it in would break `compare` run after `isolate()`."""
    code = (
        "import sys\n"
        "import eval.exam.score\n"
        "bad = sorted(m for m in sys.modules if m in ('app.core', 'app.core.database',"
        " 'app.core.config') or m.split('.')[0] == 'sqlalchemy')\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
