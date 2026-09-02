"""Entity resolution for the knowledge graph.

Turning "Nexus Legal Technologies, Inc." and "Nexus Legal Technologies Inc"
into one organisation is what makes portfolio questions — exposure by
counterparty, who else signed this clause — possible at all.

Deliberately conservative: exact match after normalisation, no fuzzy merging.
Merging two organisations that merely *look* alike would silently corrupt
exposure figures, and a wrong number a lawyer trusts is worse than two rows
they can see are duplicates. `suggest_merges` reports near-matches for a human
to confirm instead.
"""

from __future__ import annotations

import re

# Company-form suffixes stripped before comparison. Order matters: longer forms
# first so "private limited" is removed before "limited".
_SUFFIXES = (
    "private limited", "pvt ltd", "pvt. ltd.", "pvt", "public limited company",
    "limited liability partnership", "limited liability company",
    "incorporated", "corporation", "company", "limited",
    "llp", "llc", "ltd", "inc", "plc", "gmbh", "ag", "bv", "nv", "sa", "sas",
    "srl", "spa", "oy", "ab", "as", "aps", "pte", "pty", "co",
)

# Names that are placeholders, not organisations. Real data from this codebase:
# 17 contracts carry "the Counterparty" or "Counterparty" as the party name.
_PLACEHOLDERS = {
    "counterparty", "the counterparty", "party", "the party", "other party",
    "company", "the company", "supplier", "the supplier", "vendor", "the vendor",
    "client", "the client", "customer", "the customer", "contractor",
    "n/a", "n a", "na", "none", "tbd", "tbc", "unknown", "not applicable", "test",
}

# A name that is nothing but a company form ("Ltd", "GmbH") is not an entity.
_FORMS_ONLY = frozenset(_SUFFIXES)

_PUNCT = re.compile(r"[.,;:'\"()\[\]&/\\-]+")
_WS = re.compile(r"\s+")


def normalize_org_name(raw: str | None) -> str | None:
    """A comparison key for an organisation name, or None if it isn't one.

    Returns None for placeholders and for anything that normalises to nothing,
    so junk never becomes a graph entity.
    """
    if not raw:
        return None
    name = _PUNCT.sub(" ", raw.lower())
    name = _WS.sub(" ", name).strip()
    # "M/s Acme" is a common Indian prefix for a firm name
    for prefix in ("m s ", "m/s ", "messrs "):
        if name.startswith(prefix):
            name = name[len(prefix):]
    # "L.L.P." arrives here as "l l p"; collapse runs of single letters so it
    # matches the "llp" suffix rather than surviving as three tokens.
    name = re.sub(r"\b(?:[a-z] ){1,}[a-z]\b",
                  lambda m: m.group(0).replace(" ", ""), name)
    if name in _PLACEHOLDERS or name in frozenset(_SUFFIXES):
        return None

    # strip one trailing company form, repeatedly (e.g. "acme india pvt ltd")
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if name.endswith(" " + suffix):
                trimmed = name[: -(len(suffix) + 1)].strip()
                if trimmed:                      # never strip away the whole name
                    name, changed = trimmed, True
                    break
    name = _WS.sub(" ", name).strip()
    if not name or name in _PLACEHOLDERS or name in _FORMS_ONLY or len(name) < 2:
        return None
    return name


def party_entity_key(raw: str | None) -> str | None:
    """The graph key for an organisation, or None if the name is unusable."""
    normalized = normalize_org_name(raw)
    return f"org:{normalized}" if normalized else None


def suggest_merges(names: list[str], threshold: int = 90) -> list[tuple[str, str, int]]:
    """Near-duplicate organisation names for a human to confirm.

    Never merges automatically — "Contoso Consulting" and "Contoso Cloud" score
    highly and are different companies. Returns (a, b, score), best first.
    """
    keys = {}
    for raw in names:
        key = normalize_org_name(raw)
        if key:
            keys.setdefault(key, raw)
    distinct = sorted(keys)
    try:
        from rapidfuzz import fuzz
    except ImportError:                                # pragma: no cover
        return []
    out: list[tuple[str, str, int]] = []
    for i, a in enumerate(distinct):
        for b in distinct[i + 1:]:
            score = int(fuzz.token_sort_ratio(a, b))
            if score >= threshold:
                out.append((keys[a], keys[b], score))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


# ---- people ---------------------------------------------------------------

# Generic role words that are not a person's name.
_PERSON_PLACEHOLDERS = {
    "signatory", "signer", "approver", "counsel", "legal", "admin",
    "authorised signatory", "authorized signatory", "n/a", "na", "none",
    "unknown", "test", "user", "the signatory",
}


def normalize_person_name(raw: str | None) -> str | None:
    """A display-safe person name, or None if it is a placeholder/blank."""
    if not raw:
        return None
    name = _WS.sub(" ", raw.strip())
    if name.lower() in _PERSON_PLACEHOLDERS or len(name) < 2:
        return None
    return name


def person_entity_key(*, email: str | None = None, name: str | None = None) -> str | None:
    """One key per real person. Email is the identity where present — it is
    stable across name spellings ("J. Doe" / "Jane Doe"); the name is only a
    fallback, and a placeholder name yields no key at all.
    """
    if email and email.strip():
        return f"person:{email.strip().lower()}"
    normalized = normalize_person_name(name)
    return f"person:name:{normalized.lower()}" if normalized else None


# ---- jurisdictions --------------------------------------------------------

# Noise around a jurisdiction name that shouldn't split it into two entities.
_JURIS_STRIP = re.compile(
    r"\b(the )?(state|commonwealth|province|republic) of\b", re.I)
_JURIS_TRAIL = re.compile(r",?\s*(usa|u\.s\.a\.|us|uk|u\.k\.)\.?$", re.I)


def normalize_jurisdiction(raw: str | None) -> str | None:
    """Collapse 'State of Delaware', 'Delaware, USA' and 'Delaware' to one key.
    Kept conservative: it strips governmental prefixes and a country suffix,
    nothing more — 'New York' and 'New York City' stay distinct."""
    if not raw:
        return None
    name = _JURIS_STRIP.sub("", raw)
    name = _JURIS_TRAIL.sub("", name)
    name = _WS.sub(" ", _PUNCT.sub(" ", name.lower())).strip()
    if not name or name in _PLACEHOLDERS or len(name) < 2:
        return None
    return name


def jurisdiction_entity_key(raw: str | None) -> str | None:
    n = normalize_jurisdiction(raw)
    return f"jurisdiction:{n}" if n else None


# ---- provenance -----------------------------------------------------------

def clause_for_quote(quote: str | None, clauses: list) -> object | None:
    """The clause whose text contains this obligation's source quote, or None.

    ``clauses`` is a list of (handle, text) pairs; the handle is returned as-is
    (a graph node, an id — this stays agnostic). Matching is whitespace- and
    case-insensitive verbatim containment: the quote is an exact span from the
    contract, so a clause that contains it IS the source. No fuzzy matching —
    a wrong provenance link is worse than a missing one.
    """
    if not quote:
        return None
    key = " ".join(quote.split()).lower()
    if len(key) < 12:                       # too short to attribute confidently
        return None
    for handle, text in clauses:
        if key in " ".join((text or "").split()).lower():
            return handle
    return None
