"""Complete answers to "which / how many contracts ..." questions.

Retrieval looks at the best-matching passages, so a question about the whole
portfolio answered from it names some contracts and says nothing about the rest.
Here the model only turns the question into a typed filter (`PortfolioFilter`);
CODE runs it over EVERY contract the user can open and reports what it checked:

  * a condition a database field answers (type, counterparty, governing law, stage,
    risk, dates, value) is decided per contract as True / False / unknown. A blank
    field is unknown, never "no", so it is listed rather than silently left out;
  * wording no field answers ("renews automatically", "no limitation of liability
    clause") is read by the AI, one read per contract, through the existing table
    review. Code decides from each cell's typed `verdict`, never from its prose;
  * nothing the model writes reaches the answer text or the confidence.

The RESULT dict is stored on the BrainQuery, returned by the chat tool and typed in
the frontend. `portfolio_result` rebuilds it from the stored copy and the review's
current cells, so a list that was still being read when asked fills in later.

ponytail: conditions are evaluated in Python over every accessible contract. Fine at
hundreds to a few thousand; push the field conditions into SQL when books reach tens
of thousands.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from datetime import date, timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from app.ai.citations import align_citation_to_source
from app.ai.context import MAX_FULL_TEXT_CHARS
from app.ai.cost_guard import tokens_left
from app.ai.schemas import DateRange, PortfolioFilter
from app.analytics.routes import token_cost
from app.auth.models import User
from app.contract_brain.entities import normalize_org_name
from app.contract_brain.grounding import GROUNDING_THRESHOLD
from app.contract_brain.retrieval import accessible_contract_ids
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.access import accessible_contract_filter
from app.contracts.models import Contract
from app.core import org_access
from app.core.audit import write_audit_log
from app.core.config import settings
from app.core.database import utcnow
from app.core.deadlines import add_months
from app.core.enums import TabularCellStatus
from app.core.rbac import has_permission
from app.parties.models import Counterparty
from app.tabular_review.models import TabularReview, TabularReviewCell, TabularReviewColumn
from app.tabular_review.service import MAX_REVIEW_CONTRACTS, TabularReviewService

# --- which questions come here ---------------------------------------------------------

# A cheap gate before the one AI call that proposes a filter. Wide on purpose: a
# false positive costs one small call (the model answers is_list_question=false and
# the question goes to search); a false negative is caught by the search answer's
# "not every contract" limitation.
LIST_QUESTION_RE = re.compile(
    r"\b(which|how\s+many|list|count|number\s+of"
    r"|(show|find|give)(\s+me)?\s+(all|every|any)"
    r"|(all|any|every)\s+(of\s+)?(my\s+|our\s+|the\s+)?(contracts?|agreements?)"
    r"|what\s+(contracts?|agreements?))\b",
    re.IGNORECASE,
)

# A bare "how many contracts?" / "list contracts" has no condition to propose, so it
# needs no AI call: the empty filter matches every contract the user can open.
_FILLER = (
    r"(are there|do (i|we) have|are in (my |the )?portfolio|in (my |the )?portfolio"
    r"|exist|in total|total|right now|now|currently|in this project|in the project"
    r"|altogether|here)"
)
_COUNT_RE = re.compile(
    rf"^\s*(how many|the number of|number of|count(\s+of)?|total(\s+number\s+of)?)\s+"
    rf"(contracts?|agreements?)\b\s*(?:{_FILLER})?\s*[?.]?\s*$",
    re.IGNORECASE,
)
_LIST_RE = re.compile(
    rf"^\s*(list|show(\s+me)?|what|which)\s+(all\s+|my\s+|the\s+|our\s+)?"
    rf"(contracts?|agreements?)\b\s*(?:{_FILLER}|do (i|we) have)?\s*[?.]?\s*$",
    re.IGNORECASE,
)
BARE_ALL_RE = re.compile(f"(?:{_COUNT_RE.pattern})|(?:{_LIST_RE.pattern})", re.IGNORECASE)


def looks_like_list_question(question: str) -> bool:
    return bool(LIST_QUESTION_RE.search(question or ""))


def bare_all_filter(question: str) -> PortfolioFilter | None:
    """The empty filter for a bare "how many contracts" / "list contracts", else None."""
    q = (question or "").strip()
    if not BARE_ALL_RE.match(q):
        return None
    return PortfolioFilter(count_only=bool(_COUNT_RE.match(q)))


# --- field conditions ------------------------------------------------------------------

# True: the contract meets it (text = the "why" line). False: it does not. None: the
# field is blank or not comparable (text = why we cannot tell).
_Verdict = tuple[bool | None, str]
_Check = tuple[str, Callable[[Contract], _Verdict]]  # (label for the answer, check)

_SOURCE_WORDS = {
    "user": "set by a person",
    "form": "request form",
    "ai": "AI reading",
    "computed": "worked out from the renewal reading",
    "change_request": "change request",
}
_SEP = "; "
_NO_DOCUMENT = "no document to read"


def _d(value: date) -> str:
    return f"{value.day} {value:%b %Y}"


def _money(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _prov(contract: Contract, field: str) -> str:
    """Where a field's value came from, as the contract's own `field_sources` says."""
    sources = (contract.metadata_json or {}).get("field_sources") or {}
    word = _SOURCE_WORDS.get(sources.get(field))
    return f" ({word})" if word else ""


def _bounds(rng: DateRange, today: date) -> tuple[date | None, date | None] | None:
    """The inclusive [from, to] a DateRange means, counted here and never by the model.
    None when it holds nothing usable."""
    lows: list[date] = []
    highs: list[date] = []
    if rng.from_date:
        lows.append(rng.from_date)
    if rng.to_date:
        highs.append(rng.to_date)
    if rng.year is not None:
        if not 1900 <= rng.year <= 2200:
            return None
        lows.append(date(rng.year, 1, 1))
        highs.append(date(rng.year, 12, 31))
    if rng.within_next_days:
        lows.append(today)
        highs.append(today + timedelta(days=rng.within_next_days))
    if rng.within_next_months:
        lows.append(today)
        highs.append(add_months(today, rng.within_next_months))
    if rng.within_last_days:
        lows.append(today - timedelta(days=rng.within_last_days))
        highs.append(today)
    if rng.period:
        lo, hi = _period(rng.period, today)
        lows.append(lo)
        highs.append(hi)
    if not lows and not highs:
        return None
    return (max(lows) if lows else None), (min(highs) if highs else None)


def _period(period: str, today: date) -> tuple[date, date]:
    """The calendar span a named period covers around today."""
    if period.endswith("_year"):
        year = today.year + (period == "next_year")
        return date(year, 1, 1), date(year, 12, 31)
    months = 3 if period.endswith("_quarter") else 1
    start = date(today.year, (today.month - 1) // months * months + 1, 1)
    if period.startswith("next_"):
        start = add_months(start, months)
    return start, add_months(start, months) - timedelta(days=1)


def _range_label(name: str, lo: date | None, hi: date | None) -> str:
    if lo and hi:
        return f"{name} on {_d(lo)}" if lo == hi else f"{name} {_d(lo)} – {_d(hi)}"
    return f"{name} on or after {_d(lo)}" if lo else f"{name} on or before {_d(hi)}"


def _plain(raw: str | None) -> str:
    """Lowercase ASCII words separated by single spaces (accents folded: "Zürich" is
    "zurich"), so phrases match whole words."""
    folded = unicodedata.normalize("NFKD", raw or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded.lower()).split())


def _single(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _has(text: str, phrases) -> bool:
    """Any phrase in `text` (from _plain) as whole words, a trailing plural allowed: "nda"
    is found in "mutual ndas" but "india" is never found in "indiana"."""
    return any(re.search(rf"(?<![a-z0-9_]){re.escape(p)}s?(?![a-z0-9_])", text) for p in phrases)


# --- kinds of agreement ---

# Kinds of agreement by the phrases that name them. A type matches a kind only through one
# of these phrases, so a generic word ("services", "data", "master") never makes an MSA of a
# DPA. A type naming no kind here is matched by its own words.
_KINDS = (
    ("nda", "mnda", "cda", "non disclosure", "nondisclosure", "confidentiality agreement", "secrecy agreement"),
    ("msa", "master service", "master agreement"),
    ("sow", "statement of work", "work order", "task order"),
    ("dpa", "data processing", "data protection agreement", "data protection addendum"),
    ("saas", "software as a service"),
    ("consultancy", "consulting", "consultant"),
    ("vendor", "supplier", "supply agreement", "procurement"),
    ("licence", "license", "licensing"),
    ("lease", "rental agreement", "tenancy"),
    ("employment", "offer letter"),
    ("letter of intent", "loi", "term sheet", "mou", "memorandum of understanding"),
    ("amendment", "addendum"),
)
_GENERIC_TYPE_WORDS = {"agreement", "contract", "the", "a", "an", "all", "my", "our", "any", "every"}


def _type_words(plain: str) -> list[str]:
    return [w for w in plain.split() if _single(w) not in _GENERIC_TYPE_WORDS and w not in _GENERIC_TYPE_WORDS]


def _type_head(plain: str) -> str:
    """What a recorded type IS: "Amendment to MSA" is an amendment, not an MSA, and
    "Data Processing Addendum to NDA" is a DPA."""
    return plain.split(" to ")[0]


def _type_want(wanted: str) -> tuple[tuple[str, ...], list[str]] | None:
    """(phrases a recorded type must contain, qualifier words it must also carry) for a
    type the user named, or None when it names no kind at all ("agreements"). "Mutual NDA"
    is an NDA phrase plus the qualifier "mutual"."""
    plain = _plain(wanted)
    words = _type_words(plain)
    if not words:
        return None
    kinds = [kind for kind in _KINDS if _has(plain, kind)]
    if not kinds:
        return (" ".join(_single(w) for w in words),), []
    phrases = tuple(p for kind in kinds for p in kind)
    named = {_single(w) for kind in kinds for p in kind if _has(plain, (p,)) for w in p.split()}
    return phrases, [w for w in words if _single(w) not in named]


# --- places ---

# Places whose names contain another place's name, read as one unit first so "wales" is
# never found in New South Wales nor "british" (= UK) in British Columbia.
_COMPOUND_PLACES = (
    "new south wales", "british columbia", "british virgin islands", "british indian ocean territory",
    "papua new guinea", "west virginia", "new mexico", "new jersey", "new hampshire", "new york",
    "northern ireland", "south africa", "south korea", "north carolina", "north dakota",
    "south carolina", "south dakota", "rhode island", "district of columbia", "new zealand",
)
_PLACE_ALIASES = {"n y": "new york", "ny": "new york", "calif": "california", "d c": "district of columbia",
                  "dc": "district of columbia", "uae": "united arab emirates", "u a e": "united arab emirates"}
_DEMONYMS = {
    "english": "england", "welsh": "wales", "scottish": "scotland", "british": "united kingdom",
    "american": "united states", "indian": "india", "singaporean": "singapore", "irish": "ireland",
    "french": "france", "german": "germany", "dutch": "netherlands", "swiss": "switzerland",
    "australian": "australia", "canadian": "canada", "japanese": "japan", "chinese": "china",
}
# Words around a place that are not the place ("the laws of the State of Delaware").
_PLACE_FILLER = {"law", "laws", "the", "of", "state", "republic", "commonwealth", "province",
                 "governed", "governing", "by", "under", "courts", "court", "jurisdiction"}
_UK_NATIONS = ("england", "wales", "scotland", "northern_ireland")
_UK_NAMES = ("united kingdom", "uk", "u k", "great britain", "britain")
_US_STATES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "ohio", "oklahoma", "oregon", "pennsylvania",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington", "wisconsin", "wyoming",
    "new_hampshire", "new_jersey", "new_mexico", "new_york", "north_carolina", "north_dakota",
    "rhode_island", "south_carolina", "south_dakota", "west_virginia", "district_of_columbia",
)
_US_NAMES = ("united states", "usa", "u s a", "u s", "us", "america")
# A country asked for by name covers the places a contract names instead ("English law" is
# UK law, a Delaware contract is under US law).
_COUNTRIES = {**{n: (_UK_NAMES, _UK_NATIONS) for n in _UK_NAMES}, **{n: (_US_NAMES, _US_STATES) for n in _US_NAMES}}


def _place_text(raw: str | None) -> str:
    """A governing law as place words: compound places joined into one token, aliases and
    demonyms read as places, and filler words dropped."""
    text = f" {_plain(raw)} "
    for alias, place in _PLACE_ALIASES.items():
        text = text.replace(f" {alias} ", f" {place} ")
    for place in _COMPOUND_PLACES:
        text = text.replace(f" {place} ", f" {place.replace(' ', '_')} ")
    words = [_DEMONYMS.get(w, w) for w in text.split()]
    return " ".join(w for w in " ".join(words).split() if w not in _PLACE_FILLER)


def _law_want(wanted: str) -> list[tuple[str, ...]]:
    """Each place the user named (split on and / & / or / slash), as the phrases a
    recorded governing law may name to be under it."""
    out = []
    # Not on commas: "New York, USA" is one place.
    for part in re.split(r"\s*(?:\band\b|\bor\b|&|/|;)\s*", wanted or ""):
        place = _place_text(part)
        # "New York, USA" is New York: a state or nation named with its country is that state.
        inner = [w for w in place.split() if w in _US_STATES or w in _UK_NATIONS]
        place = " ".join(inner) if len(inner) == 1 else place
        if place:
            names, within = _COUNTRIES.get(place, ((place,), ()))
            out.append(tuple(dict.fromkeys((*names, *within))))
    return out


def _law_verdict(recorded: str | None, wanted: list[tuple[str, ...]]) -> bool | None:
    """True / False / None (a recorded country cannot settle a question about one of its
    states or nations: "US" neither is nor rules out Delaware)."""
    have = _place_text(recorded)
    if any(_has(have, phrases) for phrases in wanted):
        return True
    for names, within in ((_US_NAMES, _US_STATES), (_UK_NAMES, _UK_NATIONS)):
        only_country = _has(have, names) and not _has(have, within)
        if only_country and any(set(phrases) <= set(within) for phrases in wanted):
            return None
    return False


def _cannot(reason: str) -> Callable[[Contract], _Verdict]:
    # A condition that cannot be run leaves every contract unknown on it. Dropping it would
    # turn the rest of the question into "every contract matches".
    return lambda c: (None, reason)


def _build_checks(
    db: Session, flt: PortfolioFilter, *, today: date, contracts: list[Contract]
) -> tuple[list[_Check], PortfolioFilter]:
    """The field checks for this filter, and the filter as applied (a condition that
    says nothing, like the type "agreements", is left out of it)."""
    applied = flt.model_copy(deep=True)
    checks: list[_Check] = []

    # "agreements" / "contracts" name no kind: not a condition at all.
    applied.contract_types = [t for t in flt.contract_types if _type_want(t)]
    wants = [_type_want(t) for t in applied.contract_types]
    if wants:

        def by_type(c: Contract) -> _Verdict:
            have = _plain(c.contract_type)
            if not have:
                return None, "no contract type recorded"
            if not _type_words(have):
                return None, f"the type “{c.contract_type}” names no kind of agreement"
            unsure = []
            for phrases, qualifiers in wants:
                if _has(_type_head(have), phrases):
                    missing = [q for q in qualifiers if not _has(have, (q,))]
                    if not missing:
                        return True, f"type: {c.contract_type}{_prov(c, 'contract_type')}"
                    unsure.append(" ".join(missing))
            if unsure:
                # "Mutual NDA" asked, "NDA" recorded: it may or may not be mutual.
                return None, f"the type “{c.contract_type}” does not say {' or '.join(unsure)}"
            return False, ""

        checks.append(("type: " + " or ".join(applied.contract_types), by_type))

    if flt.counterparty and flt.counterparty.strip():
        want = set((normalize_org_name(flt.counterparty) or flt.counterparty.lower()).split())
        register_ids = {c.counterparty_id for c in contracts if c.counterparty_id}
        register = (
            dict(
                db.execute(
                    select(Counterparty.id, Counterparty.name).where(Counterparty.id.in_(register_ids))
                ).all()
            )
            if register_ids
            else {}
        )

        def by_counterparty(c: Contract) -> _Verdict:
            names = [c.counterparty_name, register.get(c.counterparty_id)]
            keys = [(n, normalize_org_name(n)) for n in names if n]
            keys = [(n, k) for n, k in keys if k]
            if not keys:
                return None, "no counterparty recorded"
            for shown, key in keys:
                have = set(key.split())
                if want <= have or have <= want:
                    return True, f"counterparty: {shown}"
            return False, ""

        checks.append((f"counterparty: {flt.counterparty.strip()}", by_counterparty))
    else:
        applied.counterparty = None

    if flt.governing_law and flt.governing_law.strip():
        label = f"governing law: {flt.governing_law.strip()}"
        places = _law_want(flt.governing_law)
        if places:

            def by_law(c: Contract) -> _Verdict:
                if not (c.jurisdiction or "").strip():
                    return None, "no governing law recorded"
                if not _place_text(c.jurisdiction):
                    return None, f"governing law “{c.jurisdiction}” could not be compared"
                verdict = _law_verdict(c.jurisdiction, places)
                if verdict is None:
                    return None, f"governing law recorded only as “{c.jurisdiction}”"
                if verdict:
                    return True, f"governing law: {c.jurisdiction}{_prov(c, 'jurisdiction')}"
                return False, ""

            checks.append((label, by_law))
        else:
            checks.append((label, _cannot(f"could not use “{flt.governing_law}” as a place")))
    else:
        applied.governing_law = None

    if flt.stages:

        def by_stage(c: Contract) -> _Verdict:
            return (True, f"stage: {c.lifecycle_stage}") if c.lifecycle_stage in flt.stages else (False, "")

        checks.append(("stage: " + " or ".join(flt.stages), by_stage))

    if flt.risk_bands:

        def by_risk(c: Contract) -> _Verdict:
            # No qualifying review leaves the band NULL: "unknown" is a real answer
            # here, and never counts as low.
            band = c.risk_band or "unknown"
            return (True, f"risk: {band}") if band in flt.risk_bands else (False, "")

        checks.append(("risk: " + " or ".join(flt.risk_bands), by_risk))

    for name, field, rng in (("effective", "effective_date", flt.effective), ("expires", "expiration_date", flt.expires)):
        if rng is None:
            continue
        span = _bounds(rng, today)
        if span is None:
            if rng.year is not None:
                checks.append((f"{name} in {rng.year}", _cannot(f"could not use the year {rng.year}")))
            else:
                setattr(applied, name, None)  # an empty range says nothing
            continue
        lo, hi = span

        def by_date(c: Contract, name=name, field=field, lo=lo, hi=hi) -> _Verdict:
            value = getattr(c, field)
            if value is None:
                return None, f"no {'effective' if name == 'effective' else 'expiry'} date recorded"
            if (lo and value < lo) or (hi and value > hi):
                return False, ""
            return True, f"{name} {_d(value)}{_prov(c, field)}"

        checks.append((_range_label(name, lo, hi), by_date))

    if flt.value_min is not None or flt.value_max is not None:
        cur = (flt.currency or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", cur):
            checks.append(("value", _cannot("the value was given without a currency")))
        else:
            vmin, vmax = flt.value_min, flt.value_max

            def by_value(c: Contract) -> _Verdict:
                if c.value_amount is None:
                    return None, "no value recorded"
                if not c.currency:
                    return None, "value has no currency recorded"
                if c.currency.upper() != cur:
                    return None, f"value in {c.currency.upper()}, not {cur}"
                amount = float(c.value_amount)
                if (vmin is not None and amount < vmin) or (vmax is not None and amount > vmax):
                    return False, ""
                return True, f"value {_money(amount)} {cur}{_prov(c, 'value_amount')}"

            if vmin is not None and vmax is not None:
                label = f"value {_money(vmin)} – {_money(vmax)} {cur}"
            elif vmin is not None:
                label = f"value at least {_money(vmin)} {cur}"
            else:
                label = f"value at most {_money(vmax)} {cur}"
            applied.currency = cur
            checks.append((label, by_value))
    else:
        applied.currency = None
    return checks, applied


# --- reading the contract's own words ----------------------------------------------------

# The tabular prompt keys on "yes/no question": that is what makes the AI return a typed
# verdict, and what code decides from.
_YES_NO_RULE = (
    "This is a yes/no question. Set verdict to yes (it is true of this contract), no "
    "(it is false of it) or not_stated (the contract does not say), and quote the words "
    "that decide it."
)
# A prompt around 1,000 tokens (system, skill, schema) travels with every read.
_PROMPT_TOKENS = 1_000


def _current_text(columns: list, contract_ids: list[str]):
    """Rows for the contracts that have a current text (the authoritative version's own
    snapshot, as the retrieval text lens reads it)."""
    return (
        select(*columns)
        .select_from(Contract)
        .join(ContractVersion, ContractVersion.id == Contract.current_authoritative_version_id)
        .join(ContractTextSnapshot, ContractTextSnapshot.id == ContractVersion.text_snapshot_id)
        .where(Contract.id.in_(contract_ids), ContractTextSnapshot.deleted_at.is_(None))
    )


def _text_lengths(db: Session, contract_ids: list[str]) -> dict[str, int]:
    """{contract id: length of its current text} for the contracts that have one. Read for
    these contracts only (it measures each text), never for the whole book."""
    if not contract_ids:
        return {}
    rows = db.execute(_current_text([Contract.id, func.char_length(ContractTextSnapshot.text)], contract_ids))
    return {cid: n or 0 for cid, n in rows.all()}


def _read_tokens(lengths: list[int], questions: int) -> tuple[int, int]:
    """(input, output) tokens reading these texts will take, about: each whole text (a
    token is about 4 characters, capped where the AI reads) plus a short answer per question."""
    tokens_in = sum(min(n, MAX_FULL_TEXT_CHARS) // 4 + _PROMPT_TOKENS for n in lengths)
    return tokens_in, len(lengths) * (150 + 250 * max(questions, 1))


def estimate_read_usd(db: Session, contract_ids: list[str], *, questions: int = 1) -> float:
    """What reading these contracts will cost about, priced with the AI Usage page's table
    for the tabular skill's model."""
    tokens = _read_tokens(list(_text_lengths(db, contract_ids).values()), questions)
    return token_cost(*tokens, settings.claude_model)


