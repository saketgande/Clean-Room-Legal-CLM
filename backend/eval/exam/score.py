"""Scoring for the AI exam: pure functions over a finished run's results and the exam content.

specs/005-ai-eval-baseline T011 (FR-9..FR-13, FR-22..FR-28, FR-41). No database, no network, no
model: ``score_run`` turns one ``results.json`` into a scores object, ``score_pair`` puts two runs
of the same code side by side (noise band = how far the two runs differ), and ``verdict`` says
whether a change beat the baseline. Comparisons re-score the stored predictions with this scorer
every time, so a scorer fix can never make a change look better than its baseline.

Scores object (``score_run``). Everything outside ``private`` holds only numbers, ids and
rule/field names, so the public report can be built from it::

    measures   {key: {key, label, explanation, direction, role, n, value, splits}}
               ``value`` is None when ``n`` is 0 (not measured). ``splits`` is {name: {n, value}}
               by source category, before/past 25k, call type, and so on.
    missed_high_critical / false_alarms / failed_items / failed_calls   lists of ids
    counts, fields, portfolio, routing, by_call_type, totals, time, cost_vs_estimate
    private    {invalid_quotes: quotes quoted from real text, signatures: normalised answers,
                failure_reasons: the runner's full failure text per item id}
    failed_items carry a short ``reason_code`` only; ``run.content_current`` is False when the run
    answered other content than the ``content`` it was scored with.

Roles: ``headline_quality`` measures decide pass or fail, ``report_only`` and ``cost_time`` never
do (D-10, D-12). Failed and not-attempted items are listed and never scored (FR-40).
"""

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from math import fsum
from statistics import fmean

from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta

from app.ai.citations import align_citation_to_source
from app.contract_brain.entities import normalize_jurisdiction, normalize_org_name
from eval.exam.content import (
    CUTOFF,
    DOCS_DIR,
    ExamContent,
    ExamDocument,
    SourceCategory,
    Task,
    content_fingerprint,
)

SCHEMA = "aegis-exam-scores/1"
PAIR_SCHEMA = "aegis-exam-pair/1"

HEADLINE = "headline_quality"
REPORT_ONLY = "report_only"
COST_TIME = "cost_time"

# The area a change targets -> the one measure that must improve (FR-28, A-1).
MAIN_MEASURE = {
    "review": "review_f1",
    "extraction": "extraction_accuracy",
    "qa": "qa_evidence_found",
    "routing": "routing_accuracy",
    "portfolio": "portfolio_completeness",
}

# A quote is counted "same as the source" at this similarity too: the app's own citation check
# (FR-12 asks for word for word; both numbers are reported).
ALIGN_THRESHOLD = 85.0
# A key passage is located in a document by this alignment score or better.
LOCATE_THRESHOLD = 90.0


class ComparisonRefused(ValueError):
    """Two things that must not be compared were handed to ``score_pair`` / ``verdict``."""


def _spec(label: str, explanation: str, *, role: str = REPORT_ONLY, direction: str = "higher"):
    return {"label": label, "explanation": explanation, "direction": direction, "role": role}


# Order here is the order measures appear in every report.
SPECS: dict[str, dict] = {
    "review_f1": _spec(
        "Review F1",
        "How well the AI finds the real problems in reviewed contracts without false alarms "
        "(1.0 is perfect); a flag on an unchanged control document is a false alarm.",
        role=HEADLINE,
    ),
    "review_precision": _spec(
        "Review precision", "Of the problems the AI flagged, the share that were real."
    ),
    "review_recall": _spec(
        "Review recall",
        "Of the real problems, the share the AI found; the splits show findings before and "
        "past the first 25,000 characters.",
    ),
    "review_f1_scan_twins": _spec(
        "Review F1, scanned copies",
        "Review F1 on scanned copies next to the same contracts as plain text, so the effect "
        "of the scan path is visible; not part of the main review figure.",
    ),
    "nda_verdict_accuracy": _spec(
        "NDA verdict accuracy",
        "For NDAs, how often the AI's verdict on a statement (supported, contradicted, not "
        "mentioned) matches the lawyer label.",
    ),
    "extraction_accuracy": _spec(
        "Field extraction accuracy",
        "Average, over fields, of the share of facts (parties, dates, governing law, ...) "
        "extracted correctly after normalising formats; the splits show facts before and past "
        "the first 25,000 characters.",
        role=HEADLINE,
    ),
    "extraction_accuracy_asread": _spec(
        "Extraction, stamp-only scans as read today",
        "The 2 stamp-only scans scored as the app reads them today (no OCR), on their own "
        "line and never mixed into the main figure.",
    ),
    "extraction_accuracy_scan_twins": _spec(
        "Extraction accuracy, scanned copies",
        "Field extraction on scanned copies next to the same contracts as plain text; not part "
        "of the main extraction figure.",
    ),
    "qa_evidence_found": _spec(
        "Question answering: evidence found",
        "How often the final answer cites the passage that holds the correct answer, using "
        "the context the app's own search returned.",
        role=HEADLINE,
    ),
    "qa_search_found": _spec(
        "Question answering: search found the passage",
        "How often the app's own search put the correct passage in front of the AI.",
    ),
    "portfolio_completeness": _spec(
        "Portfolio completeness",
        "For questions across the whole portfolio, how closely the contracts the AI named "
        "match the exact correct list (found, missed and wrongly included, as one F1).",
        role=HEADLINE,
    ),
    "routing_accuracy": _spec(
        "Routing accuracy",
        "Share of free-text requests sent to the workflow the owner picked, among today's "
        "workflows.",
        role=HEADLINE,
    ),
    "quote_validity": _spec(
        "Quote validity",
        "Share of quotes the AI returned that appear word for word in the source text "
        "(spacing and line breaks ignored).",
        role=HEADLINE,
    ),
    "quote_alignment_85": _spec(
        "Quotes matching at 85%",
        "Share of quotes that match the source at 85% similarity or better, the app's own "
        "citation check.",
    ),
    "truncation_rate": _spec(
        "Truncation rate",
        "Share of AI calls whose answer was cut off before it finished (lower is better).",
        role=HEADLINE,
        direction="lower",
    ),
    "coverage_avg": _spec(
        "Document coverage",
        "Average share of each document the AI was actually given (characters seen out of "
        "total) on review, extraction and labelling calls.",
    ),
    "consistency": _spec(
        "Run-to-run consistency", "Share of items that received the same answer in both runs."
    ),
    "cost_usd": _spec(
        "Cost (USD)",
        "What the run's AI calls cost at the price actually paid.",
        role=COST_TIME,
        direction="lower",
    ),
    "list_cost_usd": _spec(
        "Cost at list price (USD)",
        "The same calls priced without the batch discount.",
        role=COST_TIME,
        direction="lower",
    ),
    "total_tokens": _spec(
        "Tokens",
        "Input, output and cache tokens across all calls.",
        role=COST_TIME,
        direction="lower",
    ),
    "wall_time_s": _spec(
        "Run time (s)",
        "Wall-clock seconds from start to finish.",
        role=COST_TIME,
        direction="lower",
    ),
    "latency_ms_avg": _spec(
        "Latency (ms)",
        "Average time of a normal (non-batch) call.",
        role=COST_TIME,
        direction="lower",
    ),
    "batch_turnaround_s": _spec(
        "Batch turnaround (s)",
        "Seconds batches took from submission to the provider finishing them.",
        role=COST_TIME,
        direction="lower",
    ),
}


# --- small shared helpers ---------------------------------------------------------------