def _ask(question: str) -> str:
    return question if question.rstrip().endswith("?") else question.rstrip() + "?"


# (cell id, cell updated_at) -> the real contract span its quote aligns to, or None.
# ponytail: process-local and cleared when full; a re-read cell gets a new updated_at.
_SPANS: dict[tuple[str, str], str | None] = {}


_ELISION = re.compile(r"\s*(?:\[\s*(?:\.\.\.|…)\s*\]|\.\.\.|…)\s*")
# A yes needs real words behind it: a one-word quote ("automatically") is found in almost
# any contract. The main citation validator asks for a 12-word span; 6 is enough here
# because the span only has to show where the yes comes from.
_MIN_QUOTE_WORDS = 6


def _span_for(quote: str, text: str) -> str | None:
    """The real contract span a quote stands for, or None. An elided quote ("first part
    ... last part") verifies when each part of 3+ words aligns; the longest is shown."""
    parts = [p for p in _ELISION.split(quote) if len(p.split()) >= 3]
    if sum(len(p.split()) for p in parts) < _MIN_QUOTE_WORDS:
        return None
    spans = []
    for part in parts:
        real, score = align_citation_to_source(part, text)
        if score < GROUNDING_THRESHOLD:
            return None
        spans.append(" ".join(real.split()))
    return max(spans, key=len)


def _verified_quote(db: Session, cell: TabularReviewCell) -> str | None:
    """The contract's own words behind a "yes": the first quote that aligns to the
    contract's current text (the same alignment and threshold Ask Aegis grounds answers
    with), shown as the REAL span. None when no quote is in the text. The job keeps any
    quote the model writes, so a yes is never taken on the model's word."""
    key = (cell.id, str(cell.updated_at))
    if key in _SPANS:
        return _SPANS[key]
    text = db.scalar(_current_text([ContractTextSnapshot.text], [cell.contract_id])) or ""
    span = None
    for cit in cell.citations or []:
        quote = cit.get("quote") if isinstance(cit, dict) else None
        if quote and text:
            span = _span_for(quote, text)
            if span:
                break
    if len(_SPANS) > 4096:
        _SPANS.clear()
    _SPANS[key] = span
    return span


def _short(text: str) -> str:
    return text if len(text) <= 240 else text[:237] + "..."


def _cell_verdict(
    db: Session, question: str, cell: TabularReviewCell | None, *, lacks: bool, partly_read: bool = False
) -> _Verdict:
    """One wording question for one contract, decided from the cell's typed verdict.

    A "yes" counts only with a quote found in the contract. A "no" / "not_stated" needs
    none: an absence has nothing to quote, and the AI read the whole text, unless the text
    is longer than the AI reads (`partly_read`): then only a quoted yes settles anything.
    For a `lacks` question (the contract must NOT have it) the two swap."""
    if cell is None:
        return None, "not read yet"
    if cell.status == TabularCellStatus.FAILED:
        return None, "the AI could not read this contract"
    if cell.status not in (TabularCellStatus.COMPLETE, TabularCellStatus.NEEDS_REVIEW):
        return None, "still being read"
    # NEEDS_REVIEW only means the cell carries no quote: the verdict still decides.
    raw = cell.raw_ai_output if isinstance(cell.raw_ai_output, dict) else {}
    verdict = raw.get("verdict")
    if verdict == "yes":
        span = _verified_quote(db, cell)
        if span is None:
            return None, "the AI said yes but its quote is not in the contract — see the table"
        return (False, "") if lacks else (True, f"{_ask(question)} Yes — “{_short(span)}”")
    if verdict in ("no", "not_stated"):
        if partly_read:
            return None, f"too long to read in full (the AI reads the first {MAX_FULL_TEXT_CHARS:,} characters)"
        if lacks:
            return True, f"{_ask(question)} No — not found in the contract (the AI read all of it)"
        return False, ""
    return None, "the AI gave no yes/no answer"