def _measure(key: str, value, n: int, splits: Mapping[str, Mapping] | None = None) -> dict:
    return {
        "key": key,
        **SPECS[key],
        "n": n,
        "value": value if n else None,
        "splits": {name: dict(s) for name, s in (splits or {}).items() if s["n"]},
    }


def _share(num: int, den: int) -> dict:
    return {"n": den, "value": num / den if den else None}


def _squash(value) -> str:
    """Lower-case, one space between words, outer quotes and full stops off."""
    return re.sub(r"\s+", " ", str(value)).strip().strip("\"'“”.").lower()


_NOT_STATED = {
    "",
    "not stated",
    "not stated in the document",
    "not specified",
    "none",
    "n/a",
    "unknown",
    "not found",
    "null",
}


def _empty(value) -> bool:
    """No answer: None, blank, a "not stated" phrase, or a container of nothing but those."""
    if value is None:
        return True
    if isinstance(value, Mapping):
        return all(_empty(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return all(_empty(v) for v in value)
    return _squash(value) in _NOT_STATED


def _bucket(start: int | None) -> str:
    if start is None:
        return "unplaced"
    return "past_25k" if start >= CUTOFF else "before_25k"


def _digest(text: str) -> str:
    return hashlib.sha1(_squash(text).encode("utf-8")).hexdigest()[:12]


# --- locating a key's supporting text in its document (the past-25k split) ---------------


def load_texts(content: ExamContent) -> dict[str, str]:
    """Plain-text fixtures of public and constructed documents, by doc_id.

    Owner documents are never read here: their keys carry their own offsets, and real contract
    text must not be loaded just to place a quote (FR-36). A document that is not plain text
    (a PDF scan twin) is simply absent; its twin shares its original's answer record.
    """
    texts: dict[str, str] = {}
    for doc in content.documents:
        if doc.category is SourceCategory.OWNER or doc.mime_type != "text/plain":
            continue
        path = DOCS_DIR / doc.file
        if path.is_file():
            texts[doc.doc_id] = path.read_text(encoding="utf-8", errors="replace")
    return texts


class _Positions:
    """Where a quote starts in its document, or None when it cannot be placed.

    ponytail: located by search in the fixture text, not in the run's snapshot. Ingestion can
    shift whitespace by a few characters, which only matters for a quote within a few characters
    of the 25,000 mark. Upgrade path: have the runner record each key's snapshot offset.
    """

    def __init__(self, texts: Mapping[str, str]):
        self._texts = texts
        self._cache: dict[tuple[str, str], int | None] = {}

    def start(self, doc_id: str, quote: str | None) -> int | None:
        text = self._texts.get(doc_id)
        if not quote or not quote.strip() or not text:
            return None
        key = (doc_id, quote)
        if key not in self._cache:
            self._cache[key] = self._find(quote, text)
        return self._cache[key]

    @staticmethod
    def _find(quote: str, text: str) -> int | None:
        at = text.find(quote)
        if at >= 0:
            return at
        flexible = re.search(r"\s+".join(map(re.escape, quote.split())), text, re.IGNORECASE)
        if flexible:
            return flexible.start()
        span, score = align_citation_to_source(quote, text)
        if score >= LOCATE_THRESHOLD:
            at = text.find(span)
            return at if at >= 0 else None
        return None


# --- value normalisation (FR-23): formatting differences are not errors -------------------

# Matched with dots and spaces removed, so "N.A.", "S.p.A." and "Pvt. Ltd." are forms too.
# ponytail: a form with more in it ("(India) Pvt Ltd") and a name with " and " in it ("Johnson
# and Johnson") still split; add forms here as real answers show them.
_FORM_ONLY = re.compile(
    r"^(inc|incorporated|ltd|limited|llc|llp|lp|plc|corp|corporation|co|company|pvt|pvtltd|"
    r"pte|pteltd|pty|ptyltd|gmbh|ag|kg|se|sa|spa|sas|na|nationalassociation|bv|nv|sdnbhd|bhd)$",
    re.IGNORECASE,
)


def _split_parties(value) -> list[str]:
    """A list stays as given; a single string ("A, B and C") is split into its parties.

    A trailing company form after a comma ("Acme, Inc.", "Credit One Bank, N.A.") belongs to the
    name before it. "&" never splits: it sits inside names ("Larsen & Toubro Infotech Limited")
    far more often than between two parties, and splitting it scored right answers wrong.
    """
    if value is None:
        return []
    if not isinstance(value, str):
        return [str(v) for v in value if str(v).strip()]
    names: list[str] = []
    for chunk in re.split(r"[;|\n]|\s+and\s+", value):
        for piece in (p.strip() for p in chunk.split(",")):
            if not piece:
                continue
            if names and _FORM_ONLY.match(re.sub(r"[.\s]", "", piece)):
                names[-1] += f", {piece}"
            else:
                names.append(piece)
    return names


def _party_key(name: str) -> str:
    """One comparison key per organisation: legal-form spelling, punctuation, a leading "the" and
    a role tag in brackets ("(the Supplier)") do not make a different party."""
    cleaned = re.sub(r"\([^)]*\)", " ", name)
    cleaned = re.sub(r"^\s*the\s+", "", cleaned, flags=re.IGNORECASE)
    return normalize_org_name(cleaned) or _squash(cleaned)


def _as_date(value) -> date | None:
    """A complete calendar date, or None. A year is required and a missing day or month makes
    it incomplete, so "12 months" or "April 2024" never parse into a made-up date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not re.search(r"\b(?:19|20)\d{2}\b", text):
        return None
    try:
        first = dateparser.parse(text, fuzzy=True, default=datetime(2001, 1, 1, tzinfo=UTC))
        second = dateparser.parse(text, fuzzy=True, default=datetime(2002, 2, 2, tzinfo=UTC))
    except (ValueError, OverflowError):
        return None
    return first.date() if first == second else None


def _same_date(a, b) -> bool:
    da, db = _as_date(a), _as_date(b)
    return da == db if da and db else _squash(a) == _squash(b)


_ONES = {
    word: n
    for n, word in enumerate(
        ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
        + ["eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen"]
        + ["eighteen", "nineteen"],
        1,
    )
}
_TENS = {
    word: n * 10
    for n, word in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], 2
    )
}
# "thirty-six" and "thirty six" are one number: a compound is matched whole so it can never
# resolve to its last word ("six").
_TENS_RE, _ONES_RE = "|".join(_TENS), "|".join(list(_ONES)[:9])
_NUMBER = rf"(?:(?:{_TENS_RE})(?:[-\s](?:{_ONES_RE}))?|{'|'.join(_ONES)})"
_UNIT = r"(year|month|week|day)s?\b"
# A contract writes "thirty-six (36) months"; the digits in brackets are the figure to trust.
_BRACKETED_TERM = re.compile(r"\((\d+)\)\s*" + _UNIT, re.IGNORECASE)
_TERM = re.compile(r"\b(\d+|" + _NUMBER + r")\s*" + _UNIT, re.IGNORECASE)


def _number(text: str) -> int:
    if text.isdigit():
        return int(text)
    return sum({**_ONES, **_TENS}[word] for word in re.split(r"[-\s]", text.lower()))


def _duration(text: str) -> relativedelta | None:
    found = _BRACKETED_TERM.search(text) or _TERM.search(text)
    if not found:
        return None
    return relativedelta(**{found[2].lower() + "s": _number(found[1])})


def _same_end(want, got, effective) -> bool:
    """A date key against the AI's end date; a duration key ("3 years") against start + term.

    The start is the key's own effective date when the key has one, else the AI's. One day of
    slack: "3 years from 1 April 2024" ends on 31 March 2027 as often as on 1 April.
    """
    term = _duration(str(want))
    if term is None:
        return _same_date(want, got)
    end, start = _as_date(got), _as_date(effective)
    if end and start:
        return abs((start + term - end).days) <= 1
    return _duration(str(got)) == term


_CURRENCY_TOKENS = {
    "inr": "INR",
    "rs": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "₹": "INR",
    "usd": "USD",
    "$": "USD",
    "eur": "EUR",
    "€": "EUR",
    "gbp": "GBP",
    "£": "GBP",
}
_SCALES = {
    "lakh": 10**5,
    "lakhs": 10**5,
    "lac": 10**5,
    "lacs": 10**5,
    "crore": 10**7,
    "crores": 10**7,
    "cr": 10**7,
    "thousand": 10**3,
    "million": 10**6,
    "mn": 10**6,
    "billion": 10**9,
    "bn": 10**9,
}


def _currency(text) -> str | None:
    found = re.search(
        r"₹|\$|€|£|\b(?:inr|rs|rupees?|usd|eur|gbp)\b", str(text or ""), re.IGNORECASE
    )
    return _CURRENCY_TOKENS[found[0].lower()] if found else None


def _amount(value) -> tuple[Decimal, str | None] | None:
    """(amount, currency or None) from "INR 2,500,000.00", "₹25,00,000", "Rs. 25 lakh",
    "2 crore", "USD 5,000" or an {amount, currency} object."""
    currency = None
    if isinstance(value, Mapping):
        currency = _currency(value.get("currency"))
        value = value.get("amount")
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value)), currency
    text = str(value or "")
    found = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([a-z]+)?", text, re.IGNORECASE)
    if not found:
        return None
    try:
        number = Decimal(found[1].replace(",", ""))
    except InvalidOperation:
        return None
    number *= _SCALES.get((found[2] or "").lower(), 1)
    return number, currency or _currency(text)


def _same_amount(want, got) -> bool:
    a, b = _amount(want), _amount(got)
    return bool(a and b and a[0] == b[0] and (a[1] == b[1] or None in (a[1], b[1])))


# Containment of a tiny string proves nothing; and counting words like "shall" and "the" let any
# clause "overlap" any passage, so only content words (4+ letters) count towards the 60%.
_MIN_CONTAINED_CHARS = 8
_CONTENT_WORD = re.compile(r"[a-z0-9]{4,}")


def _overlaps(a: str, b: str) -> bool:
    """Do two passages cover the same text? Containment, or 60% of the shorter one's content
    words in the other (the earlier harness's overlap rule, minus function words)."""
    a, b = _squash(a), _squash(b)
    if not a or not b:
        return False
    if min(len(a), len(b)) >= _MIN_CONTAINED_CHARS and (a in b or b in a):
        return True
    words_a, words_b = set(_CONTENT_WORD.findall(a)), set(_CONTENT_WORD.findall(b))
    shorter = min(words_a, words_b, key=len)
    return len(shorter) >= 3 and len(words_a & words_b) / len(shorter) >= 0.6


def _texts(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [str(v) for v in value.values() if not _empty(v)]
    return [str(v) for v in value if not _empty(v)]


# --- extraction ------------------------------------------------------------------------

_KIND_OF_FIELD = {
    "parties": "parties",
    "effective_date": "date",
    "expiration_date": "date",
    "end_date_or_term": "date_or_term",
    "governing_law": "jurisdiction",
    "value": "amount",
}  # every other field is a passage the app finds as a clause


# Clause fields the runner answers with every app clause of a broad kind (runner
# _CLAUSE_FIELDS: term_and_termination, notices), so nearly every contract "answers" them and a
# "not stated" key could never be passed: such keys are counted, not scored (spec D-16).
# liability_cap is not here: its only kind is limitation_of_liability, so it stays scored.
# ponytail: an invented renewal or notice clause goes uncaught. Upgrade path: the runner keeps
# clause kinds per field and only a field's own kind counts (renewal for renewal_term);
# termination_for_convenience has no kind of its own, so it stays unscored until the app has one.
_BROAD_CLAUSE_FIELDS = frozenset({"renewal_term", "notice_period", "termination_for_convenience"})


class _Truth:
    """One answer-key fact, whichever key it came from (public, constructed or the owner's)."""

    def __init__(self, field: str, kind: str, value, quote: str | None, start: int | None):
        self.field, self.kind, self.value, self.quote, self.start = field, kind, value, quote, start


def _predicted(field: str, pred: Mapping):
    """What the app extracted for a field. The end of a contract is one fact that arrives as
    ``expiration_date`` or ``end_date_or_term``; either answers either key."""
    if field in ("end_date_or_term", "expiration_date"):
        return next(
            (
                v
                for v in (pred.get("end_date_or_term"), pred.get("expiration_date"))
                if not _empty(v)
            ),
            None,
        )
    return pred.get(field)


def _check(truth: _Truth, pred: Mapping, key_effective) -> tuple[list[bool], int]:
    """One bool per scored unit (a key party is a unit of its own) and the extra-party count."""
    got = _predicted(truth.field, pred)
    if truth.kind == "parties" and not _empty(truth.value):
        wanted = list(dict.fromkeys(_party_key(n) for n in _split_parties(truth.value)))
        have = {_party_key(n) for n in _split_parties(got)}
        # Parties the key does not list are reported as "extra", not counted against accuracy.
        return [k in have for k in wanted], len(have - set(wanted))
    if _empty(truth.value):
        return [_empty(got)], 0  # the document does not state it: only "not stated" is right
    if _empty(got):
        return [False], 0
    if truth.kind == "date":
        ok = _same_date(truth.value, got)
    elif truth.kind == "date_or_term":
        ok = _same_end(truth.value, got, key_effective or pred.get("effective_date"))
    elif truth.kind == "jurisdiction":
        ok = _jurisdiction(truth.value) == _jurisdiction(got)
    elif truth.kind == "amount":
        ok = _same_amount(truth.value, got)
    else:
        passage = truth.quote or str(truth.value)
        ok = any(_overlaps(clause, passage) for clause in _texts(got))
    return [ok], 0


def _jurisdiction(value) -> str:
    """The app's own jurisdiction key, after dropping the "laws of" wrapper ("the laws of
    India" and "India" name the same place)."""
    text = re.sub(r"^\s*(?:the\s+)?laws?\s+of\s+(?:the\s+)?", "", str(value), flags=re.IGNORECASE)
    return normalize_jurisdiction(text) or _squash(text)


def _normalised(truth: _Truth, pred: Mapping):
    """The AI's answer in normal form, for run-to-run consistency (compared, never published)."""
    got = _predicted(truth.field, pred)
    if _empty(got):
        return None
    if truth.kind == "parties":
        return sorted({_party_key(n) for n in _split_parties(got)})
    if truth.kind in ("date", "date_or_term"):
        return str(_as_date(got) or _duration(str(got)) or _squash(got))
    if truth.kind == "jurisdiction":
        return _jurisdiction(got)
    if truth.kind == "amount":
        amount = _amount(got)
        # Trailing zeros dropped: "2,500,000.00" and "2,500,000" are the same answer, and the
        # Decimal's own text would call them different (consistency compares these signatures).
        return [format(amount[0].normalize(), "f"), amount[1]] if amount else _squash(got)
    return _digest(" ".join(_texts(got)))


def _owner_truths(content: ExamContent, results: Mapping) -> tuple[dict, Counter]:
    """Owner keys that may score: marked by the owner, tied to the file and frozen text the
    contract has now (FR-20), and not listed as out of date by the run."""
    manifest = {e.doc_id: e for e in content.owner_manifest}
    listed = set((results.get("excluded") or {}).get("owner_keys_out_of_date") or [])
    truths: dict[str, list[_Truth]] = defaultdict(list)
    skipped: Counter = Counter()
    for key in content.owner_keys:
        if key.status == "unmarked":
            skipped["unmarked"] += 1
            continue
        entry = manifest.get(key.doc_id)
        changed = entry and (
            key.file_sha256 != entry.file_sha256
            or key.frozen_text_sha256 != entry.frozen_text_sha256
        )
        if changed or f"{key.doc_id}:{key.field}" in listed:
            skipped["out_of_date"] += 1
            continue
        kind = _KIND_OF_FIELD.get(key.field, "passage")
        truths[key.doc_id].append(_Truth(key.field, kind, key.value, key.quote, key.char_start))
    return truths, skipped


def _macro(rows: list[dict]) -> dict:
    """Mean of the per-field accuracies, so a field with many facts does not drown the rest."""
    by_field: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        by_field[row["field"]].append(row["correct"])
    value = fmean(sum(v) / len(v) for v in by_field.values()) if by_field else None
    return {"n": len(rows), "value": value}


def _extraction(items, content, docs, pos, results) -> dict:
    owner, skipped = _owner_truths(content, results)
    by_item = {i.item_id: i for i in content.extraction}
    by_doc = {i.doc_id: i for i in content.extraction}
    rows: list[dict] = []
    signatures: dict[str, dict] = {}
    extra_parties = unkeyed = 0
    not_stated_passages: Counter = Counter()
    for item in items:
        doc = docs.get(item["doc_id"])
        base = (doc.as_read_today_of or doc.twin_of if doc else None) or item["doc_id"]
        category = "owner"
        if base in owner:
            truths = owner[base]
        else:
            key = by_item.get(item["item_id"]) or by_doc.get(base)
            if key is None:
                unkeyed += 1
                continue
            category = key.category.value
            truths = [_Truth(f.field, f.kind, f.value, f.quote, None) for f in key.fields]
        role = (
            "asread" if doc and doc.as_read_today_of else "twin" if doc and doc.twin_of else "main"
        )
        pred = (item.get("prediction") or {}).get("fields") or {}
        effective = next((t.value for t in truths if t.field == "effective_date"), None)
        for truth in truths:
            if truth.field in _BROAD_CLAUSE_FIELDS and _empty(truth.value):
                if role == "main":
                    not_stated_passages[truth.field] += 1
                continue
            start = truth.start if truth.start is not None else pos.start(base, truth.quote)
            oks, extra = _check(truth, pred, effective)
            extra_parties += extra
            rows += [
                {
                    "doc_id": item["doc_id"],
                    "field": truth.field,
                    "correct": ok,
                    "role": role,
                    "category": category,
                    "bucket": _bucket(start),
                }
                for ok in oks
            ]
        signatures[item["item_id"]] = {t.field: _normalised(t, pred) for t in truths}
    main = [r for r in rows if r["role"] == "main"]
    twins = {docs[r["doc_id"]].twin_of for r in rows if r["role"] == "twin"}
    split = {
        c: _macro([r for r in main if r["category"] == c]) for c in _categories(main, "category")
    }
    for bucket in ("before_25k", "past_25k"):
        split[bucket] = _macro([r for r in main if r["bucket"] == bucket])
    fields = {}
    for name in dict.fromkeys(r["field"] for r in main):
        mine = [r for r in main if r["field"] == name]
        fields[name] = {
            "n": len(mine),
            "accuracy": sum(r["correct"] for r in mine) / len(mine),
            **{
                b: _share(
                    sum(r["correct"] for r in mine if r["bucket"] == b),
                    sum(r["bucket"] == b for r in mine),
                )
                for b in ("before_25k", "past_25k")
            },
        }
    overall = _macro(main)
    asread = _macro([r for r in rows if r["role"] == "asread"])
    scanned = _macro([r for r in rows if r["role"] == "twin"])
    original = _macro([r for r in main if r["doc_id"] in twins])
    return {
        "measures": {
            "extraction_accuracy": _measure(
                "extraction_accuracy", overall["value"], overall["n"], split
            ),
            "extraction_accuracy_asread": _measure(
                "extraction_accuracy_asread", asread["value"], asread["n"]
            ),
            "extraction_accuracy_scan_twins": _measure(
                "extraction_accuracy_scan_twins",
                scanned["value"],
                scanned["n"],
                {"text_original": original, "scanned": scanned},
            ),
        },
        "fields": fields,
        "signatures": signatures,
        "counts": {
            "items": dict(Counter(r["category"] for r in main if r["field"] == "parties")),
            "units_by_bucket": dict(Counter(r["bucket"] for r in main)),
            "extra_parties": extra_parties,
            "unkeyed": unkeyed,
            "owner_keys_skipped": dict(skipped),
            "not_stated_passages": dict(not_stated_passages),
        },
    }


def _categories(rows: list[dict], field: str) -> list[str]:
    return [c.value for c in SourceCategory if any(r[field] == c.value for r in rows)]


# --- contract review -------------------------------------------------------------------


def _review_events(key, flags, bucket_of) -> list[tuple]:
    """(kind, rule, severity, bucket) per scored unit: tp / fn per expected finding, fp per false
    alarm. A flag the runner could not attribute to a rule is a false alarm of its own."""
    flagged: dict[str, dict] = {}
    loose = 0
    for flag in flags:
        rule = flag.get("rule")
        if rule is None or flag.get("attributed_by") == "none":
            loose += 1
        else:
            flagged.setdefault(rule, flag)
    # An unchanged control document has nothing to find: everything flagged on it is a false alarm.
    expected = {} if key.control else {r.rule: r for r in key.rules if r.status != "compliant"}
    events = []
    for rule, rule_key in expected.items():
        severity = rule_key.planted.severity if rule_key.planted else rule_key.severity
        events.append(("tp" if rule in flagged else "fn", rule, severity, bucket_of(rule_key)))
    events += [("fp", rule, None, None) for rule in flagged if rule not in expected]
    events += [("fp", None, None, None)] * loose
    return events


def _prf(c: Counter) -> tuple:
    tp, fp, fn = c["tp"], c["fp"], c["fn"]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
    return precision, recall, f1


def _review(items, content, docs, pos) -> dict:
    by_item = {r.item_id: r for r in content.review}
    by_doc = {r.doc_id: r for r in content.review}
    total: Counter = Counter()
    by_category: dict[str, Counter] = defaultdict(Counter)
    by_bucket: dict[str, Counter] = defaultdict(Counter)
    per_doc: dict[str, Counter] = {}
    missed, alarms, signatures = [], [], {}
    nda_right = nda_n = unkeyed = high_critical = 0
    review_docs: Counter = Counter()
    for item in items:
        doc = docs.get(item["doc_id"])
        original = doc.twin_of if doc else None
        key = by_item.get(item["item_id"]) or by_doc.get(original or item["doc_id"])
        if key is None:
            unkeyed += 1
            continue
        text_doc = original or item["doc_id"]
        flags = (item.get("prediction") or {}).get("flags") or []
        events = _review_events(
            key,
            flags,
            lambda rk, d=text_doc: _bucket(
                rk.planted.char_start if rk.planted else pos.start(d, rk.evidence_quote)
            ),
        )
        per_doc[item["doc_id"]] = Counter(e[0] for e in events)
        if original:
            continue  # a scanned copy is a side line, never part of the headline (FR-8)
        review_docs[key.category.value] += 1
        for kind, rule, severity, bucket in events:
            total[kind] += 1
            by_category[key.category.value][kind] += 1
            if kind != "fp":
                by_bucket[bucket][kind] += 1
            if kind != "fp" and severity in ("high", "critical"):
                high_critical += 1
                if kind == "fn":
                    missed.append(
                        {"item_id": item["item_id"], "rule": rule, "severity": severity}
                    )
            if kind == "fp":
                alarms.append({"item_id": item["item_id"], "rule": rule})
        flagged = sorted({e[1] for e in events if e[0] != "fn" and e[1]})
        signatures[item["item_id"]] = {
            "flagged": flagged,
            "unattributed": sum(e[0] == "fp" and e[1] is None for e in events),
        }
        if key.playbook == "nda_checklist":
            said = {f["rule"]: f.get("status", "deviation") for f in flags if f.get("rule")}
            nda_right += sum(said.get(r.rule, "compliant") == r.status for r in key.rules)
            nda_n += len(key.rules)

    precision, recall, f1 = _prf(total)

    def split(which: int, names: Mapping[str, Counter], size) -> dict:
        return {name: {"n": size(c), "value": _prf(c)[which]} for name, c in names.items()}

    def f1_size(c):
        return c["tp"] + c["fp"] + c["fn"]

    twin_docs = [d for d in per_doc if docs.get(d) and docs[d].twin_of]
    scanned = sum((per_doc[d] for d in twin_docs), Counter())
    originals = sum(
        (per_doc[docs[d].twin_of] for d in twin_docs if docs[d].twin_of in per_doc), Counter()
    )
    _, _, scanned_f1 = _prf(scanned)
    _, _, original_f1 = _prf(originals)
    return {
        "measures": {
            "review_f1": _measure("review_f1", f1, f1_size(total), split(2, by_category, f1_size)),
            "review_precision": _measure(
                "review_precision",
                precision,
                total["tp"] + total["fp"],
                split(0, by_category, lambda c: c["tp"] + c["fp"]),
            ),
            "review_recall": _measure(
                "review_recall",
                recall,
                total["tp"] + total["fn"],
                {
                    **split(1, by_category, lambda c: c["tp"] + c["fn"]),
                    **split(1, by_bucket, lambda c: c["tp"] + c["fn"]),
                },
            ),
            "review_f1_scan_twins": _measure(
                "review_f1_scan_twins",
                scanned_f1,
                f1_size(scanned),
                {
                    "text_original": {"n": f1_size(originals), "value": original_f1},
                    "scanned": {"n": f1_size(scanned), "value": scanned_f1},
                },
            ),
            "nda_verdict_accuracy": _measure(
                "nda_verdict_accuracy", nda_right / nda_n if nda_n else None, nda_n
            ),
        },
        "missed_high_critical": missed,
        "false_alarms": alarms,
        "signatures": signatures,
        "counts": {
            "docs": dict(review_docs),
            "findings_by_bucket": {b: c["tp"] + c["fn"] for b, c in by_bucket.items()},
            "scan_twin_docs": len(twin_docs),
            "unkeyed": unkeyed,
            # The n behind the missed-findings list (FR-26): every high or critical finding the
            # keys hold in a scored review document, found or missed.
            "high_critical_findings": high_critical,
        },
    }


# --- question answering and portfolio ----------------------------------------------------


def _qa(items, content, docs, pos) -> dict:
    keys = {q.item_id: q for q in content.qa}
    found = cited = unkeyed = empty = 0
    by_bucket: dict[str, Counter] = defaultdict(Counter)
    signatures, categories = {}, Counter()
    for item in items:
        key = keys.get(item["item_id"])
        if key is None:
            unkeyed += 1
            continue
        pred = item.get("prediction") or {}
        sources = [s.get("text") or "" for s in pred.get("sources") or []]
        searched = any(_overlaps(s, p) for s in sources for p in key.passages)
        quoted = any(_overlaps(c, p) for c in pred.get("citations") or [] for p in key.passages)
        starts = [s for s in (pos.start(key.doc_id, p) for p in key.passages) if s is not None]
        bucket = _bucket(min(starts) if starts else None)
        found += searched
        cited += quoted
        empty += not sources
        categories[key.category.value] += 1
        by_bucket[bucket]["n"] += 1
        by_bucket[bucket]["searched"] += searched
        by_bucket[bucket]["cited"] += quoted
        signatures[item["item_id"]] = [searched, quoted]
    n = sum(categories.values())

    def split(field: str) -> dict:
        return {b: _share(c[field], c["n"]) for b, c in by_bucket.items() if b != "unplaced"}

    return {
        "measures": {
            "qa_evidence_found": _measure(
                "qa_evidence_found", cited / n if n else None, n, split("cited")
            ),
            "qa_search_found": _measure(
                "qa_search_found", found / n if n else None, n, split("searched")
            ),
        },
        "signatures": signatures,
        "counts": {
            "questions": dict(categories),
            "past_25k": by_bucket["past_25k"]["n"],
            "unplaced": by_bucket["unplaced"]["n"],
            "empty_context": empty,
            "unkeyed": unkeyed,
        },
    }


def _portfolio(items, content, results) -> dict:
    keys = {q.item_id: q for q in content.portfolio}
    universe = None
    if results.get("mode") == "sample" and content.sample.get("portfolio_docs"):
        universe = set(content.sample["portfolio_docs"])  # the sample's small sub-portfolio
    questions, signatures = [], {}
    tp = fp = fn = unkeyed = 0
    for item in items:
        key = keys.get(item["item_id"])
        if key is None:
            unkeyed += 1
            continue
        expected = set(key.expected_doc_ids)
        named = set((item.get("prediction") or {}).get("named_doc_ids") or [])
        if universe is not None:
            expected, named = expected & universe, named & universe
        found, missed, wrong = expected & named, expected - named, named - expected
        tp, fn, fp = tp + len(found), fn + len(missed), fp + len(wrong)
        questions.append(
            {
                "item_id": item["item_id"],
                "expected": len(expected),
                "found": len(found),
                "missed": len(missed),
                "wrongly_included": len(wrong),
                "missed_doc_ids": sorted(missed),
                "wrong_doc_ids": sorted(wrong),
            }
        )
        signatures[item["item_id"]] = sorted(named)
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
    return {
        "measures": {
            "portfolio_completeness": _measure("portfolio_completeness", f1, tp + fn),
        },
        "questions": questions,
        "signatures": signatures,
        "counts": {"questions": len(questions), "unkeyed": unkeyed},
    }


# --- routing ---------------------------------------------------------------------------


def _routing(items, content, results) -> dict:
    snapshot = (content.workflow_snapshot or {}).get("fingerprint")
    keys = {k.item_id: k for k in content.routing_keys if k.status != "unmarked"}
    stale = bool((results.get("excluded") or {}).get("routing_out_of_date")) or any(
        snapshot is not None and k.workflow_fingerprint != snapshot for k in keys.values()
    )  # a workflow was added or removed since the owner picked: nothing to score against
    right = n = unkeyed = 0
    signatures = {}
    for item in items:
        key = keys.get(item["item_id"])
        if key is None:
            unkeyed += 1
            continue
        got = (item.get("prediction") or {}).get("workflow_id")
        signatures[item["item_id"]] = got
        if not stale:
            n += 1
            right += got == key.workflow_id
    marked = [k for k in content.routing_keys if k.status != "unmarked"]
    workflows = (content.workflow_snapshot or {}).get("workflows") or []
    per_workflow = {
        w["id"]: {"name": w.get("name"), "requests": 0} for w in workflows if w.get("enabled", True)
    }
    for request in content.routing:
        slot = per_workflow.setdefault(
            request.drafted_for_workflow_id, {"name": None, "requests": 0}
        )
        slot["requests"] += 1
    return {
        "measures": {"routing_accuracy": _measure("routing_accuracy", right / n if n else None, n)},
        "signatures": signatures,
        "counts": {
            "requests": len(content.routing),
            "scored": n,
            "unpicked": len(content.routing) - len(marked),
            "out_of_date": stale,
            "workflows_total": len(per_workflow),
            "workflows_covered": sum(1 for w in per_workflow.values() if w["requests"]),
            "requests_per_workflow": per_workflow,
            "pick_changed_rate": (
                sum(k.status == "corrected" for k in marked) / len(marked) if marked else None
            ),
            "unkeyed": unkeyed,
        },
    }


# --- label-free checks, cost and time (FR-10..FR-14) ---------------------------------------


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def _calls(results: Mapping) -> dict:
    types: dict[str, dict] = {}
    coverage, invalid, failed = [], [], []
    quotes = valid = aligned = 0
    for call in results.get("calls") or []:
        t = types.setdefault(
            call["call_type"],
            {
                "calls": 0,
                "batched": 0,
                "failed": 0,
                "truncated": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0,
                "list_cost_usd": 0.0,
                "latency_ms_sum": 0,
                "latency_ms_calls": 0,
                "coverage_sum": 0.0,
                "coverage_calls": 0,
                "quotes": 0,
                "quotes_valid": 0,
            },
        )
        ok = str(call.get("status") or "").lower() == "succeeded"
        t["calls"] += 1
        t["batched"] += bool(call.get("batched"))
        t["truncated"] += call.get("stop_reason") == "max_tokens"
        for field in (
            "prompt_tokens",
            "completion_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            t[field] += call.get(field) or 0
        cost = call.get("cost_usd") or 0.0
        t["cost_usd"] += cost
        t["list_cost_usd"] += cost * 2 if call.get("batched") else cost
        if call.get("latency_ms") is not None and not call.get("batched"):
            t["latency_ms_sum"] += call["latency_ms"]
            t["latency_ms_calls"] += 1
        if not ok:
            t["failed"] += 1
            failed.append(
                {
                    "call_id": call.get("call_id"),
                    "call_type": call["call_type"],
                    "status": call.get("status"),
                    "doc_id": call.get("doc_id"),
                    "item_ids": call.get("item_ids") or [],
                }
            )
        seen, total = call.get("chars_seen"), call.get("chars_total")
        if seen is not None and total:
            share = min(1.0, seen / total)
            t["coverage_sum"] += share
            t["coverage_calls"] += 1
            coverage.append(
                {
                    "call_id": call.get("call_id"),
                    "call_type": call["call_type"],
                    "doc_id": call.get("doc_id"),
                    "chars_seen": seen,
                    "chars_total": total,
                    "coverage": share,
                }
            )
        if ok:  # a call that failed gave no answer, so its quotes are not an answer's quotes
            for quote in call.get("quotes") or []:
                quotes += 1
                t["quotes"] += 1
                valid += bool(quote.get("word_for_word"))
                t["quotes_valid"] += bool(quote.get("word_for_word"))
                aligned += (quote.get("align_score") or 0) >= ALIGN_THRESHOLD
                if not quote.get("word_for_word"):
                    invalid.append(
                        {
                            "call_id": call.get("call_id"),
                            "call_type": call["call_type"],
                            "doc_id": call.get("doc_id"),
                            "text": quote.get("text"),
                        }
                    )
    for t in types.values():
        t["cost_usd"] = round(t["cost_usd"], 6)
        t["list_cost_usd"] = round(t["list_cost_usd"], 6)
        t["total_tokens"] = sum(
            t[f]
            for f in (
                "prompt_tokens",
                "completion_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
            )
        )
        t["truncation_rate"] = t["truncated"] / t["calls"]
        t["coverage_avg"] = t["coverage_sum"] / t["coverage_calls"] if t["coverage_calls"] else None
        t["latency_ms_avg"] = (
            t["latency_ms_sum"] / t["latency_ms_calls"] if t["latency_ms_calls"] else None
        )
    # Totals are the sum of the parts, by construction (AC-14).
    summed = (
        "calls",
        "batched",
        "failed",
        "truncated",
        "prompt_tokens",
        "completion_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
        "latency_ms_sum",
        "latency_ms_calls",
        "coverage_calls",
        "quotes",
        "quotes_valid",
    )
    totals = {f: sum(t[f] for t in types.values()) for f in summed}
    totals["cost_usd"] = round(fsum(t["cost_usd"] for t in types.values()), 6)
    totals["list_cost_usd"] = round(fsum(t["list_cost_usd"] for t in types.values()), 6)
    coverage_sum = fsum(t["coverage_sum"] for t in types.values())
    totals["coverage_avg"] = (
        coverage_sum / totals["coverage_calls"] if totals["coverage_calls"] else None
    )
    totals["truncation_rate"] = totals["truncated"] / totals["calls"] if totals["calls"] else None
    totals["latency_ms_avg"] = (
        totals["latency_ms_sum"] / totals["latency_ms_calls"]
        if totals["latency_ms_calls"]
        else None
    )
    return {
        "by_type": types,
        "totals": totals,
        "coverage": coverage,
        "failed": failed,
        "invalid_quotes": invalid,
        "quotes": {"n": quotes, "valid": valid, "aligned": aligned},
    }


def _label_free(calls: dict, results: Mapping) -> dict:
    types, totals, q = calls["by_type"], calls["totals"], calls["quotes"]
    n_calls = totals["calls"]
    by_type = {
        "quote_validity": {k: _share(t["quotes_valid"], t["quotes"]) for k, t in types.items()},
        "truncation_rate": {k: _share(t["truncated"], t["calls"]) for k, t in types.items()},
        "coverage_avg": {
            k: {"n": t["coverage_calls"], "value": t["coverage_avg"]} for k, t in types.items()
        },
        "cost_usd": {k: {"n": t["calls"], "value": t["cost_usd"]} for k, t in types.items()},
        "list_cost_usd": {
            k: {"n": t["calls"], "value": t["list_cost_usd"]} for k, t in types.items()
        },
        "total_tokens": {
            k: {"n": t["calls"], "value": t["total_tokens"]} for k, t in types.items()
        },
    }
    turnaround: dict[str, float] = defaultdict(float)
    for batch in results.get("batches") or []:
        took = _seconds(batch.get("submitted_at"), batch.get("ended_at"))
        if took is not None:
            turnaround[str(batch.get("phase"))] += took
    wall = results.get("wall_time_s")
    measures = {
        "quote_validity": _measure(
            "quote_validity",
            q["valid"] / q["n"] if q["n"] else None,
            q["n"],
            by_type["quote_validity"],
        ),
        "quote_alignment_85": _measure(
            "quote_alignment_85", q["aligned"] / q["n"] if q["n"] else None, q["n"]
        ),
        "truncation_rate": _measure(
            "truncation_rate", totals["truncation_rate"], n_calls, by_type["truncation_rate"]
        ),
        "coverage_avg": _measure(
            "coverage_avg",
            totals["coverage_avg"],
            totals["coverage_calls"],
            by_type["coverage_avg"],
        ),
        "cost_usd": _measure("cost_usd", totals["cost_usd"], n_calls, by_type["cost_usd"]),
        "list_cost_usd": _measure(
            "list_cost_usd", totals["list_cost_usd"], n_calls, by_type["list_cost_usd"]
        ),
        "total_tokens": _measure(
            "total_tokens", totals["total_tokens"], n_calls, by_type["total_tokens"]
        ),
        "wall_time_s": _measure("wall_time_s", wall, 1 if wall is not None else 0),
        "latency_ms_avg": _measure(
            "latency_ms_avg", totals["latency_ms_avg"], totals["latency_ms_calls"]
        ),
        "batch_turnaround_s": _measure(
            "batch_turnaround_s",
            sum(turnaround.values()),
            len(turnaround),
        ),
    }
    estimate = results.get("estimate") or {}
    est_types = estimate.get("by_call_type") or {}
    return {
        "measures": measures,
        "time": {
            "wall_time_s": wall,
            "phase_wall_time_s": results.get("phase_wall_time_s") or {},
            "batch_turnaround_s": dict(turnaround),
        },
        "cost_vs_estimate": {
            "total": {"estimate_usd": estimate.get("usd"), "actual_usd": totals["cost_usd"]},
            "by_call_type": {
                name: {
                    "estimate_usd": (est_types.get(name) or {}).get("usd"),
                    "actual_usd": t["cost_usd"],
                }
                for name, t in types.items()
            },
        },
    }


# --- one run ---------------------------------------------------------------------------


def _reason_code(reason) -> str | None:
    """The short label of a runner's failure reason ("upstream: <detail>" -> "upstream").

    The detail can embed model output or contract text, so it is kept under ``private`` only;
    anything that does not look like a plain label is "other"."""
    if not reason:
        return None
    head = str(reason).split(":", 1)[0].strip()
    return head if re.fullmatch(r"[A-Za-z_ -]{1,40}", head) else "other"


def score_run(
    results: dict, content: ExamContent, *, texts: Mapping[str, str] | None = None
) -> dict:
    """Score one run. ``texts`` (doc_id -> text) places key passages for the before/past-25k
    splits; left out, the plain-text fixtures on disk are used (``load_texts``)."""
    if texts is None:
        texts = load_texts(content)
    docs: dict[str, ExamDocument] = {d.doc_id: d for d in content.documents}
    pos = _Positions(texts)
    scored: dict[str, list[dict]] = defaultdict(list)
    unscored, failure_reasons, status_counts = [], {}, Counter()
    for item in results.get("items") or []:
        status_counts[item.get("status")] += 1
        if item.get("status") == "scored":
            scored[item["task"]].append(item)
        else:
            reason = (item.get("failure") or {}).get("reason")
            if reason:
                failure_reasons[item["item_id"]] = reason
            unscored.append(
                {
                    "item_id": item["item_id"],
                    "task": item["task"],
                    "doc_id": item.get("doc_id"),
                    "status": item.get("status"),
                    "reason_code": _reason_code(reason),
                }
            )
    review = _review(scored[Task.REVIEW], content, docs, pos)
    extraction = _extraction(scored[Task.EXTRACTION], content, docs, pos, results)
    qa = _qa(scored[Task.QA], content, docs, pos)
    portfolio = _portfolio(scored[Task.PORTFOLIO], content, results)
    routing = _routing(scored[Task.ROUTING], content, results)
    calls = _calls(results)
    label_free = _label_free(calls, results)

    measures: dict[str, dict] = {}
    for part in (review, extraction, qa, portfolio, routing, label_free):
        measures.update(part["measures"])
    measures = {key: measures[key] for key in SPECS if key in measures}

    signatures = {}
    for task, part in (
        ("review", review),
        ("extraction", extraction),
        ("qa", qa),
        ("portfolio", portfolio),
        ("routing", routing),
    ):
        signatures.update({i: {"task": task, "sig": s} for i, s in part["signatures"].items()})

    marked = [k for k in content.owner_keys if k.status != "unmarked"]
    fingerprint = (results.get("versions") or {}).get("content_fingerprint")
    n_calls = calls["totals"]["calls"]
    failed_rate = results.get("failed_call_rate")
    return {
        "schema": SCHEMA,
        "run": {
            "run_id": results.get("run_id"),
            "mode": results.get("mode"),
            "delivery": results.get("delivery"),
            "outcome": results.get("outcome"),
            "contains_errors": results.get("contains_errors"),
            "failed_call_rate": (
                failed_rate
                if failed_rate is not None
                else (len(calls["failed"]) / n_calls if n_calls else 0.0)
            ),
            "change": results.get("change"),
            "target_area": results.get("target_area"),
            "content_fingerprint": fingerprint,
            # False: these answers were given to other questions or keys than ``content`` holds.
            "content_current": fingerprint == content_fingerprint(content),
        },
        "measures": measures,
        "missed_high_critical": review["missed_high_critical"],
        "false_alarms": review["false_alarms"],
        "failed_items": unscored,
        "failed_calls": calls["failed"],
        "fields": extraction["fields"],
        "portfolio": {"questions": portfolio["questions"]},
        "by_call_type": calls["by_type"],
        "totals": calls["totals"],
        "coverage_per_call": calls["coverage"],
        "time": label_free["time"],
        "cost_vs_estimate": label_free["cost_vs_estimate"],
        "counts": {
            "items": {s or "unknown": n for s, n in status_counts.items()},
            "review": review["counts"],
            "extraction": extraction["counts"],
            "qa": qa["counts"],
            "portfolio": portfolio["counts"],
            "routing": routing["counts"],
            "calls": n_calls,
            "failed_calls": len(calls["failed"]),
            "owner": {
                "rows_marked": len(marked),
                "rows_unmarked": len(content.owner_keys) - len(marked),
                "prefill_changed_rate": (
                    sum(k.status == "corrected" for k in marked) / len(marked) if marked else None
                ),
            },
        },
        "private": {
            "invalid_quotes": calls["invalid_quotes"],
            "signatures": signatures,
            "failure_reasons": failure_reasons,
        },
    }


# --- two runs: the noise band -----------------------------------------------------------


def _band(a, b) -> dict:
    if a is None or b is None:
        return {"run_a": a, "run_b": b, "average": None, "noise_band": None}
    return {"run_a": a, "run_b": b, "average": (a + b) / 2, "noise_band": abs(a - b)}


def _pair_measure(a: dict, b: dict) -> dict:
    splits = {}
    for name in dict.fromkeys([*a["splits"], *b["splits"]]):
        sa, sb = a["splits"].get(name), b["splits"].get(name)
        n = min(sa["n"], sb["n"]) if sa and sb else 0
        splits[name] = {"n": n, **_band(sa["value"] if sa else None, sb["value"] if sb else None)}
    return {
        **{k: a[k] for k in ("key", "label", "explanation", "direction", "role")},
        "n": min(a["n"], b["n"]),
        "n_a": a["n"],
        "n_b": b["n"],
        "values": _band(a["value"], b["value"]),
        "splits": splits,
    }


def _consistency(sa: dict, sb: dict) -> dict:
    """Share of items scored in both runs that received the same normalised answer (FR-13)."""
    sigs_a, sigs_b = sa["private"]["signatures"], sb["private"]["signatures"]
    both = [i for i in sigs_a if i in sigs_b]
    by_task: dict[str, Counter] = defaultdict(Counter)
    for item_id in both:
        task = sigs_a[item_id]["task"]
        by_task[task]["n"] += 1
        by_task[task]["same"] += sigs_a[item_id]["sig"] == sigs_b[item_id]["sig"]
    same = sum(c["same"] for c in by_task.values())
    return {
        "key": "consistency",
        **SPECS["consistency"],
        "n": len(both),
        "n_a": len(sigs_a),
        "n_b": len(sigs_b),
        "values": {
            "run_a": None,
            "run_b": None,
            "average": same / len(both) if both else None,
            "noise_band": None,
        },
        "splits": {
            t: {
                "n": c["n"],
                "run_a": None,
                "run_b": None,
                "average": c["same"] / c["n"],
                "noise_band": None,
            }
            for t, c in by_task.items()
        },
    }


def score_pair(
    a: dict, b: dict, content: ExamContent, *, texts: Mapping[str, str] | None = None
) -> dict:
    """Two runs of the same code side by side. ``a`` and ``b`` are ``results.json`` dicts.

    Everything at the top level of the result is public-safe; the two runs' full scores, with
    their private detail (quote text, answers), sit under ``private`` and must not be written to
    a committed report.
    """
    if a.get("run_id") == b.get("run_id"):
        # One run scored twice has a noise band of exactly 0, which would make any change that
        # moves a measure at all look like a win.
        raise ComparisonRefused("the two runs are the same run; a noise band needs two runs")
    versions = [(r.get("versions") or {}).get("content_fingerprint") for r in (a, b)]
    if None in versions:
        raise ComparisonRefused("a run did not record which exam content it was asked")
    if versions[0] != versions[1]:
        raise ComparisonRefused("the two runs used different exam content")
    if versions[0] != content_fingerprint(content):
        # Scoring old answers against keys edited since would make the comparison meaningless
        # while the two runs still agreed with each other.
        raise ComparisonRefused("the exam content has changed since these runs were made")
    if a.get("delivery") != b.get("delivery"):
        raise ComparisonRefused("the two runs used different delivery modes")
    if texts is None:
        texts = load_texts(content)
    sa, sb = score_run(a, content, texts=texts), score_run(b, content, texts=texts)
    by_key = {k: _pair_measure(sa["measures"][k], sb["measures"][k]) for k in sa["measures"]}
    by_key["consistency"] = _consistency(sa, sb)
    measures = [by_key[k] for k in SPECS if k in by_key]
    missed: dict[tuple, dict] = {}
    for label, scores in (("a", sa), ("b", sb)):
        for entry in scores["missed_high_critical"]:
            slot = missed.setdefault(
                (entry["item_id"], entry["rule"]), {**entry, "runs_missed": []}
            )
            slot["runs_missed"].append(label)
    return {
        "schema": PAIR_SCHEMA,
        "content_fingerprint": versions[0],
        "delivery": a.get("delivery"),
        "runs": [
            {
                **s["run"],
                "versions": r.get("versions"),
                "cost_usd": s["totals"]["cost_usd"],
                "estimate_usd": (r.get("estimate") or {}).get("usd"),
                "wall_time_s": r.get("wall_time_s"),
            }
            for s, r in ((sa, a), (sb, b))
        ],
        "measures": measures,
        "missed_high_critical": list(missed.values()),
        "counts": sa["counts"],
        "private": {"scores": {"a": sa, "b": sb}},
    }


# --- verdict: did a change beat the baseline? --------------------------------------------


def _outcome(measure: dict, change: dict | None) -> dict:
    """How one measure moved: the change's average against the baseline's, judged against the
    baseline's own noise band. ``delta`` is signed so that positive is always better
    (truncation rate is lower-is-better, plan FR-28); ``difference`` is the plain change."""
    base_avg = measure["values"]["average"]
    new_avg = change["values"]["average"] if change else None
    band = measure["values"]["noise_band"] or 0.0
    out = {
        "key": measure["key"],
        "baseline_average": base_avg,
        "change_average": new_avg,
        "delta": None,
        "difference": None,
        "noise_band": band,
        "result": "not_measured",
    }
    if base_avg is None or new_avg is None:
        return out
    delta = (new_avg - base_avg) * (1 if measure["direction"] == "higher" else -1)
    out["delta"] = delta
    out["difference"] = new_avg - base_avg
    # Rounded so that exactly one noise band of movement is "within the band", not float luck.
    if round(delta - band, 9) > 0:
        out["result"] = "improved"
    elif round(delta + band, 9) < 0:
        out["result"] = "worsened"
    else:
        out["result"] = "within_band"
    return out


def _recorded_target(change_pair: dict) -> str | None:
    """The target area both of the change's runs recorded when they were approved, before any
    answer existed (FR-28). None when either run recorded none; a refusal when they disagree.

    Read from the runs and never passed in: a target chosen when the verdict is asked for could
    be picked after seeing which measure moved."""
    targets = {r.get("target_area") or None for r in change_pair["runs"]}
    if None in targets:
        return None
    if len(targets) > 1:
        raise ComparisonRefused(
            "the change's two runs recorded different target areas "
            f"({' and '.join(sorted(targets))}); a change has one target"
        )
    return targets.pop()


def verdict(baseline_pair: dict, change_pair: dict) -> dict:
    """Pass only if the target area's main measure improves by more than its noise band AND no
    other headline quality measure worsens by more than its own (FR-28). Report-only measures
    and cost/time are shown beside the scores and never decide (D-10, D-12). The target area is
    the one recorded on the change's two runs (``_recorded_target``)."""
    if baseline_pair["content_fingerprint"] != change_pair["content_fingerprint"]:
        raise ComparisonRefused(
            "the change was run against different exam content than the baseline"
        )
    if baseline_pair["delivery"] != change_pair["delivery"]:
        raise ComparisonRefused(
            f"the baseline used {baseline_pair['delivery']} delivery and the change "
            f"{change_pair['delivery']}; they are not comparable"
        )
    if any(r["mode"] != "full" for p in (baseline_pair, change_pair) for r in p["runs"]):
        raise ComparisonRefused("a sample run is not comparable to the baseline")

    target = _recorded_target(change_pair)
    main = MAIN_MEASURE.get(target) if target else None
    changed = {m["key"]: m for m in change_pair["measures"]}
    per_measure, report_only, cost_time = [], [], []
    for measure in baseline_pair["measures"]:
        key = measure["key"]
        if measure["role"] == HEADLINE:
            out = _outcome(measure, changed.get(key))
            out["decides"] = True
            if out["result"] == "not_measured":
                out["passed"] = None
            elif key == main:
                out["passed"] = out["result"] == "improved"
            else:
                out["passed"] = out["result"] != "worsened"
            per_measure.append(out)
        elif measure["role"] == REPORT_ONLY:
            out = _outcome(measure, changed.get(key))
            report_only.append(
                {
                    k: out[k]
                    for k in (
                        "key",
                        "baseline_average",
                        "change_average",
                        "delta",
                        "difference",
                        "noise_band",
                    )
                }
            )
        else:
            base_avg = measure["values"]["average"]
            new_avg = (changed.get(key) or {"values": {}})["values"].get("average")
            cost_time.append(
                {
                    "key": key,
                    "baseline": base_avg,
                    "change": new_avg,
                    "difference": new_avg - base_avg if None not in (base_avg, new_avg) else None,
                }
            )

    reason = None
    if target is None:
        reason = "target not recorded before runs"
    elif main is None:
        reason = f"unknown target area {target!r}"
    elif (
        next((m["result"] for m in per_measure if m["key"] == main), "not_measured")
        == "not_measured"
    ):
        reason = "main measure not measured"
    failed = [] if reason else [m["key"] for m in per_measure if m["passed"] is False]
    return {
        "baseline": baseline_pair.get("label"),
        "change": change_pair.get("label"),
        "delivery": baseline_pair["delivery"],
        "target_area": target,
        "main_measure": main,
        "verdict": "no_verdict" if reason else "fail" if failed else "pass",
        "no_verdict_reason": reason,
        "per_measure": per_measure,
        "failed_measures": failed,
        "report_only": report_only,
        "cost_time": cost_time,
    }