_TERMINAL = {TabularCellStatus.COMPLETE, TabularCellStatus.NEEDS_REVIEW, TabularCellStatus.FAILED}


def _review_grid(db: Session, *, org_id: str, review_id: str | None):
    """([(column_id, question, lacks)] in column order, {(contract_id, column_id): cell})."""
    if not review_id:
        return [], {}
    columns = [
        c
        for c in db.scalars(
            select(TabularReviewColumn)
            .where(TabularReviewColumn.org_id == org_id, TabularReviewColumn.tabular_review_id == review_id)
            .order_by(TabularReviewColumn.position)
        ).all()
        # Only the filter's own yes/no columns: the answer links to the table, where a
        # person may add columns of their own.
        if (c.metadata_json or {}).get("answer_type") == "yes_no"
    ]
    # populate_existing: a session that already read these cells (the chat tool waits in
    # one) must see what the worker has written since, not its cached copy.
    cells = db.scalars(
        select(TabularReviewCell)
        .where(TabularReviewCell.org_id == org_id, TabularReviewCell.tabular_review_id == review_id)
        .execution_options(populate_existing=True)
    ).all()
    return (
        [(c.id, c.name, (c.metadata_json or {}).get("wanted") == "no") for c in columns],
        {(x.contract_id, x.column_id): x for x in cells},
    )


# --- the RESULT ---------------------------------------------------------------------------


def _title_key(item: dict) -> tuple[str, str]:
    return (item["title"] or "").lower(), item["contract_id"]


def _contract_key(c: Contract) -> tuple[str, str]:
    return (c.title or "").lower(), c.id


def _build(db: Session, *, org_id: str, state: dict, allowed: set[str] | None = None) -> dict:
    """RESULT from a state: the field outcomes (`matches`, `unknown`, `candidates` =
    contracts handed to the AI) plus whatever the review's cells say right now.
    `allowed` is the viewer's accessible set, when it is not the asker's."""

    def keep(cid: str) -> bool:
        return allowed is None or cid in allowed

    matches = [m for m in state["matches"] if keep(m["contract_id"])]
    unknown = [u for u in state["unknown"] if keep(u["contract_id"])]
    candidates = {cid: v for cid, v in (state.get("candidates") or {}).items() if keep(cid)}
    total = state["total"]
    if allowed is not None:
        # Contracts that did not match are not stored, so a viewer who cannot open one
        # of those still has it counted. Everything else drops out of the totals.
        stored = {x["contract_id"] for x in state["matches"] + state["unknown"]} | set(
            state.get("candidates") or {}
        )
        total -= len(stored) - len(matches) - len(unknown) - len(candidates)

    reading = None
    stored_reading = state.get("reading")
    if stored_reading:
        review_id = stored_reading.get("tabular_review_id")
        questions, grid = _review_grid(db, org_id=org_id, review_id=review_id)
        done = failed = 0
        for cid, info in candidates.items():
            outcomes = [
                _cell_verdict(db, q, grid.get((cid, col_id)), lacks=lacks, partly_read=bool(info.get("partly_read")))
                for col_id, q, lacks in questions
            ]
            cells = [grid.get((cid, col_id)) for col_id, _, _ in questions]
            if cells and all(x is not None and x.status in _TERMINAL for x in cells):
                done += 1
            if any(x is not None and x.status == TabularCellStatus.FAILED for x in cells):
                failed += 1
            if not outcomes:
                outcomes = [(None, "reading was not started")]
            if any(ok is False for ok, _ in outcomes):
                continue  # read, and it does not say what was asked
            reasons = [text for ok, text in outcomes if ok is None]
            if reasons:
                unknown.append(
                    {"contract_id": cid, "title": info["title"], "reason": _SEP.join(dict.fromkeys(reasons))}
                )
            else:
                matches.append(
                    {
                        "contract_id": cid,
                        "title": info["title"],
                        "counterparty_name": info["counterparty_name"],
                        "why": info["why"] + [text for _, text in outcomes],
                    }
                )
        reading = {
            "tabular_review_id": review_id,
            "contracts": len(candidates),
            "done": done,
            "failed": failed,
            "estimate_usd": stored_reading.get("estimate_usd", 0.0),
            "started": bool(stored_reading.get("started")),
        }

    matches.sort(key=_title_key)
    unknown.sort(key=_title_key)
    reasons = Counter(r for u in unknown for r in u["reason"].split(_SEP))
    not_checked = list(state["not_checked"])
    complete = not unknown and not not_checked
    result = {
        "answer_mode": "database_filter",
        "question": state["question"],
        "filter": state["filter"],
        "conditions": state["conditions"],
        "total": total,
        "checked": total - len(unknown),
        "matched": len(matches),
        "matches": matches,
        "unknown": unknown,
        "unknown_reasons": dict(reasons.most_common()),
        "not_checked": not_checked,
        "reading": reading,
        "complete": complete,
        "count_only": state["count_only"],
        "contract_ids": [m["contract_id"] for m in matches] + [u["contract_id"] for u in unknown],
        # Set by code: "high" only when every contract got a definite yes or no on every
        # condition and nothing in the question went unchecked.
        "confidence": "high" if complete else "medium",
    }
    if candidates:
        # Kept (beyond the shared shape) so the result can be rebuilt as the cells finish.
        result["candidates"] = candidates
    return result


def run_portfolio_filter(
    db: Session,
    *,
    user: User,
    flt: PortfolioFilter,
    question: str,
    not_checked: list[str] | None = None,
    today: date | None = None,
    start_reading: bool = True,
) -> dict:
    """Run a filter over EVERY contract the user can open and return the RESULT.

    Field conditions are decided here. Wording is read by the AI through ONE table review
    over the contracts that met the field conditions: that call commits (the review's jobs
    are dispatched), so nothing is held open while the AI works. `start_reading=False`
    stops short of it (tests, dry runs) and says so in the result.
    """
    today = today or utcnow().date()
    # The same set as accessible_contract_ids (org, not deleted, walls and clearance), with
    # the columns the checks read.
    contracts = list(
        db.scalars(
            select(Contract)
            .where(
                Contract.org_id == user.org_id,
                Contract.deleted_at.is_(None),
                accessible_contract_filter(user),
            )
            .options(
                load_only(
                    Contract.id, Contract.title, Contract.contract_type, Contract.lifecycle_stage,
                    Contract.counterparty_name, Contract.counterparty_id, Contract.jurisdiction,
                    Contract.risk_band, Contract.value_amount, Contract.currency,
                    Contract.effective_date, Contract.expiration_date, Contract.metadata_json,
                )
            )
        ).all()
    )
    checks, applied = _build_checks(db, flt, today=today, contracts=contracts)
    wording = [w.strip() for w in dict.fromkeys(flt.wording) if w and w.strip()]
    lacks = [w.strip() for w in dict.fromkeys(flt.lacks) if w and w.strip()]
    applied.wording, applied.lacks = wording, lacks
    asks = [(w, False) for w in wording] + [(w, True) for w in lacks]

    unknown: list[dict] = []
    survivors: list[tuple[Contract, list[str]]] = []
    for c in contracts:
        outcomes = [check(c) for _, check in checks]
        if any(ok is False for ok, _ in outcomes):
            continue
        reasons = [text for ok, text in outcomes if ok is None]
        if reasons:
            unknown.append(
                {"contract_id": c.id, "title": c.title, "reason": _SEP.join(dict.fromkeys(reasons))}
            )
        else:
            survivors.append((c, [text for _, text in outcomes]))

    state = {
        "question": question,
        "filter": applied.model_dump(mode="json"),
        "conditions": [label for label, _ in checks]
        + [f"AI reads each contract: {_ask(w)}" + (" — must be no" if no else "") for w, no in asks],
        "total": len(contracts),
        "matches": [],
        "unknown": unknown,
        "not_checked": [*(not_checked or []), *(x.strip() for x in flt.not_checkable if x and x.strip())],
        "count_only": flt.count_only,
        "candidates": None,
        "reading": None,
    }

    if not asks:
        state["matches"] = [
            {"contract_id": c.id, "title": c.title, "counterparty_name": c.counterparty_name, "why": why}
            for c, why in survivors
        ]
        return _build(db, org_id=user.org_id, state=state)

    lengths = _text_lengths(db, [c.id for c, _ in survivors])
    has_text = set(lengths)
    readable = sorted((s for s in survivors if s[0].id in has_text), key=lambda s: _contract_key(s[0]))
    to_read, beyond = readable[:MAX_REVIEW_CONTRACTS], readable[MAX_REVIEW_CONTRACTS:]
    for c, _ in survivors:
        if c.id not in has_text:
            unknown.append({"contract_id": c.id, "title": c.title, "reason": _NO_DOCUMENT})
    for c, _ in beyond:
        unknown.append(
            {
                "contract_id": c.id,
                "title": c.title,
                "reason": f"not read: {MAX_REVIEW_CONTRACTS}-contract limit per question",
            }
        )
    if beyond:
        state["not_checked"].append(
            f"the AI read {len(to_read)} of the {len(readable)} contracts that met the other "
            f"conditions ({MAX_REVIEW_CONTRACTS}-contract limit per question)"
        )
    if not to_read:
        return _build(db, org_id=user.org_id, state=state)

    read_ids = [c.id for c, _ in to_read]
    state["candidates"] = {
        c.id: {
            "title": c.title,
            "counterparty_name": c.counterparty_name,
            "why": why,
            # The AI reads the first MAX_FULL_TEXT_CHARS: a "not found" past that is no answer.
            **({"partly_read": True} if lengths[c.id] > MAX_FULL_TEXT_CHARS else {}),
        }
        for c, why in to_read
    }
    tokens_in, tokens_out = _read_tokens([lengths[cid] for cid in read_ids], len(asks))
    estimate = round(token_cost(tokens_in, tokens_out, settings.claude_model), 2)
    review_id = _open_reading(db, user=user, asks=asks, read_ids=read_ids) if start_reading else None
    if review_id is not None:
        estimate = 0.0  # the same reading is already under way: nothing new is paid
    elif not start_reading:
        state["not_checked"].append("the AI reading was not started")
    elif not has_permission(org_access.effective_permission_values(db, user=user), "assistant:use_ai_tools"):
        # Reading spends money; it needs the same permission as starting a table review.
        state["not_checked"].append("the AI reading needs the AI tools permission (ask an admin)")
    elif (left := tokens_left(user.org_id)) is not None and tokens_in + tokens_out > left:
        # Started anyway it would use up the org's daily cap, and every other AI feature
        # would then fail until tomorrow.
        state["not_checked"].append(
            f"the AI reading (about {tokens_in + tokens_out:,} tokens, ${estimate:.2f}) is more than "
            f"what is left of today's AI budget ({left:,} tokens)"
        )
    else:
        review_id = _start_reading(db, user=user, question=question, asks=asks, read_ids=read_ids, estimate=estimate)
        if review_id is None:
            state["not_checked"].append("the AI reading could not be started")
    state["reading"] = {"tabular_review_id": review_id, "estimate_usd": estimate, "started": review_id is not None}
    return _build(db, org_id=user.org_id, state=state)


# A re-ask within this window shows the reading already under way instead of paying again.
_REUSE_WINDOW = timedelta(hours=1)


def _column_spec(asks: list[tuple[str, bool]]) -> list[tuple[str, str]]:
    return [(f"{_ask(w)} {_YES_NO_RULE}", "no" if no else "yes") for w, no in asks]


def _open_reading(db: Session, *, user: User, asks: list[tuple[str, bool]], read_ids: list[str]) -> str | None:
    """A reading of the same questions over the same contracts that this user started in
    the last hour. Asking again (a chat retry, "is it done?", the question rephrased into
    the same filter) then follows that reading as it fills instead of starting a second."""
    reviews = db.scalars(
        select(TabularReview)
        .where(
            TabularReview.org_id == user.org_id,
            TabularReview.created_by_user_id == user.id,
            TabularReview.deleted_at.is_(None),
            TabularReview.created_at >= utcnow() - _REUSE_WINDOW,
        )
        .order_by(TabularReview.created_at.desc())
    ).all()
    want = _column_spec(asks)
    for review in reviews:
        if (review.metadata_json or {}).get("source") != "portfolio_question":
            continue
        if sorted(review.source_contract_ids or []) != sorted(read_ids):
            continue
        columns = db.scalars(
            select(TabularReviewColumn)
            .where(TabularReviewColumn.tabular_review_id == review.id)
            .order_by(TabularReviewColumn.position)
        ).all()
        have = [(c.prompt, (c.metadata_json or {}).get("wanted")) for c in columns
                if (c.metadata_json or {}).get("answer_type") == "yes_no"]
        if have == want:
            return review.id
    return None


def _start_reading(
    db: Session, *, user: User, question: str, asks: list[tuple[str, bool]], read_ids: list[str], estimate: float
):
    payload = SimpleNamespace(
        name=f"Question: {question}"[:255],
        contract_ids=read_ids,
        columns=[SimpleNamespace(name=w[:255], prompt=prompt) for (w, _), (prompt, _) in zip(asks, _column_spec(asks), strict=True)],
    )
    try:
        review = TabularReviewService(db).create_review(
            payload=payload,
            current_user=user,
            metadata_json={"source": "portfolio_question", "question": question},
            column_metadata=[{"answer_type": "yes_no", "wanted": "no" if no else "yes"} for _, no in asks],
        )
    except HTTPException:
        db.rollback()
        return None
    # The review commits as it dispatches its jobs; the audit row (which takes the app-wide
    # audit lock until commit) is written after that and committed at once.
    write_audit_log(
        db,
        action="contract_brain.portfolio_reading_started",
        resource_type="tabular_review",
        resource_id=review.id,
        org_id=user.org_id,
        actor_user_id=user.id,
        after={"question": question, "contracts": len(read_ids), "questions": len(asks), "estimate_usd": estimate},
    )
    db.commit()
    return review.id


def portfolio_result(db: Session, *, user: User, meta: dict) -> dict:
    """The RESULT as it stands now, from a stored RESULT and the review's current cells.
    Contracts the user cannot open are dropped and the totals reduced."""
    state = {
        "question": meta.get("question", ""),
        "filter": meta.get("filter") or {},
        "conditions": meta.get("conditions") or [],
        "total": meta.get("total", 0),
        "matches": meta.get("matches") or [],
        "unknown": meta.get("unknown") or [],
        "not_checked": meta.get("not_checked") or [],
        "count_only": bool(meta.get("count_only")),
        "candidates": meta.get("candidates"),
        "reading": meta.get("reading"),
    }
    cands = state["candidates"] or {}
    # Contracts being read are in `unknown` ("still being read") in the stored copy; the
    # cells decide them again here.
    state["unknown"] = [u for u in state["unknown"] if u["contract_id"] not in cands]
    state["matches"] = [m for m in state["matches"] if m["contract_id"] not in cands]
    return _build(db, org_id=user.org_id, state=state, allowed=accessible_contract_ids(db, user=user))


def reading_finished(result: dict) -> bool:
    """No AI reading outstanding: none was needed, or every contract's cells are final."""
    reading = result.get("reading")
    return not reading or reading["done"] >= reading["contracts"] or not reading["started"]


# --- the answer text ------------------------------------------------------------------------


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def answer_text(result: dict) -> str:
    """The answer as markdown. Written by code from the RESULT; no model writes it."""
    total, matched = result["total"], result["matched"]
    if result["conditions"]:
        head = f"**{matched} of {_n(total, 'contract')} match:** " + _SEP.join(result["conditions"]) + "."
    else:
        head = f"**{_n(total, 'contract')}** in your portfolio."
    parts = [head, f"Checked {result['checked']} of {_n(total, 'contract')}."]
    if result["matches"]:
        lines = []
        for i, m in enumerate(result["matches"], 1):
            party = f" ({m['counterparty_name']})" if m.get("counterparty_name") else ""
            lines.append(f"{i}. **{m['title']}**{party}")
            lines.extend(f"   - {why}" for why in m["why"])
        parts.append("\n".join(lines))
    if result["unknown"]:
        reasons = _SEP.join(f"{n} {reason}" for reason, n in result["unknown_reasons"].items())
        parts.append(f"Could not check ({len(result['unknown'])}): {reasons}.")
    if result["not_checked"]:
        parts.append("Not checked: " + _SEP.join(result["not_checked"]) + ".")
    reading = result["reading"]
    if reading:
        flt = result["filter"]
        asked = _SEP.join(f"“{_ask(q)}”" for q in [*(flt.get("wording") or []), *(flt.get("lacks") or [])])
        cost = f"about ${reading['estimate_usd']:.2f}"
        n = reading["contracts"]
        if not reading["started"]:
            parts.append(f"Reading was not started: {_n(n, 'contract')} would be read for {asked} ({cost}).")
        elif reading["done"] < n:
            parts.append(
                f"The AI is reading {_n(n, 'contract')} for: {asked} ({reading['done']} of {n} done, {cost})."
            )
        else:
            parts.append(f"The AI read {_n(n, 'contract')} for: {asked} ({cost}).")
    return "\n\n".join(parts)


def mark_search_partial(answer: dict, *, contract_count: int) -> dict:
    """A portfolio answer that came from SEARCH looked at the best-matching passages, not
    every contract. Say so, and never leave it above "medium" confidence ("low" stays)."""
    if contract_count <= 1:
        return answer  # one contract in scope: sampling left nothing out
    note = "Looked at the best-matching passages, not every contract — for a complete list ask “which contracts …”."
    out = dict(answer)
    out["limitations"] = f"{out['limitations']} {note}" if out.get("limitations") else note
    if out.get("confidence") == "high":
        out["confidence"] = "medium"
    return out
