"""Build the exam's content from public, lawyer-labelled data. No AI call, no network after the
downloads, nothing paid (specs/005-ai-eval-baseline T008, FR-2, FR-4).

Part 1 (the top of this file) builds the public sets:

* CUAD v1 (CC BY 4.0): the 40 contracts above 25,000 characters with the most labelled passages
  past 25,000, used twice - *review* (8 lawyer-labelled "this provision is present" categories)
  and *extraction* (parties, dates, governing law and four clause passages).
* ContractNLI 1.0 (CC BY 4.0): 10 NDAs x 17 lawyer verdicts (supported / contradicted / not
  mentioned) -> review.
* LegalBench-RAG (MIT): 100 questions that come with the exact passage that answers them, asked
  of the same 40 CUAD contracts so each contract is ingested once.

Every answer key here is a lawyer's label from the dataset; nothing in this file asks a model.
Selection is deterministic: ties are broken by ``sha256(SEED:name)``, never by an RNG whose
sequence could change between Python versions.

Part 2 (the constructed contracts, T012) is further down this file. It builds, with no model and
no network: Common Paper CSA/DPA documents with planted changes, the AEGIS drafting templates
filled with Indian values (the app's own ``render_document``) with planted changes, long documents
whose changes sit past 25,000 characters, scanned twins, and the 40-contract portfolio with its
questions. The severity of every planted change is copied from the platform playbook library
(``app/playbooks/library.py``) at build time; no model ever sets one (FR-5, D-9).
``documents.json`` and ``items/review.json`` hold the public and the constructed sets together, so
only ``write_constructed`` writes them (``write_public`` writes the purely public files).

Raw downloads live in the git-ignored ``eval/contracts/raw/`` (``RAW_DIR``); only the generated
JSON under ``content/`` is committed.
"""

import hashlib
import html
import json
import re
import shutil
import textwrap
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

from app.contract_brain.entities import normalize_jurisdiction, normalize_org_name
from eval.exam.content import (
    CONTENT_DIR,
    CUTOFF,
    DOCS_DIR,
    RAW_DIR,
    Dataset,
    DatasetFile,
    ExamDocument,
    ExtractionItem,
    FieldKey,
    PlantedChange,
    PortfolioQuestion,
    QAItem,
    ReviewItem,
    ReviewRuleKey,
    SourceCategory,
)

SEED = "aegis-exam-1"
N_CUAD, N_NDA, N_QA = 40, 10, 100
N_NDA_LONGEST = 4  # the 4 longest NDAs, then the rest by seeded rank
QA_PER_DOC = 3
QA_PAST_SHARE = 0.5  # target share with the passage past 25k; before-25k ones are scarcer
MAX_PARTIES = 6  # more named parties than this is a consortium deal, not a scorable key
PASSAGE_MAX = 1_500  # LegalBench-RAG answers longer than this (in total) are skipped
QUOTE_MAX = 1_500  # a stored quote is a prefix of the exact text, so it still occurs in it

# --- where the raw data comes from ------------------------------------------------------------
# Pinned to commits where GitHub offers one, so a later upstream edit cannot change the exam
# without the file hashes in datasets.json changing too (a changed hash changes the content
# fingerprint: loud, never silent).
CUAD_URL = (
    "https://github.com/The-Atticus-Project/cuad/raw/67faa0e6023b04fcaae6cc09497ab00e5d63a2a2/"
    "data.zip"
)
CNLI_URL = "https://stanfordnlp.github.io/contract-nli/resources/contract-nli.zip"
LBR_URL = (
    "https://www.dropbox.com/scl/fo/r7xfa5i3hdsbxex1w6amw/AID389Olvtm-ZLTKAPrw6k4"
    "?rlkey=5n8zrbk4c08lbit3iiexofmwg&st=0hu354cq&dl=1"
)
_CP = "https://raw.githubusercontent.com/CommonPaper"
COMMONPAPER_FILES = {  # downloaded here; part 2 reads them from RAW_DIR
    "commonpaper/CSA.md": f"{_CP}/CSA/3efceb01e3c8bfb7b302a5f34975aa2b084026a3/CSA.md",
    "commonpaper/DPA.md": f"{_CP}/DPA/9f1c40d3fb0bb900397a919d058a9c0870f6b0f0/DPA.md",
    "commonpaper/Mutual-NDA.md": (
        f"{_CP}/Mutual-NDA/2a3068b6c0ab6015c440f541d5b215bc3ac2f4cb/Mutual-NDA.md"
    ),
}

CUAD_JSON = "cuad/CUADv1.json"
CNLI_JSON = "cnli/contract-nli/test.json"
CNLI_LICENCE_FILE = "cnli/contract-nli/LICENSE"
LBR_JSON = "lbr/benchmarks/cuad.json"
LBR_CORPUS = "lbr/corpus/cuad"

# (archive, url, folder under RAW_DIR, members to keep). The ContractNLI archive also holds ~300
# original PDFs and the training split; the Dropbox one holds three corpora the exam does not
# use. Keeping only what is read keeps the raw folder small.
_ARCHIVES: tuple[tuple[str, str, str, Callable[[str], bool]], ...] = (
    ("archives/cuad-data.zip", CUAD_URL, "cuad", lambda n: n == "CUADv1.json"),
    (
        "archives/contract-nli.zip",
        CNLI_URL,
        "cnli",
        lambda n: n in ("contract-nli/test.json", "contract-nli/LICENSE"),
    ),
    (
        "archives/legalbench-rag.zip",
        LBR_URL,
        "lbr",
        lambda n: n == "benchmarks/cuad.json" or n.startswith("corpus/cuad/"),
    ),
)
EXTRACTED = ".extracted"  # written last, so an interrupted unpack is redone, not trusted


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "aegis-exam-build"})
    with urllib.request.urlopen(request, timeout=120) as resp, part.open("wb") as out:
        shutil.copyfileobj(resp, out)
    part.replace(dest)


def _extract(archive: Path, dest: Path, keep: Callable[[str], bool]) -> None:
    """Unpack the wanted members of a downloaded archive, refusing any path that escapes ``dest``.

    The archives come from the internet: a member named ``../x`` or ``/etc/x`` must not be able
    to write outside the raw folder. Directory entries (and the bare "/" entry the Dropbox
    archive carries) hold no data and are skipped.
    """
    root = dest.resolve()
    kept = 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.strip("/")
            if info.is_dir() or not name or not keep(name):
                continue
            target = (dest / name).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"{archive.name}: member {info.filename!r} escapes {dest}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            kept += 1
    if not kept:
        # An upstream re-layout must show up as a problem on every run, not as a marker that
        # makes the next run skip the dataset and report its set silently missing.
        raise ValueError(f"{archive.name}: none of the files the exam reads are in the archive")
    (dest / EXTRACTED).write_text("", encoding="utf-8")


def download_datasets(
    raw_dir: Path = RAW_DIR, *, fetch: Callable[[str, Path], None] = _fetch
) -> list[str]:
    """Fetch what is missing under ``raw_dir`` (free public downloads, ~200 MB). Returns one line
    per dataset that could not be fetched; the build then reports that set missing rather than
    substituting anything (spec edge case, FR-4).

    An archive already on disk is reused and a folder that is already complete is left alone, so
    re-running costs nothing.
    """
    problems: list[str] = []
    for archive, url, folder, keep in _ARCHIVES:
        if (raw_dir / folder / EXTRACTED).is_file():
            continue
        try:
            if not (raw_dir / archive).is_file():
                fetch(url, raw_dir / archive)
            _extract(raw_dir / archive, raw_dir / folder, keep)
        except zipfile.BadZipFile as exc:
            # An error page saved as the archive would otherwise be reused, and fail, forever.
            (raw_dir / archive).unlink(missing_ok=True)
            problems.append(f"{folder}: {exc}")
        except (OSError, ValueError) as exc:
            problems.append(f"{folder}: {exc}")
    for rel, url in COMMONPAPER_FILES.items():
        if (raw_dir / rel).is_file():
            continue
        try:
            fetch(url, raw_dir / rel)
        except OSError as exc:
            problems.append(f"commonpaper: {exc}")
    return problems


# --- the dataset registry (FR-2) ---------------------------------------------------------------


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _cp_version(raw_dir: Path) -> str:
    """The version each Common Paper file prints in its own link to the standard terms. The DPA
    file prints none (its repository README says "Version 1"), hence the fallback."""
    parts = []
    for label, rel, fallback in (
        ("CSA", "commonpaper/CSA.md", "unknown"),
        ("DPA", "commonpaper/DPA.md", "1.0"),
        ("Mutual NDA", "commonpaper/Mutual-NDA.md", "unknown"),
    ):
        path = raw_dir / rel
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        found = re.search(r"commonpaper\.com/standards/[a-z-]+/(\d+\.\d+)", text)
        parts.append(f"{label} {found.group(1) if found else fallback}")
    return "; ".join(parts)


def dataset_registry(raw_dir: Path = RAW_DIR) -> list[Dataset]:
    """Name, version, licence and the sha256 of every raw file the exam reads.

    The archives themselves are not hashed: the Dropbox one is re-zipped on every download, so
    its hash would change the content fingerprint without any content changing. The extracted
    files are what the exam reads.
    """

    def make(key, name, version, licence, url, rels) -> Dataset:
        listed = [DatasetFile(path=r, sha256=_sha256_file(raw_dir / r)) for r in rels]
        return Dataset(
            key=key,
            name=name,
            version=version,
            licence=licence,
            url=url,
            files=listed,
            available=all(f.sha256 for f in listed),
        )

    lbr_sha = _sha256_file(raw_dir / LBR_JSON)
    return [
        make(
            "cuad", "CUAD", "v1", "CC BY 4.0", "https://github.com/TheAtticusProject/cuad",
            [CUAD_JSON],
        ),
        make(
            "contractnli", "ContractNLI", "1.0", "CC BY 4.0",
            "https://stanfordnlp.github.io/contract-nli/", [CNLI_JSON, CNLI_LICENCE_FILE],
        ),
        make(
            "legalbench_rag", "LegalBench-RAG",
            f"release sha256:{lbr_sha[:12]}" if lbr_sha else "unavailable",
            "MIT (built on CC BY 4.0 sources)",
            "https://github.com/zeroentropy-ai/legalbenchrag", [LBR_JSON],
        ),
        make(
            "commonpaper", "Common Paper standard agreements", _cp_version(raw_dir),
            "CC BY 4.0", "https://commonpaper.com/standards/", list(COMMONPAPER_FILES),
        ),
    ]


# --- small helpers -----------------------------------------------------------------------------


def _rank(name: str) -> str:
    return hashlib.sha256(f"{SEED}:{name}".encode()).hexdigest()


def _past(start: int) -> bool:
    """Past 25k: the supporting text starts beyond the first 25,000 characters."""
    return start >= CUTOFF


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _clip(text: str) -> str:
    return text.strip()[:QUOTE_MAX].rstrip()


def _source(d: Dataset) -> str:
    return f"{d.name} {d.version}"


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _rule(clause_type: str, preferred: str, rationale: str) -> dict:
    """A playbook rule in the platform's own column names (``app.playbooks.library.rule``), so
    the exam database can load it as a ``PlaybookRule`` row. ``risk_level`` stays None: a public
    checklist has no lawyer-set severity, and one is never made up (FR-5)."""
    return {
        "clause_type": clause_type,
        "rule_type": "standard_position",
        "preferred_position": preferred,
        "fallback_position": None,
        "required_language": None,
        "prohibited_language": None,
        "risk_level": None,
        "rationale": rationale,
        "negotiation_guidance": None,
        "approval_required": False,
        "escalation_role": None,
        "sample_clause": None,
    }


# --- CUAD: review checklist + extraction keys --------------------------------------------------

# CUAD category -> the exam playbook rule it becomes (review). A labelled span means the
# contract has the provision, which the checklist treats as a deviation.
CUAD_REVIEW_RULES = {
    "Uncapped Liability": "uncapped_liability",
    "Non-Compete": "non_compete",
    "Exclusivity": "exclusivity",
    "Most Favored Nation": "most_favored_nation",
    "Liquidated Damages": "liquidated_damages",
    "Irrevocable Or Perpetual License": "irrevocable_or_perpetual_license",
    "Covenant Not To Sue": "covenant_not_to_sue",
    "Joint Ip Ownership": "joint_ip_ownership",
}
# Extraction fields read straight from a labelled passage: field -> CUAD category.
PASSAGE_FIELDS = {
    "renewal_term": "Renewal Term",
    "notice_period": "Notice Period To Terminate Renewal",
    "liability_cap": "Cap On Liability",
    "termination_for_convenience": "Termination For Convenience",
}
# D-14: notice_period is the notice to end the contract OR to stop it renewing, but CUAD labels only
# the renewal one. A contract with one of these clauses and no notice label may well state a notice
# there, so its key would be unknown, not "not stated".
_NOTICE_MAY_SIT_IN = ("Termination For Convenience", "Renewal Term")
_EXTRACTION_CATEGORIES = (
    "Parties", "Agreement Date", "Effective Date", "Expiration Date", "Governing Law",
    *PASSAGE_FIELDS.values(),
)
_CUAD_CATEGORY = re.compile(r'related to "([^"]+)"')
_CUAD_DETAILS = re.compile(r"Details:\s*(.*)$", re.DOTALL)


@dataclass(frozen=True)
class _Span:
    start: int
    text: str


def _spans_by_category(paragraph: dict) -> tuple[dict[str, list[_Span]], dict[str, str], int]:
    """Labelled spans per CUAD category (earliest first), each category's own definition text,
    and how many labels could not be found in the contract text and were dropped.

    A label is trusted at its ``answer_start`` if the text is really there, else located by
    exact search; one that cannot be located at all is dropped and counted, never guessed.
    """
    context = paragraph["context"]
    spans: dict[str, list[_Span]] = {}
    details: dict[str, str] = {}
    dropped = 0
    for qa in paragraph["qas"]:
        category = _CUAD_CATEGORY.search(qa["question"])
        if not category:
            continue
        name = category.group(1)
        found = _CUAD_DETAILS.search(qa["question"])
        details.setdefault(name, " ".join(found.group(1).split()) if found else "")
        for answer in qa["answers"]:
            text = answer["text"].strip()
            if not text:
                continue
            start = answer["answer_start"]
            if not context.startswith(text, start):
                start = context.find(text)
            if start < 0:
                dropped += 1
                continue
            spans.setdefault(name, []).append(_Span(start, text))
    for found_spans in spans.values():
        found_spans.sort(key=lambda s: (s.start, s.text))
    return spans, details, dropped


# A date is only accepted whole (month, day and year all written out): a key built from
# "June , 2010" or "September 1999" would have to invent a day.
_MONTH = (
    r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_MONTH_NUMBER = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"((?:1[89]|20)\d{2})"
_MDY = re.compile(rf"\b{_MONTH}\s+{_DAY}\s*,?\s*{_YEAR}\b", re.IGNORECASE)
_DMY = re.compile(rf"\b{_DAY}\s+(?:day\s+)?(?:of\s+)?{_MONTH}\s*,?\s*{_YEAR}\b", re.IGNORECASE)


def parse_full_date(text: str) -> str | None:
    """``YYYY-MM-DD`` when ``text`` holds exactly one complete calendar date, else None."""
    flat = " ".join(text.split())
    found: set[str] = set()
    for pattern, day_group, month_group in ((_MDY, 2, 1), (_DMY, 1, 2)):
        for m in pattern.finditer(flat):
            month = _MONTH_NUMBER[m.group(month_group)[:3].lower()]
            try:
                found.add(date(int(m.group(3)), month, int(m.group(day_group))).isoformat())
            except ValueError:  # "February 30, 2001": a typo in the contract is not a date
                return None
    return next(iter(found)) if len(found) == 1 else None


# Jurisdictions a governing-law clause is checked against. Longest names first so "West
# Virginia" beats "Virginia" and "England and Wales" beats "England".
_JURISDICTIONS = sorted(
    [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
        "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
        "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
        "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "Ohio",
        "Oklahoma", "Oregon", "Pennsylvania", "Tennessee", "Texas", "Utah", "Vermont",
        "Virginia", "Washington", "Wisconsin", "Wyoming",
        "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
        "North Dakota", "Rhode Island", "South Carolina", "South Dakota", "West Virginia",
        "District of Columbia", "Washington, D.C.", "Puerto Rico", "United States of America",
        "United States", "England and Wales", "England", "Scotland", "Ireland",
        "Northern Ireland", "United Kingdom", "Singapore", "Hong Kong", "Japan", "India",
        "People's Republic of China", "China", "Korea", "Taiwan", "Australia",
        "New South Wales", "Victoria", "New Zealand", "Canada", "Ontario", "British Columbia",
        "Quebec", "Alberta", "Germany", "France", "Switzerland", "Netherlands", "Belgium",
        "Luxembourg", "Sweden", "Denmark", "Norway", "Finland", "Italy", "Spain", "Austria",
        "Israel", "Brazil", "Mexico", "Russia", "South Africa", "Cayman Islands",
        "British Virgin Islands", "Bermuda",
    ],
    key=len,
    reverse=True,
)
_JURISDICTION = re.compile(
    r"\b(" + "|".join(re.escape(j) for j in _JURISDICTIONS) + r")\b", re.IGNORECASE
)
_LAW_CUE = re.compile(r"\blaws?\s+of\b", re.IGNORECASE)


def parse_jurisdiction(passage: str) -> str | None:
    """The jurisdiction a governing-law clause names, normalised, or None (a redacted "[***]",
    or a clause naming none we know). Looks after the first "law(s) of", so "...the Lanham Act
    and the laws of the State of Texas" gives Texas."""
    flat = " ".join(passage.split())
    cue = _LAW_CUE.search(flat)
    found = (cue and _JURISDICTION.search(flat, cue.end())) or _JURISDICTION.search(flat)
    return normalize_jurisdiction(found.group(1)) if found else None


# Role words, pronouns and sentence words that mark a "Parties" label as not being a name.
_NOT_A_NAME = {
    "party", "parties", "company", "distributor", "supplier", "vendor", "customer", "client",
    "licensee", "licensor", "seller", "buyer", "purchaser", "provider", "contractor",
    "consultant", "franchisor", "franchisee", "manufacturer", "agent", "principal", "owner",
    "lessee", "lessor", "borrower", "lender", "employee", "employer", "reseller", "sponsor",
    "affiliate", "partner", "investor", "collaborator", "we", "us", "our", "you", "your",
}
_SENTENCE_WORDS = {
    "referred", "hereinafter", "shall", "may", "means", "herein", "agreement", "collectively",
    "individually", "each", "between", "among", "and", "or", "doing",
}
_FORM_TOKENS = {
    "inc", "corp", "co", "ltd", "llc", "plc", "llp", "lp", "gmbh", "ag", "sa", "bv", "nv", "pvt",
    "pte", "pty", "as", "ab", "oy", "srl", "spa", "sas", "aps",
}


def _tokens(name: str) -> list[str]:
    return (normalize_org_name(name) or "").split()


def _core(name: str) -> tuple[str, ...]:
    """The name without its trailing legal form(s): the part that identifies the company."""
    tokens = _tokens(name)
    while tokens and tokens[-1] in _FORM_TOKENS:
        tokens.pop()
    return tuple(tokens)


def _party_candidate(raw: str) -> str | None:
    """A cleaned organisation name from one "Parties" label, or None when the label is a role
    word, a pronoun phrase, a sentence, or a bare alias with no legal form."""
    name = " ".join(raw.split()).strip(" \"'\u201c\u201d\u2018\u2019,;:")
    name = re.split(r"\s*\(", name)[0].strip(" ,;")
    words = re.findall(r"[A-Za-z][\w.&'/-]*", name)
    if not words or len(words) > 8 or len(name) > 80 or not any(w[0].isupper() for w in words):
        return None
    if re.search("[\"\u201c\u201d]", name):
        return None  # a defined-term phrase such as: "you" or the "Franchisee"
    lowered = {w.lower().strip(".,") for w in words}
    if lowered & _SENTENCE_WORDS or lowered <= _NOT_A_NAME | {"the", "a", "an"}:
        return None
    core = _core(name)
    bare = len(core) == len(_tokens(name))  # no legal form vouching for it
    if not core or (bare and (len(core) < 2 or len(core[-1]) == 1)):
        return None  # nothing but a legal form, an alias like "FMI", or a label like "Party B"
    return name


def party_names(labels: list[str]) -> list[str]:
    """The organisations a contract's "Parties" labels name, in order of appearance.

    CUAD highlights every mention of a party, so aliases ("FMI", "the Distributor") are labelled
    as well as full names. The key keeps only what looks like a full legal name - a legal form,
    or at least two words - so the AI is not marked wrong for writing "Foundation Medicine, Inc."
    where the label says "FMI". Extra parties the AI lists are not counted against it (score.py),
    so a key that is sure rather than complete is the fair one. A name that is only the start of
    a longer one ("Zogenix" / "Zogenix, Inc.") is an alias too and is dropped.

    ponytail: a short defined-term alias ("Roche Basel" for "F. Hoffmann-La Roche Ltd") looks like
    a short real name ("MD Anderson") and gets through; telling them apart takes a person, so
    that is the ceiling of this heuristic.
    """
    candidates = [c for raw in labels if (c := _party_candidate(raw))]
    kept: list[str] = []
    for name in sorted(candidates, key=lambda c: (-len(_core(c)), -len(c))):
        core = _core(name)
        if any(core == _core(other)[: len(core)] for other in kept):
            continue
        kept.append(name)
    return sorted(kept, key=candidates.index)


def extraction_keys(
    spans: dict[str, list[_Span]],
) -> tuple[list[FieldKey], dict[str, int], Counter]:
    """The extraction answer key for one contract, where each key's quote starts, and a count of
    fields left out as unscorable.

    * a category with no label is "not stated in the document" (value None), except
      notice_period when the contract has a termination or renewal clause (see
      ``_NOTICE_MAY_SIT_IN``): that field is left out and counted;
    * a label that cannot be turned into a clean value (a date without a day, a redacted law,
      no usable party name) leaves the field out and is counted, never guessed;
    * ``quote`` is the earliest label used, so "past 25k" means the key's evidence starts there.
    """
    keys: list[FieldKey] = []
    starts: dict[str, int] = {}
    left_out: Counter = Counter()

    def add(field_name: str, kind: str, value, used: _Span | None) -> None:
        keys.append(
            FieldKey(
                field=field_name,
                kind=kind,
                value=value,
                quote=_clip(used.text) if used else None,
            )
        )
        if used:
            starts[field_name] = used.start

    names = party_names([s.text for s in spans.get("Parties", [])])
    if len(names) > MAX_PARTIES:
        left_out[f"parties: more than {MAX_PARTIES} named"] += 1
    elif names:
        used = next(s for s in spans["Parties"] if _party_candidate(s.text) in names)
        add("parties", "parties", names, used)
    else:
        left_out["parties: no usable name"] += 1

    # The app has one start-date field, so a contract that only states an agreement date is
    # keyed by that date.
    for field_name, categories in (
        ("effective_date", ("Effective Date", "Agreement Date")),
        ("expiration_date", ("Expiration Date",)),
    ):
        pool = next((spans[c] for c in categories if spans.get(c)), [])
        dated = [(s, d) for s in pool if (d := parse_full_date(s.text))]
        if not pool:
            add(field_name, "date", None, None)
        elif len({d for _, d in dated}) == 1:
            add(field_name, "date", dated[0][1], dated[0][0])
        else:
            left_out[f"{field_name}: no single complete date"] += 1

    law = spans.get("Governing Law", [])
    place = next((p for s in law if (p := parse_jurisdiction(s.text))), None)
    if not law:
        add("governing_law", "jurisdiction", None, None)
    elif place is None:
        left_out["governing_law: no jurisdiction named"] += 1
    else:
        add(
            "governing_law", "jurisdiction", place,
            next(s for s in law if parse_jurisdiction(s.text) == place),
        )

    for field_name, category in PASSAGE_FIELDS.items():
        first = next(iter(spans.get(category, [])), None)
        if field_name == "notice_period" and not first and any(
            spans.get(c) for c in _NOTICE_MAY_SIT_IN
        ):
            left_out["notice_period: notice may sit in the termination or renewal clause"] += 1
            continue
        add(field_name, "passage", _clip(first.text) if first else None, first)
    return keys, starts, left_out


@dataclass
class _CuadDoc:
    doc_id: str
    title: str
    text: str
    spans: dict[str, list[_Span]]
    past_labels: int
    dropped: int  # labels not found in the text


def _select_cuad(raw_dir: Path, n: int) -> tuple[list[_CuadDoc], int, dict[str, str]]:
    """The ``n`` CUAD contracts longer than 25,000 characters with the most review/extraction
    labels starting past 25,000 (ties by title), their labelled spans, and each category's
    definition text.

    The document text is the LegalBench-RAG corpus copy of the contract, because its question
    spans index into it; the two copies are identical for all 461 shared contracts, and a
    contract whose copies differ is skipped rather than trusted.
    """
    data = json.loads((raw_dir / CUAD_JSON).read_text(encoding="utf-8"))["data"]
    counted = (*CUAD_REVIEW_RULES, *_EXTRACTION_CATEGORIES)
    rows: list[_CuadDoc] = []
    details: dict[str, str] = {}
    for entry in data:
        paragraph = entry["paragraphs"][0]
        copy = raw_dir / LBR_CORPUS / f"{entry['title']}.txt"
        if len(paragraph["context"]) <= CUTOFF or not copy.is_file():
            continue
        text = copy.read_text(encoding="utf-8")
        if text != paragraph["context"]:
            continue
        spans, doc_details, dropped = _spans_by_category(paragraph)
        details = details or doc_details
        past = sum(_past(s.start) for c in counted for s in spans.get(c, []))
        rows.append(_CuadDoc("", entry["title"], text, spans, past, dropped))
    rows.sort(key=lambda r: (-r.past_labels, r.title))
    chosen = rows[:n]
    for i, row in enumerate(chosen, 1):
        row.doc_id = f"cuad-{i:03d}"
    return chosen, len(rows), details


def _review_item(doc: _CuadDoc, source: str) -> ReviewItem:
    rules = []
    for category, rule in CUAD_REVIEW_RULES.items():
        first = next(iter(doc.spans.get(category, [])), None)
        rules.append(
            ReviewRuleKey(
                rule=rule,
                status="deviation" if first else "compliant",
                evidence_quote=_clip(first.text) if first else None,
            )
        )
    return ReviewItem(
        item_id=f"rv-{doc.doc_id}",
        doc_id=doc.doc_id,
        category=SourceCategory.PUBLIC,
        source=source,
        playbook="cuad_checklist",
        rules=rules,
    )


def _cuad_playbook(details: dict[str, str], dataset: Dataset) -> dict:
    """The review checklist for the CUAD set, worded from CUAD's own category definitions."""
    rules = [
        _rule(
            rule,
            "The contract has no such provision. A provision that answers yes to this question "
            f"is a deviation: {details.get(category, category)}",
            f"CUAD category: {category}.",
        )
        for category, rule in CUAD_REVIEW_RULES.items()
    ]
    return {
        "name": "CUAD review checklist (exam)",
        "description": (
            "Exam playbook. Each rule flags a provision the contract should not contain; the "
            f"answer key is the lawyers' labels in {_source(dataset)} ({dataset.licence})."
        ),
        "source": f"{_source(dataset)}, {dataset.licence}",
        "rules": rules,
    }


def _public_document(doc_id: str, title: str, text: str, dataset: str, steps: list) -> tuple:
    data = text.encode("utf-8")
    rel = f"public/{doc_id}.txt"
    doc = ExamDocument(
        doc_id=doc_id,
        category=SourceCategory.PUBLIC,
        dataset=dataset,
        file=rel,
        mime_type="text/plain",
        file_sha256=hashlib.sha256(data).hexdigest(),
        title=title,
        ai_steps=steps,
    )
    return doc, rel, data


# --- ContractNLI: NDA verdicts -----------------------------------------------------------------

_NLI_STATUS = {"Entailment": "compliant", "Contradiction": "deviation", "NotMentioned": "missing"}


def _nli_rule(key: str, label: dict) -> str:
    return f"nda_{int(key.split('-')[1])}_{_slug(label['short_description'])}"


def _nda_playbook(labels: dict, dataset: Dataset) -> dict:
    """ContractNLI's 17 hypotheses as a checklist: the NDA should state each point."""
    rules = [
        _rule(_nli_rule(key, label), label["hypothesis"], label["short_description"])
        for key, label in sorted(labels.items(), key=lambda kv: int(kv[0].split("-")[1]))
    ]
    return {
        "name": "ContractNLI NDA checklist (exam)",
        "description": (
            "Exam playbook. Each rule is a statement about an NDA. The rule is met when the NDA "
            "states it, a deviation when the NDA says the opposite, and missing when the NDA "
            f"does not mention it. The answer key is {_source(dataset)} ({dataset.licence})."
        ),
        "source": f"{_source(dataset)}, {dataset.licence}",
        "rules": rules,
    }


def _nda_sets(raw_dir: Path, dataset: Dataset, n: int) -> tuple[list, list[ReviewItem], dict]:
    """``n`` NDAs - the longest few, then a seeded pick - with the lawyers' verdict on every
    hypothesis. Returns (documents, review items, playbook)."""
    data = json.loads((raw_dir / CNLI_JSON).read_text(encoding="utf-8"))
    labels = data["labels"]
    docs = sorted(data["documents"], key=lambda d: (-len(d["text"]), d["file_name"]))
    rest = sorted(docs[N_NDA_LONGEST:], key=lambda d: _rank(d["file_name"]))
    picked = docs[:N_NDA_LONGEST] + rest[: max(n - N_NDA_LONGEST, 0)]
    documents, review = [], []
    for i, doc in enumerate(picked[:n], 1):
        doc_id = f"cnli-{i:02d}"
        annotations = doc["annotation_sets"][0]["annotations"]
        rules = []
        for key in sorted(annotations, key=lambda k: int(k.split("-")[1])):
            note = annotations[key]
            quote = None
            if note["spans"]:
                start, end = doc["spans"][note["spans"][0]]
                quote = _clip(doc["text"][start:end]) or None
            rules.append(
                ReviewRuleKey(
                    rule=_nli_rule(key, labels[key]),
                    status=_NLI_STATUS[note["choice"]],
                    evidence_quote=quote,
                )
            )
        title = Path(unquote(doc["file_name"]).strip()).stem
        documents.append(_public_document(doc_id, title, doc["text"], "contractnli", ["metadata"]))
        review.append(
            ReviewItem(
                item_id=f"rv-{doc_id}",
                doc_id=doc_id,
                category=SourceCategory.PUBLIC,
                source=_source(dataset),
                playbook="nda_checklist",
                rules=rules,
            )
        )
    return documents, review, _nda_playbook(labels, dataset)


# --- LegalBench-RAG: questions with their exact answer passages --------------------------------


def _qa_items(raw_dir: Path, docs: list[_CuadDoc], dataset: Dataset, n: int) -> list[QAItem]:
    """``n`` questions on the CUAD contracts: at most ``QA_PER_DOC`` per contract, 1-3 snippets
    from one file, under ``PASSAGE_MAX`` characters and quoting the corpus exactly. No two items
    ask the same question of the same contract, so no two items send an identical request."""
    tests = json.loads((raw_dir / LBR_JSON).read_text(encoding="utf-8"))["tests"]
    by_title = {d.title: d for d in docs}
    candidates = []
    for test in tests:
        snippets = test["snippets"]
        files = {s["file_path"] for s in snippets}
        title = next(iter(files))[len("cuad/") : -len(".txt")] if len(files) == 1 else None
        doc = by_title.get(title)
        if (
            doc is None
            or not 1 <= len(snippets) <= 3
            or sum(len(s["answer"]) for s in snippets) > PASSAGE_MAX
            or any(doc.text[s["span"][0] : s["span"][1]] != s["answer"] for s in snippets)
        ):
            continue
        first = min(s["span"][0] for s in snippets)
        rank = _rank(f"{doc.doc_id}|{test['query']}")
        candidates.append((not _past(first), rank, doc, first, test))
    # Both buckets (FR-24) need enough questions to read, so the split is aimed at
    # QA_PAST_SHARE rather than letting the long contracts supply only past-25k questions.
    # Within a bucket the order is the seeded rank.
    candidates.sort(key=lambda c: c[:2])
    taken: list[tuple] = []
    per_doc: Counter = Counter()
    asked: set[tuple[str, str]] = set()

    def take(pool: list, limit: int) -> None:
        for _, _, doc, first, test in pool:
            if len(taken) >= limit:
                return
            if per_doc[doc.doc_id] >= QA_PER_DOC or (doc.doc_id, test["query"]) in asked:
                continue
            per_doc[doc.doc_id] += 1
            asked.add((doc.doc_id, test["query"]))
            taken.append((doc.doc_id, first, test))

    past = [c for c in candidates if not c[0]]
    before = [c for c in candidates if c[0]]
    # Few questions have their answer before 25k in these long contracts (about one in seven),
    # so the before-25k bucket is filled first, up to its share; the past-25k ones then take
    # every remaining place. Filling past-25k first would use up the three-per-contract limit
    # and leave the before-25k bucket nearly empty.
    take(before, n - round(n * QA_PAST_SHARE))
    take(past, n)
    take(before, n)
    taken.sort(key=lambda t: (t[0], t[1], t[2]["query"]))
    items = []
    for i, (doc_id, _, test) in enumerate(taken, 1):
        passages = [s["answer"].strip() for s in test["snippets"]]
        items.append(
            QAItem(
                item_id=f"qa-{i:03d}",
                doc_id=doc_id,
                category=SourceCategory.PUBLIC,
                source=f"{_source(dataset)} / cuad",
                question=test["query"],
                answer=" … ".join(passages),
                passages=passages,
            )
        )
    return items


# --- the public build --------------------------------------------------------------------------


@dataclass
class PublicBuild:
    datasets: list[Dataset]
    documents: list[ExamDocument] = field(default_factory=list)  # cuad-NNN and cnli-NN
    review: list[ReviewItem] = field(default_factory=list)  # rv-cuad-NNN and rv-cnli-NN
    extraction: list[ExtractionItem] = field(default_factory=list)
    qa: list[QAItem] = field(default_factory=list)
    playbooks: dict[str, dict] = field(default_factory=dict)  # nda_checklist, cuad_checklist
    texts: dict[str, bytes] = field(default_factory=dict)  # path under DOCS_DIR -> text bytes
    built: set[str] = field(default_factory=set)  # cuad | contractnli | qa
    report: dict = field(default_factory=dict)


def build_public(
    raw_dir: Path = RAW_DIR, *, n_cuad: int = N_CUAD, n_nda: int = N_NDA, n_qa: int = N_QA
) -> PublicBuild:
    """Build the public sets from the raw downloads. Pure: writes nothing.

    A set whose dataset is not on disk is left out and named in ``report["missing"]`` - never
    replaced by something else (spec edge case).
    """
    registry = dataset_registry(raw_dir)
    ds = {d.key: d for d in registry}
    out = PublicBuild(datasets=registry)
    report = out.report
    report["missing"] = [d.key for d in registry if not d.available]
    report["past_25k_cutoff"] = CUTOFF

    if ds["cuad"].available and ds["legalbench_rag"].available:
        docs, candidates, details = _select_cuad(raw_dir, n_cuad)
        source = _source(ds["cuad"])
        deviations = past_deviations = 0
        fields = past_fields = 0
        left_out: Counter = Counter()
        for doc in docs:
            built, rel, data = _public_document(
                doc.doc_id, doc.title, doc.text, "cuad", ["metadata", "clauses", "brain"]
            )
            out.documents.append(built)
            out.texts[rel] = data
            out.review.append(_review_item(doc, source))
            for category in CUAD_REVIEW_RULES:
                first = next(iter(doc.spans.get(category, [])), None)
                deviations += first is not None
                past_deviations += first is not None and _past(first.start)
            keys, starts, dropped = extraction_keys(doc.spans)
            out.extraction.append(
                ExtractionItem(
                    item_id=f"ex-{doc.doc_id}",
                    doc_id=doc.doc_id,
                    category=SourceCategory.PUBLIC,
                    source=source,
                    fields=keys,
                )
            )
            fields += len(keys)
            past_fields += sum(_past(s) for s in starts.values())
            left_out.update(dropped)
        out.playbooks["cuad_checklist"] = _cuad_playbook(details, ds["cuad"])
        out.built.add("cuad")
        report["cuad"] = {
            "documents": len(docs),
            "documents_past_25k_labels": sum(d.past_labels > 0 for d in docs),
            "candidates_over_25k": candidates,
            "labels_not_found_in_text": sum(d.dropped for d in docs),
            "review_deviations": deviations,
            "review_deviations_past_25k": past_deviations,
            "extraction_fields": fields,
            "extraction_fields_past_25k": past_fields,
            "extraction_fields_left_out": dict(sorted(left_out.items())),
        }
        if docs and ds["legalbench_rag"].available:
            out.qa = _qa_items(raw_dir, docs, ds["legalbench_rag"], n_qa)
            out.built.add("qa")
            report["qa"] = {
                "questions": len(out.qa),
                "documents": len({q.doc_id for q in out.qa}),
                "past_25k": _qa_past(docs, out.qa),
            }

    if ds["contractnli"].available:
        documents, review, playbook = _nda_sets(raw_dir, ds["contractnli"], n_nda)
        for doc, rel, data in documents:
            out.documents.append(doc)
            out.texts[rel] = data
        out.review += review
        out.playbooks["nda_checklist"] = playbook
        out.built.add("contractnli")
        verdicts = Counter(r.status for item in review for r in item.rules)
        report["contractnli"] = {
            "documents": len(review),
            "verdicts": sum(verdicts.values()),
            "by_verdict": dict(sorted(verdicts.items())),
            "documents_over_25k": sum(len(d[2]) > CUTOFF for d in documents),
        }
    return out


def _qa_past(docs: list[_CuadDoc], items: list[QAItem]) -> int:
    """How many questions have every answer passage starting past 25,000 characters."""
    text = {d.doc_id: d.text for d in docs}
    return sum(all(_past(text[q.doc_id].find(p)) for p in q.passages) for q in items)


def write_public(
    build: PublicBuild, *, content_dir: Path = CONTENT_DIR, docs_dir: Path = DOCS_DIR
) -> list[Path]:
    """Write the files that are purely public, and the contract texts under ``docs_dir``.

    ``documents.json``, ``items/review.json`` and ``items/extraction.json`` are not written
    here: they also hold the constructed sets (part 2), so a public-only refresh would drop those
    rows. A set that was not built (its dataset is missing) leaves its previous file untouched.
    """
    written: list[Path] = []

    def put(rel: str, value) -> None:
        _write_json(content_dir / rel, value)
        written.append(content_dir / rel)

    put("datasets.json", [d.model_dump(mode="json") for d in build.datasets])
    if "cuad" in build.built:
        put("playbooks/cuad_checklist.json", build.playbooks["cuad_checklist"])
    if "qa" in build.built:
        put("items/qa.json", [i.model_dump(mode="json") for i in build.qa])
    if "contractnli" in build.built:
        put("playbooks/nda_checklist.json", build.playbooks["nda_checklist"])
    for rel, data in build.texts.items():
        (docs_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (docs_dir / rel).write_bytes(data)
    return written


def format_report(build: PublicBuild) -> str:
    """The build's counts, one line each, as `build` prints them (FR-4: exact counts and how many
    items are past 25k; a missing dataset named, never hidden)."""
    lines = [f"public sets built: {', '.join(sorted(build.built)) or 'none'}"]
    for key, value in build.report.items():
        lines.append(f"  {key}: {json.dumps(value, sort_keys=True)}")
    if build.report["missing"]:
        lines.append(f"MISSING datasets (their sets were not built): {build.report['missing']}")
    return "\n".join(lines)


def run_public(raw_dir: Path = RAW_DIR, *, download: bool = True) -> PublicBuild:
    """Download what is missing, build the public sets, write them, print the counts."""
    if download:
        for problem in download_datasets(raw_dir):
            print(f"download failed: {problem}")
    build = build_public(raw_dir)
    write_public(build)
    print(format_report(build))
    print(
        "documents.json, items/review.json and items/extraction.json were not written: they also "
        "hold the constructed sets, so only the full build (without --only public) refreshes them"
    )
    return build


# ==================================================================================================
# Part 2 (T012): the constructed sets
# ==================================================================================================

SEVERITIES = ("low", "medium", "high", "critical")
ANCHOR_CHARS = 80  # a planted change's anchor: the text just before it (the plan asks for >= 60)
MIN_PLANTS_PAST_25K = 10  # FR-7: the build refuses to finish with fewer plants past 25,000
MIN_LONG_CHARS = 45_000  # a long document is at least this long (FR-7)
CP_PROVIDER = "Northwind Cloud, Inc."
KESTREL = "Kestrel Components Private Limited"  # the one counterparty used in exactly 3 contracts
N_CP_CSA, N_CP_DPA = 11, 7
N_CP_CSA_CONTROLS, N_CP_DPA_CONTROLS = 2, 1  # unchanged documents, to count false alarms
SCAN_TWINS = ("cp-msa-03", "cp-dpa-02", "ae-msa-02", "ae-saas-02", "ae-vendor-03", "ae-long-01")
SCAN_STAMP = "DocuSign Envelope ID"  # the only text layer of a scanned twin (FR-8)
SCAN_DPI = 150
PAGE_W, PAGE_H = 595, 842  # A4 in points
LINES_PER_PAGE, WRAP_CHARS = 60, 95


def _pick(options: tuple | list, *key: str):
    """One option, chosen by a hash of ``key`` (version-proof, unlike an RNG's sequence)."""
    return options[int(_rank(":".join(key))[:12], 16) % len(options)]


def _select(ids: list[str], k: int, tag: str) -> set[str]:
    """Exactly ``k`` of ``ids``, those with the smallest seeded rank for ``tag``."""
    return set(sorted(ids, key=lambda i: _rank(f"{tag}:{i}"))[:k])


# --- planting a change in a document -----------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """The whole line (list number and indentation included) that holds ``phrase``."""

    phrase: str


# What a plant replaces: an exact string, a (first, last) pair - from the first string through the
# end of the next ``last`` after it - or a whole line.
Find = str | tuple[str, str] | Line


@dataclass(frozen=True)
class Plant:
    """A change that breaks one playbook rule. ``replace=None`` deletes the text (status missing)
    """

    rule: str
    find: Find
    replace: str | None


def span(first: str, last: str) -> tuple[str, str]:
    """Everything from ``first`` through the end of the next ``last`` after it."""
    return first, last


def _only(text: str, needle: str) -> int:
    at = text.find(needle)
    if at < 0 or text.find(needle, at + 1) >= 0:
        raise ValueError(
            f"{needle[:70]!r} occurs {text.count(needle)} times in the document, expected 1"
        )
    return at


def _locate(text: str, find: Find) -> tuple[int, int]:
    if isinstance(find, Line):
        at = _only(text, find.phrase)
        end = text.find("\n", at)
        return text.rfind("\n", 0, at) + 1, len(text) if end < 0 else end
    if isinstance(find, tuple):
        first, last = find
        start = _only(text, first)
        stop = text.find(last, start)
        if stop < 0:
            raise ValueError(f"{last[:70]!r} does not follow {first[:40]!r}")
        return start, stop + len(last)
    start = _only(text, find)
    return start, start + len(find)


def apply_plants(base: str, plants: list[Plant]) -> tuple[str, list[tuple[Plant, str, int]]]:
    """The document with every plant applied, and per plant (plant, the text it replaced, its
    offset in the *new* text).

    A deleted paragraph takes the line break after it too, so no blank gap marks the deletion.
    Plants must not overlap: two changes to one sentence would leave neither a clean answer.
    """
    located = sorted(((*_locate(base, p.find), p) for p in plants), key=lambda t: t[0])
    pieces: list[str] = []
    applied: list[tuple[Plant, str, int]] = []
    cursor = shift = 0
    for start, end, plant in located:
        if start < cursor:
            raise ValueError(f"the plant for {plant.rule} overlaps another one")
        stop = end
        if plant.replace is None:
            while stop < len(base) and base[stop] == "\n" and stop - end < 2:
                stop += 1
        pieces += [base[cursor:start], plant.replace or ""]
        applied.append((plant, base[start:end].strip(), start + shift))
        shift += len(plant.replace or "") - (stop - start)
        cursor = stop
    pieces.append(base[cursor:])
    return "".join(pieces), applied


def planted_changes(
    final: str,
    applied: list[tuple[Plant, str, int]],
    severity_of: Callable[[str], tuple[str, str] | None],
) -> dict[str, PlantedChange]:
    """The answer record of each plant: what was changed, the new text, where it sits and its
    severity - which ``severity_of`` reads from the platform playbook rule, never from a model."""
    changes: dict[str, PlantedChange] = {}
    for plant, original, at in applied:
        if plant.rule in changes:
            raise ValueError(f"two changes break {plant.rule} in one document")
        resolved = severity_of(plant.rule)
        if resolved is None:
            raise ValueError(f"{plant.rule} has no severity on its platform rule: nothing to plant")
        severity, source = resolved
        anchor = final[max(0, at - ANCHOR_CHARS) : at]
        if len(anchor) < 60:
            raise ValueError(f"the change for {plant.rule} is too near the top to be anchored")
        if plant.replace is not None and not final.startswith(plant.replace, at):
            raise ValueError(f"the change for {plant.rule} is not where its offset says")
        changes[plant.rule] = PlantedChange(
            rule=plant.rule,
            status="deviation" if plant.replace is not None else "missing",
            original_text=original,
            changed_text=plant.replace,
            anchor_text=anchor,
            char_start=at,
            severity=severity,
            severity_source=source,
        )
    return changes


def assign_rules(rules: list[str], n_docs: int, tag: str) -> list[list[str]]:
    """Which rules each planted document breaks: 2-4 distinct rules per document, handed out in
    seeded order so that every rule is planted in at least one document of its set."""
    ranked = sorted(rules, key=lambda r: _rank(f"{tag}:{r}"))
    per = min(4, max(2, -(-len(ranked) // n_docs)))
    plan = [[ranked[(k * per + i) % len(ranked)] for i in range(per)] for k in range(n_docs)]
    if any(len(set(p)) != per for p in plan) or {r for p in plan for r in p} != set(ranked):
        raise ValueError(f"cannot spread {len(ranked)} rules over {n_docs} documents")
    return plan


def library_severity(library: list[dict], playbook: str, clause_type: str) -> str | None:
    """The ``risk_level`` a person set on one rule of the platform's playbook library, or None when
    the playbook or rule is not there or the rule has no usable severity (then nothing is planted
    for it - a severity is never made up, FR-5)."""
    for book in library:
        if book["name"] == playbook:
            for rule in book["rules"]:
                if rule["clause_type"] == clause_type and rule.get("risk_level") in SEVERITIES:
                    return rule["risk_level"]
    return None


def _library() -> list[dict]:
    # Imported here, not at the top: the playbook module pulls the database layer in, and the exam
    # process must repoint its settings first (env.isolate runs before `build` gets this far).
    from app.playbooks.library import PLAYBOOK_LIBRARY

    return PLAYBOOK_LIBRARY


def _review_rules(rule_names: list[str], planted: dict[str, PlantedChange]) -> list[ReviewRuleKey]:
    keys = []
    for name in rule_names:
        change = planted.get(name)
        if change is None:
            keys.append(ReviewRuleKey(rule=name, status="compliant"))
        else:
            keys.append(
                ReviewRuleKey(
                    rule=name,
                    status=change.status,
                    evidence_quote=_clip(change.changed_text) if change.changed_text else None,
                    severity=change.severity,
                    planted=change,
                )
            )
    return keys


@dataclass
class _Doc:
    """One constructed contract on its way to the content files."""

    doc_id: str
    title: str
    text: str
    review: ReviewItem | None  # None for a scanned twin: it shares its original's record
    steps: list[str]
    uploader: str = "runner"
    dataset: str | None = None
    extraction: ExtractionItem | None = None
    twin_of: str | None = None
    pdf: bytes | None = None  # a scanned twin's file; the text documents are plain text


def _write_text_file(doc: _Doc) -> tuple[str, bytes]:
    return f"constructed/{doc.doc_id}.txt", doc.text.encode("utf-8")


# --- Common Paper: CSA ("MSA") and DPA with planted changes -------------------------------------

# exam rule -> (library playbook, library clause_type, the position, the standard wording is read
# from the downloaded file). The mapping is frozen in the plan; a test pins what each resolves to.
CP_MSA_RULES = (
    ("limitation_of_liability", "Master Services Agreement Playbook", "limitation_of_liability",
     "Each party's liability is capped at no less than the fees paid in the prior 12 months."),
    ("governing_law", "Customer Agreement Playbook", "governing_law",
     "Governed by Delaware law with Delaware courts."),
    ("renewal_notice", "Software / SaaS Agreement Playbook", "renewal",
     "Non-renewal notice of no more than 30 days before the end of the term."),
    ("termination_for_breach", "Master Services Agreement Playbook", "termination",
     "A party may terminate for uncured material breach after 30 days notice."),
    ("confidentiality", "Master Services Agreement Playbook", "confidentiality",
     "The agreement contains mutual confidentiality obligations."),
    ("ip_indemnity", "Master Services Agreement Playbook", "indemnification",
     "Provider indemnifies Customer against third-party IP infringement claims."),
    ("payment_terms", "Master Services Agreement Playbook", "payment_terms",
     "Invoices are payable within 30 days."),
    ("return_of_data", "Data Processing Agreement Playbook", "return_or_deletion",
     "Confidential information is returned or destroyed on termination."),
    ("assignment", "Vendor Agreement Playbook", "subcontracting",
     "No assignment without consent, except on merger or change of control."),
    ("service_warranty", "Vendor Agreement Playbook", "warranties",
     "Provider warrants it will not materially reduce the Cloud Service's functionality."),
)
CP_DPA_RULES = (
    ("breach_notification", "Data Processing Agreement Playbook", "breach_notification",
     "Security incidents are notified within 72 hours."),
    ("subprocessor_notice", "Data Processing Agreement Playbook", "sub_processors",
     "At least 10 business days' notice before a new subprocessor."),
    ("subprocessor_liability", "Data Processing Agreement Playbook", "sub_processors",
     "Provider stays fully liable for its subprocessors."),
    ("audit_rights", "Data Processing Agreement Playbook", "audit",
     "Customer has audit rights."),
    ("deletion_at_expiry", "Data Processing Agreement Playbook", "return_or_deletion",
     "Personal data is returned or deleted when the DPA ends."),
    ("inquiry_notice", "Data Processing Agreement Playbook", "data_subject_rights",
     "Provider notifies Customer and does not answer third-party requests without consent."),
)

# The CSA's variable terms live on a cover page in the real agreement; this one is written out at
# the top of the text, so most CSA plants change a cover-page value (as a negotiator would).
_CSA_COVER = (
    (
        "General Cap Amount",
        (
            "the Fees paid or payable by Customer in the 12 months before the event giving rise "
            "to the claim"
        ),
    ),
    ("Governing Law", "the laws of the State of Delaware"),
    ("Chosen Courts", "the state or federal courts located in Wilmington, Delaware"),
    (
        "Non-Renewal Notice Date",
        "at least 30 days before the end of the current Subscription Period",
    ),
    ("Payment Period", "30 days from the invoice date"),
    (
        "Provider Covered Claims",
        (
            "any claim by a third party that the Cloud Service infringes that party's intellectual "
            "property rights"
        ),
    ),
)
_CSA_PLANTS = (
    Plant(
        "limitation_of_liability",
        "General Cap Amount: the Fees paid or payable by Customer in the 12 months before the "
        "event giving rise to the claim",
        "General Cap Amount: the Fees paid by Customer in the 3 months before the event giving "
        "rise to the claim",
    ),
    Plant(
        "governing_law",
        ("Governing Law: the laws of the State of Delaware", "Wilmington, Delaware"),
        "Governing Law: the laws of the Cayman Islands\n"
        "Chosen Courts: the courts of George Town, Cayman Islands",
    ),
    Plant(
        "renewal_notice",
        "Non-Renewal Notice Date: at least 30 days before the end of the current Subscription "
        "Period",
        "Non-Renewal Notice Date: at least 120 days before the end of the current Subscription "
        "Period",
    ),
    Plant(
        "termination_for_breach",
        "fails to cure a material breach of the Framework Terms or an Order Form following 30 days "
        "notice",
        "fails to cure a material breach of the Framework Terms or an Order Form following 5 days "
        "notice",
    ),
    Plant("confidentiality", Line("Non-Use and Non-Disclosure."), None),
    Plant(
        "ip_indemnity",
        "Provider Covered Claims: any claim by a third party that the Cloud Service infringes that "
        "party's intellectual property rights",
        "Provider Covered Claims: None",
    ),
    Plant(
        "payment_terms",
        "Payment Period: 30 days from the invoice date",
        "Payment Period: 7 days from the invoice date",
    ),
    Plant("return_of_data", Line("Each Recipient will return or destroy"), None),
    Plant(
        "assignment",
        span(
            "Neither party may assign any rights or obligations under this Agreement",
            "their permitted successors and assigns.",
        ),
        "Provider may assign or transfer this Agreement to any third party without Customer's "
        "consent. Customer may not assign this Agreement without Provider's prior written consent.",
    ),
    Plant(
        "service_warranty",
        Line("it will not materially reduce the general functionality of the Cloud Service"),
        None,
    ),
)
_DPA_PLANTS = (
    Plant(
        "breach_notification",
        "no later than 72 hours after becoming aware of the Security Incident",
        "no later than 30 days after becoming aware of the Security Incident",
    ),
    Plant(
        "subprocessor_notice",
        "Provider will inform Customer at least 10 business days in advance",
        "Provider will inform Customer at least 2 business days in advance",
    ),
    Plant(
        "subprocessor_liability",
        Line(
            "Provider remains fully liable for all obligations subcontracted to its Subprocessors"
        ),
        None,
    ),
    Plant("audit_rights", Line("Audit Rights."), None),
    Plant(
        "deletion_at_expiry",
        Line("After the DPA expires, Provider will return or delete Customer Personal Data"),
        None,
    ),
    Plant(
        "inquiry_notice",
        "Provider will notify Customer about the request and Provider will not respond to the "
        "request without Customer’s prior consent.",
        "Provider may respond to such requests directly without notifying Customer.",
    ),
)

_CP_CUSTOMERS = (
    "Globex Pharma Ltd", "Initech Analytics Pvt Ltd", "Umbrella Health Inc", "Stark Components LLC",
    "Wayne Logistics Ltd", "Soylent Foods Pvt Ltd", "Hooli Cloud Inc", "Vandelay Industries",
    "Tyrell Biotech Ltd", "Cyberdyne Systems Pvt Ltd", "Wonka Nutrition Ltd",
    "Oscorp Materials Inc", "Acme Diagnostics Pvt Ltd", "Pied Piper Data Inc",
    "Massive Dynamic Ltd", "Gringotts Finance Ltd", "Monarch Retail Pvt Ltd", "Aperture Labs Inc",
    "Nakatomi Trading Ltd", "Dunder Paper Pvt Ltd",
)


def _clean_markdown(path: Path) -> str:
    """The Common Paper file as plain text: markup and bold marks gone, entities decoded."""
    text = html.unescape(re.sub(r"<[^>]+>", "", path.read_text(encoding="utf-8")))
    return re.sub(r"\*\*", "", text).replace("# ", "", 1)


_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)


def _effective(doc_id: str) -> tuple[date, str]:
    """A seeded effective date and how this document writes it (ISO, or "3 March 2025")."""
    day = date(2025, 1, 1) + timedelta(days=int(_rank(f"{doc_id}:eff")[:6], 16) % 540)
    # not strftime: it follows the machine's locale
    long = f"{day.day} {_MONTH_NAMES[day.month - 1]} {day.year}"
    return day, _pick((day.isoformat(), long), doc_id, "style")


def _cp_playbook(
    rules: tuple, plants: tuple[Plant, ...], standard: str, library: list[dict], name: str,
    description: str,
) -> dict:
    """An exam playbook for a Common Paper agreement. Each rule names the platform rule it stands
    for (``severity_from``) and carries that rule's ``risk_level`` - the review prompt sees it, and
    it is the severity of every plant on the rule. A platform rule that is missing or has no
    severity leaves ``risk_level`` empty and gets no plant. ``sample_clause`` is the standard
    wording itself, read from ``standard`` (an unchanged document) at the place the rule's plant
    changes."""
    where = {p.rule: _locate(standard, p.find) for p in plants}
    out = []
    for rule, book, clause, position in rules:
        built = _rule(
            rule, position, "Common Paper standard position (CC BY 4.0), as the exam reads it."
        )
        built["risk_level"] = library_severity(library, book, clause)
        built["sample_clause"] = _clip(standard[slice(*where[rule])])
        built["severity_from"] = {"playbook": book, "clause_type": clause}
        out.append(built)
    return {
        "name": name,
        "description": description,
        "source": "Common Paper standard agreements, CC BY 4.0",
        "rules": out,
    }


def _cp_severity(rules: tuple, library: list[dict]) -> Callable[[str], tuple[str, str] | None]:
    """(severity, where it came from) of an exam rule, or None when the platform rule has none."""
    where = {r[0]: (r[1], r[2]) for r in rules}

    def severity_of(rule: str):
        book, clause = where[rule]
        level = library_severity(library, book, clause)
        return (level, f"{book} / {clause}") if level else None

    return severity_of


def _cp_docs(raw_dir: Path, library: list[dict], report: dict) -> list[_Doc]:
    csa_file, dpa_file = raw_dir / "commonpaper" / "CSA.md", raw_dir / "commonpaper" / "DPA.md"
    if not (csa_file.is_file() and dpa_file.is_file()):
        report["missing"].append("commonpaper")
        return []
    docs: list[_Doc] = []
    for kind, source, rules, plants, n_docs, n_controls, label in (
        ("msa", _clean_markdown(csa_file), CP_MSA_RULES, _CSA_PLANTS, N_CP_CSA, N_CP_CSA_CONTROLS,
         "Cloud Service Agreement"),
        ("dpa", _clean_markdown(dpa_file), CP_DPA_RULES, _DPA_PLANTS, N_CP_DPA, N_CP_DPA_CONTROLS,
         "Data Processing Agreement"),
    ):
        severity_of = _cp_severity(rules, library)
        usable = [p for p in plants if severity_of(p.rule)]
        for rule, book, clause, _ in rules:
            if not severity_of(rule):
                report["rules_without_severity"].append(f"cp_{kind}.{rule} ({book} / {clause})")
        plan = assign_rules([p.rule for p in usable], n_docs - n_controls, f"cp-{kind}")
        by_rule = {p.rule: p for p in usable}
        for number in range(1, n_docs + 1):
            doc_id = f"cp-{kind}-{number:02d}"
            shift = 5 if kind == "dpa" else 0  # the DPAs do not reuse the CSAs' customers in order
            customer = _CP_CUSTOMERS[(number - 1 + shift) % len(_CP_CUSTOMERS)]
            day, written = _effective(doc_id)
            cover = [
                f"{label.upper()} - COVER PAGE",
                f"Provider: {CP_PROVIDER}",
                f"Customer: {customer}",
                f"Effective Date: {written}",
            ]
            if kind == "msa":
                cover += [f"{name}: {value}" for name, value in _CSA_COVER]
            base = "\n".join(cover) + "\n\n" + source
            chosen = []
            if number > n_controls:  # the first documents of a set are its controls
                chosen = [by_rule[r] for r in plan[number - n_controls - 1]]
            final, applied = apply_plants(base, chosen)
            changes = planted_changes(final, applied, severity_of)
            docs.append(
                _Doc(
                    doc_id=doc_id,
                    title=f"{label} - {customer}",
                    text=final,
                    review=ReviewItem(
                        item_id=f"rv-{doc_id}",
                        doc_id=doc_id,
                        category=SourceCategory.CONSTRUCTED,
                        source="constructed",
                        playbook=f"cp_{kind}",
                        control=not chosen,
                        rules=_review_rules([r[0] for r in rules], changes),
                    ),
                    steps=["metadata"],
                    dataset="commonpaper",
                    extraction=_cp_extraction(doc_id, kind, customer, day, final, changes),
                )
            )
    return docs


def _cp_extraction(
    doc_id: str, kind: str, customer: str, day: date, text: str, changes: dict
) -> ExtractionItem:
    """What a reader of the cover page can state: both parties and the effective date, and for the
    CSA the governing law (the plant that changes it changes the answer, so the key follows it)."""
    parties = f"Provider: {CP_PROVIDER}\nCustomer: {customer}"
    fields = [
        FieldKey(field="parties", kind="parties", value=[CP_PROVIDER, customer], quote=parties),
        FieldKey(
            field="effective_date",
            kind="date",
            value=day.isoformat(),
            quote=_line_with(text, "Effective Date: "),
        ),
    ]
    if kind == "msa":
        cayman = "governing_law" in changes
        fields.append(
            FieldKey(
                field="governing_law",
                kind="jurisdiction",
                value="cayman islands" if cayman else "delaware",
                quote=_line_with(text, "Governing Law: the laws of"),
            )
        )
    for f in fields:
        if f.quote not in text:
            raise ValueError(f"{doc_id}: the key quote for {f.field} is not in the text")
    return ExtractionItem(
        item_id=f"ex-{doc_id}",
        doc_id=doc_id,
        category=SourceCategory.CONSTRUCTED,
        source="constructed",
        fields=fields,
    )


def _line_with(text: str, phrase: str) -> str:
    at = _only(text, phrase)
    end = text.find("\n", at)
    return text[text.rfind("\n", 0, at) + 1 : len(text) if end < 0 else end]


# --- AEGIS templates: the plant catalogue per contract type -------------------------------------

# Each type is reviewed against the platform playbook of the same name (library). A plant breaks one
# rule of that playbook, so its severity is that rule's risk_level. Only rules whose clause the
# shipped template really contains are listed (a rule with nothing to break would key a clause the
# template does not have). Every ``find`` must occur exactly once in the rendered document - the
# build refuses to finish otherwise.
AEGIS_PLAYBOOK = {
    "msa": "Master Services Agreement Playbook",
    "consultancy": "Consultancy Agreement Playbook",
    "vendor": "Vendor Agreement Playbook",
    "saas": "Software / SaaS Agreement Playbook",
    "sow": "Statement of Work Playbook",
    "dpa": "Data Processing Agreement Playbook",
}

AEGIS_CATALOGUE: dict[str, tuple[Plant, ...]] = {
    "msa": (
        Plant(
            "limitation_of_liability",
            span(
                "each Party's total aggregate liability arising out of",
                "preceding the event giving rise to the claim",
            ),
            "the Customer's total aggregate liability arising out of or in connection with this "
            "Agreement shall be unlimited, and the Provider's total aggregate liability shall not "
            "exceed the Fees paid in the three (3) months immediately preceding the event giving "
            "rise to the claim",
        ),
        Plant(
            "indemnification",
            span(
                "(c) any allegation that the Services or Deliverables, or their use by the "
                "Customer as contemplated by this Agreement, infringe",
                "any breach by the Provider of Clause 10 or the Data Protection Laws.",
            ),
            "(c) any breach by the Provider of Clause 10 or the Data Protection Laws.",
        ),
        Plant(
            "intellectual_property",
            span(
                "All Intellectual Property Rights in the Deliverables vest in and belong to the "
                "Customer upon payment",
                "to the extent permitted by Applicable Law.",
            ),
            "All Intellectual Property Rights in the Deliverables remain with the Provider. The "
            "Provider grants the Customer a non-exclusive, non-transferable licence to use the "
            "Deliverables for the Customer's internal business purposes during the term of this "
            "Agreement only.",
        ),
        Plant(
            "payment_terms",
            "Any advance or mobilisation payment shall not exceed twenty percent (20%) of the "
            "total Fees under the relevant Statement of Work and shall be credited against the "
            "first invoices issued under it.",
            "The Customer shall pay fifty percent (50%) of the total Fees under each Statement of "
            "Work in advance, on signature of that Statement of Work, and the balance on delivery.",
        ),
        Plant(
            "termination",
            span(
                "8.1 The Customer may terminate this Agreement or any Statement of Work for "
                "convenience",
                "written notice to the Provider.",
            ),
            None,
        ),
        Plant(
            "confidentiality",
            span(
                "The obligations in this Clause 9 continue during the term of this Agreement",
                "under Applicable Law.",
            ),
            "The obligations in this Clause 9 continue during the term of this Agreement and for "
            "six (6) months after its termination or expiry.",
        ),
        Plant(
            "insurance",
            span(
                "The Provider shall, at its own cost, maintain with reputable insurers",
                "does not limit its liability under this Agreement.",
            ),
            "The Provider is not required to maintain any insurance in connection with this "
            "Agreement or the Services.",
        ),
        Plant(
            "service_levels",
            span(
                "If the Provider fails to meet a Service Level, the Provider shall credit the "
                "Customer",
                "are without prejudice to the Customer's other rights and remedies.",
            ),
            "If the Provider fails to meet a Service Level, the Provider shall use reasonable "
            "efforts to remedy the failure. No service credits are payable and the Customer's sole "
            "remedy is the remediation plan under Clause 5.2.",
        ),
        Plant(
            "data_protection",
            "and in any event within seventy-two (72) hours, after becoming aware of any personal "
            "data breach",
            "and in any event within thirty (30) days, after becoming aware of any personal data "
            "breach",
        ),
    ),
    "consultancy": (
        Plant(
            "intellectual_property",
            span(
                "All Intellectual Property Rights in the Work Product vest in the Company on "
                "creation.",
                "from each of its Personnel.",
            ),
            "All Intellectual Property Rights in the Work Product remain with the Consultant. The "
            "Consultant grants the Company a non-exclusive, non-transferable licence to use the "
            "Work Product for the Company's internal purposes during the term of this Agreement "
            "only.",
        ),
        Plant(
            "independent_contractor",
            span(
                "The Consultant is engaged as an independent contractor.",
                "other entitlement of an employee of the Company.",
            ),
            "The Consultant shall work under the day-to-day direction and supervision of the "
            "Company, during the Company's normal working hours and at the Company's premises, "
            "using the Company's equipment, and shall be entitled to the same leave and benefits "
            "as the Company's employees.",
        ),
        Plant(
            "confidentiality",
            span(
                "The obligations in this Clause 9 continue during the term of this Agreement",
                "under Applicable Law.",
            ),
            "The obligations in this Clause 9 continue during the term of this Agreement and for "
            "six (6) months after its termination or expiry.",
        ),
        Plant(
            "conflicts_of_interest",
            span(
                "4.1 The Consultant confirms that, at the Effective Date",
                "in connection with the Services.",
            ),
            "4.1 The Consultant may act for any other client, including the Company's competitors, "
            "on the same or similar matters during the term of this Agreement, and has no "
            "obligation to disclose any such engagement to the Company.",
        ),
        Plant(
            "non_solicitation",
            span(
                "10.1 During the term of this Agreement and for twelve (12) months",
                "is not a breach of this Clause 10.",
            ),
            None,
        ),
        Plant(
            "payment_terms",
            "Any advance payment shall not exceed twenty percent (20%) of the total Fees and shall "
            "be credited against the first invoices issued under this Agreement.",
            "The Company shall pay fifty percent (50%) of the total Fees in advance on signature "
            "of this Agreement.",
        ),
        Plant(
            "termination",
            span(
                "7.1 The Company may terminate this Agreement for convenience",
                "written notice to the Consultant.",
            ),
            None,
        ),
        Plant(
            "limitation_of_liability",
            span(
                "each Party's total aggregate liability arising out of",
                "payable under this Agreement.",
            ),
            "the Company's total aggregate liability arising out of or in connection with this "
            "Agreement shall be unlimited, and the Consultant's total aggregate liability shall "
            "not exceed INR 100,000 in aggregate.",
        ),
    ),
    "vendor": (
        Plant(
            "limitation_of_liability",
            span(
                "each Party's total aggregate liability arising out of",
                "preceding the event giving rise to the claim.",
            ),
            "the Company's total aggregate liability arising out of or in connection with this "
            "Agreement shall be unlimited, and the Vendor's total aggregate liability shall not "
            "exceed the total Fees paid in the three (3) months preceding the event giving rise "
            "to the claim.",
        ),
        Plant(
            "indemnification",
            span(
                "10.1 The Vendor shall defend, indemnify and hold harmless",
                "infringe the intellectual property rights of a third party.",
            ),
            "10.1 The Vendor's only liability in respect of any third-party claim relating to the "
            "Deliverables is to refund the Fees paid for the Deliverables concerned.",
        ),
        Plant(
            "warranties",
            "for a period of at least twelve (12) months from acceptance (the \"Warranty Period\")",
            "for a period of thirty (30) days from delivery (the \"Warranty Period\")",
        ),
        Plant(
            "compliance",
            span(
                "6.1 The Vendor shall, and shall ensure that its personnel and subcontractors "
                "shall, comply",
                "without liability to the Vendor.",
            ),
            "6.1 The Vendor shall use reasonable efforts to comply with Applicable Law in "
            "performing this Agreement. The Company has no right to terminate this Agreement or "
            "any Order for any breach of Applicable Law by the Vendor.",
        ),
        Plant(
            "payment_terms",
            "No advance payment shall be made except where an Order expressly provides for one, "
            "and any such advance shall not exceed twenty percent (20%) of the Fees under that "
            "Order. Wherever possible the Parties shall prefer milestone payments tied to "
            "acceptance over any advance.",
            "The Company shall pay fifty percent (50%) of the Fees under each Order in advance, on "
            "placing that Order.",
        ),
        Plant(
            "subcontracting",
            span(
                "The Vendor shall not subcontract any of its obligations under this Agreement "
                "without",
                "bound by obligations consistent with this Agreement.",
            ),
            "The Vendor may subcontract any of its obligations under this Agreement to any third "
            "party without the Company's consent, and is not responsible for the acts or omissions "
            "of its subcontractors.",
        ),
        Plant(
            "audit_rights",
            span(
                "14.2 The Company, or an auditor appointed by it",
                "bear the reasonable cost of the audit.",
            ),
            None,
        ),
        Plant(
            "data_protection",
            "and in any event within seventy-two (72) hours, after becoming aware of any personal "
            "data breach affecting the Company's personal data",
            "and in any event within thirty (30) days, after becoming aware of any personal data "
            "breach affecting the Company's personal data",
        ),
    ),
    "saas": (
        Plant(
            "service_levels",
            "at least 99.5% of the time in each calendar month",
            "at least 95.0% of the time in each calendar month",
        ),
        Plant(
            "data_security",
            "in any event within seventy-two (72) hours after becoming aware of it",
            "in any event within thirty (30) days after becoming aware of it",
        ),
        Plant(
            "data_location",
            span(
                "The Provider shall host, store and process Customer Data only in data centres "
                "located in India",
                "in the European Union.",
            ),
            "The Provider may host, store and process Customer Data in data centres located in any "
            "country of its choice, without notice to the Customer.",
        ),
        Plant(
            "exit_and_data_return",
            span(
                "On expiry or termination of this Agreement or any Order Form for any reason, the "
                "Provider shall, for a period of thirty (30) days",
                "without additional charge.",
            ),
            "On expiry or termination of this Agreement or any Order Form for any reason, the "
            "Provider may delete all Customer Data immediately and has no obligation to return or "
            "export it.",
        ),
        Plant(
            "price_increases",
            span(
                "The Provider may increase the Fees with effect from a renewal",
                "is of no effect.",
            ),
            "The Provider may increase the Fees at any time on thirty (30) days' notice by any "
            "amount it considers appropriate.",
        ),
        # Drops the provider's reminder (the rule asks for one) and nothing else: the clause stays
        # opt-in with its 60-day cancellation, so this plant cannot turn a contract the portfolio
        # lists as fixed-term into an auto-renewing one.
        Plant(
            "renewal",
            span(
                "The Provider shall send the Customer a written renewal reminder",
                "without the Customer's express written confirmation.",
            ),
            "The Provider is not required to send the Customer a renewal reminder, or to state the "
            "renewal date, the cancellation deadline or the Fees that will apply on renewal.",
        ),
        Plant(
            "limitation_of_liability",
            span(
                "15.2 Subject to clauses 15.4 and 15.5, the Provider's total aggregate liability",
                "in addition to the amount in clause 15.1.",
            ),
            None,
        ),
        Plant(
            "payment_terms",
            "provided that no amount invoiced before the relevant period begins shall exceed "
            "twenty percent (20%) of the annual Fees unless the Parties agree otherwise in a "
            "signed Order Form",
            "and the Provider may invoice up to one hundred percent (100%) of the annual Fees in "
            "advance, at the start of each year",
        ),
    ),
    "sow": (
        Plant(
            "scope_and_deliverables",
            span(
                "Any service, deliverable or activity not expressly listed in Clauses 4.2 and "
                "4.3 is out of scope.",
                "added through a Change Order under Clause 7.",
            ),
            "The Services and Deliverables are described in general terms only. The Provider shall "
            "perform any other work reasonably related to them that the Customer requires, and "
            "shall be paid for it at the rates in the Master Agreement.",
        ),
        Plant(
            "acceptance",
            span(
                "A Deliverable is accepted only when the Customer confirms acceptance in writing.",
                "does not constitute acceptance.",
            ),
            "A Deliverable is deemed accepted if the Customer does not notify the Provider of a "
            "non-conformity within five (5) working days of receiving it, or if the Customer uses "
            "it.",
        ),
        Plant(
            "fees_and_pricing",
            span(
                "Total charges under this SOW shall not exceed the not-to-exceed amount",
                "unless it is increased by a signed Change Order.",
            ),
            "There is no cap on the total charges under this SOW, and the Customer shall pay for "
            "all time worked as invoiced.",
        ),
        Plant(
            "milestone_payments",
            span(
                "Payment under this SOW is tied to the Customer's written acceptance of "
                "Deliverables, not to calendar dates.",
                "linked to that milestone under Clause 6:",
            ),
            "Payment under this SOW falls due on the calendar dates stated below, whether or not "
            "the related Deliverables have been delivered or accepted. The Provider may invoice "
            "each payment on its due date:",
        ),
        Plant(
            "change_control",
            span(
                "7.3 Until a Change Order is signed by both Parties",
                "recorded in a signed Change Order.",
            ),
            "7.3 Any change to this SOW may be agreed orally or by email between the Parties' "
            "representatives and takes effect immediately, and the Provider is entitled to "
            "additional payment and an extension of time for it.",
        ),
        Plant(
            "order_of_precedence",
            span(
                "If there is any conflict or inconsistency between the Master Agreement and this "
                "SOW, the Master Agreement prevails",
                "this SOW prevails for this SOW only.",
            ),
            "If there is any conflict or inconsistency between the Master Agreement and this SOW, "
            "this SOW prevails in all respects, including in relation to liability, indemnities, "
            "data protection and intellectual property.",
        ),
        Plant(
            "key_personnel",
            span(
                "The Provider shall ensure that the Key Personnel devote the time necessary",
                "beyond the Provider's reasonable control.",
            ),
            "The Provider may remove or replace any Key Personnel at any time without notice to, "
            "or the consent of, the Customer.",
        ),
    ),
    "dpa": (
        Plant(
            "processing_instructions",
            span(
                "The Processor shall process Personal Data only on the documented instructions of "
                "the Controller and only for the purpose stated in Annex 1",
                "on important grounds of public interest.",
            ),
            "The Processor may process Personal Data for any purpose it considers necessary for "
            "its business, including its own analytics and product development, and need not "
            "follow the Controller's instructions.",
        ),
        Plant(
            "sub_processors",
            span(
                "The Processor shall not engage any sub-processor without the Controller's prior "
                "specific written authorisation.",
                "are listed in Annex 3.",
            ),
            "The Processor may engage any sub-processor of its choice, without notice to or the "
            "consent of the Controller.",
        ),
        Plant(
            "breach_notification",
            "and in any event within forty-eight (48) hours, after becoming aware of it",
            "and in any event within thirty (30) days, after becoming aware of it",
        ),
        Plant(
            "security_measures",
            span(
                "Those security measures shall include, at a minimum, the measures described in "
                "Annex 2",
                "effectiveness of the measures.",
            ),
            "The Processor shall apply such security measures as it considers appropriate, and is "
            "not required to encrypt Personal Data or to follow any particular standard.",
        ),
        Plant(
            "international_transfers",
            span(
                "International transfers of Personal Data are permitted only in accordance with "
                "this Section 9.",
                "without the Controller's prior written authorisation.",
            ),
            "The Processor may transfer Personal Data to, and permit it to be accessed from, any "
            "country, without notice to or the authorisation of the Controller.",
        ),
        Plant(
            "audit",
            span(
                "The Processor shall allow for and contribute to audits",
                "required by a Supervisory Authority.",
            ),
            "The Controller has no right to audit the Processor, and the Processor's own "
            "certification is conclusive evidence of its compliance.",
        ),
        Plant(
            "data_subject_rights",
            span(
                "If the Processor receives a request from a Data Subject, it shall forward the "
                "request",
                "except on the Controller's documented instructions.",
            ),
            "If the Processor receives a request from a Data Subject, it may respond to it "
            "directly and need not inform the Controller.",
        ),
        Plant(
            "return_or_deletion",
            span(
                "On termination or expiry of the Services, or at any earlier time on the "
                "Controller's written request",
                "within thirty (30) days.",
            ),
            "On termination or expiry of the Services, the Processor may retain all Personal Data "
            "indefinitely for its own purposes.",
        ),
    ),
}


# --- long documents: the changes sit in the schedules, past 25,000 characters --------------------

# A long document is an AEGIS MSA (or SaaS agreement) with the SoW template appended as Schedule 1
# and the DPA template as Schedule 2 (FR-7). Each plant sits in a schedule and breaks a rule of the
# host playbook, so it is the platform rule's severity that applies. ``replace(plant, rule=...)``
# reuses a DPA catalogue plant under the host rule it breaks.
_DPA_BY_RULE = {p.rule: p for p in AEGIS_CATALOGUE["dpa"]}
_S1_PAYMENT = Plant(
    "payment_terms",
    "shall pay valid, undisputed invoices within 45 days of receipt, and may withhold any amount "
    "it disputes in good faith in accordance with the Master Agreement",
    "shall pay all invoices within seven (7) days of receipt, whether or not disputed, and may not "
    "withhold any amount for any reason",
)
_S1_LIABILITY = Plant(
    "limitation_of_liability",
    span(
        "All other terms of the Master Agreement, including those relating to confidentiality,",
        "consistent with the Master Agreement.",
    ),
    "All other terms of the Master Agreement apply to this SOW, except the terms on limitation of "
    "liability, which do not apply. The Provider's total aggregate liability arising out of or in "
    "connection with this SOW shall not exceed INR 100,000.",
)
_S1_TERMINATION = Plant(
    "termination",
    ("This SOW may be terminated in accordance with the Master Agreement.", "to the Customer."),
    "This SOW may not be terminated for convenience. On any termination the Customer shall pay the "
    "Provider all Fees that would have been payable for the full term of this SOW.",
)
_S2_INDEMNITY = Plant(
    "indemnification",
    span(
        "The Processor shall indemnify and hold harmless the Controller against all losses",
        "by the Processor or any Sub-processor.",
    ),
    "The Controller shall indemnify and hold harmless the Processor against all losses, damages, "
    "fines, penalties, costs and expenses (including reasonable legal fees) arising out of the "
    "Processing of Personal Data under this Agreement.",
)
_S2_LIABILITY = Plant(
    "limitation_of_liability",
    "Any limitation or exclusion of liability in the Principal Agreement shall not apply to the "
    "Processor's liability under this Agreement or for breach of Applicable Data Protection Law.",
    "The Processor's total aggregate liability under this Agreement shall not exceed INR 100,000 "
    "in aggregate.",
)
LONG_DOCS = (
    ("ae-long-01", "msa",
     (_S1_PAYMENT, _S1_LIABILITY,
      replace(_DPA_BY_RULE["breach_notification"], rule="data_protection"))),
    ("ae-long-02", "msa",
     (_S1_TERMINATION, _S2_INDEMNITY,
      replace(_DPA_BY_RULE["international_transfers"], rule="data_protection"))),
    ("ae-long-03", "msa", (_S1_PAYMENT, _S2_LIABILITY, _S1_TERMINATION)),
    ("ae-long-04", "saas",
     (_S1_PAYMENT, replace(_DPA_BY_RULE["breach_notification"], rule="data_security"),
      replace(_DPA_BY_RULE["international_transfers"], rule="data_location"))),
)


# --- the deals: who signs what, where, for how long and for how much ---------------------------

# The 40 portfolio contracts are the 24 AEGIS review documents, the 4 long documents and 12 extra
# ones. Their facts are decided here, before any text is written, and the portfolio questions' exact
# answer lists are computed from them - then checked against the rendered texts, so a plant (or a
# template edit) that disturbs a fact fails the build instead of leaving a wrong key.
_ENTITIES = (
    "Aegis Pharma Private Limited", "Aegis Lifesciences Pvt. Ltd.", "M/s Aegis Healthcare Traders",
)
_CITIES = ("Mumbai", "New Delhi", "Bengaluru")
_OFFICE = {
    "Mumbai": "Unit 12, Tech Park One, Andheri East, Mumbai, Maharashtra 400059, India",
    "New Delhi": "3rd Floor, Statesman House, Barakhamba Road, New Delhi 110001, India",
    "Bengaluru": "No. 45, Outer Ring Road, Marathahalli, Bengaluru, Karnataka 560037, India",
}
_OTHER_OFFICES = (
    "Plot 7, MIDC Industrial Area, Pune, Maharashtra 411019, India",
    "12 Industrial Estate, Naroda, Ahmedabad, Gujarat 382330, India",
    "Sector 18, Udyog Vihar, Gurugram, Haryana 122015, India",
    "SIPCOT Industrial Park, Sriperumbudur, Chennai, Tamil Nadu 602105, India",
    "Salt Lake Sector V, Kolkata, West Bengal 700091, India",
    "Plot 22, Hitech City, Hyderabad, Telangana 500081, India",
)
_ABROAD = {
    "England and Wales": (
        ("Thames Valley Logistics Ltd", "Pennine Analytics Limited", "Cotswold Biotech Ltd",
         "Severn Components Ltd"),
        "Unit 5, 22 Canal Street, London EC2A 3BH, United Kingdom",
    ),
    "Singapore": (
        (
            "Merlion Analytics Pte. Ltd.", "Raffles Components Pte. Ltd.",
            "Sentosa Logistics Pte. Ltd.",
        ),
        "10 Anson Road, #12-05 International Plaza, Singapore 079903",
    ),
}
_IN_FIRST = (
    "Aranya", "Bhavani", "Chandra", "Deccan", "Ekta", "Fortuna", "Gandiva", "Himadri", "Indus",
    "Jyoti", "Kaveri", "Lotus", "Malabar", "Narmada", "Orchid", "Pinnacle", "Quantum", "Rajdhani",
    "Sahyadri", "Tapovan", "Udaan", "Vindhya", "Yamuna", "Zenith", "Ashoka", "Bharat", "Chola",
    "Dhruva", "Eastern", "Gokul", "Harappa", "Ishan", "Jaipur", "Konark", "Lakshya", "Mandara",
    "Nilgiri", "Omkar", "Prakash", "Rudra", "Surya", "Trident",
)
_IN_TRADE = (
    "Logistics", "Analytics", "Components", "Biotech", "Packaging", "Software", "Consulting",
    "Foods", "Textiles", "Engineering", "Diagnostics", "Materials", "Labs", "Systems",
)
_IN_FORM = ("Private Limited", "Pvt. Ltd.", "Limited", "LLP")
_PF_KINDS = ("nda", "vendor", "saas", "msa", "consultancy", "sow", "nda", "vendor", "saas", "dpa",
             "nda", "msa")
_PF_LAWS = ("India", "England and Wales", "Singapore", "India", "England and Wales", "Singapore",
            "India", "England and Wales", "India", "Singapore", "England and Wales", "India")
_KESTREL_DOCS = ("ae-vendor-02", "ae-saas-04", "pf-04")  # three contracts, three kinds, one party
_VALUE_KINDS = ("msa", "consultancy", "vendor", "saas", "sow")
_END_2027 = (date(2027, 3, 31), date(2027, 6, 30), date(2027, 9, 30), date(2027, 12, 31))
_END_OTHER = (date(2026, 12, 31), date(2028, 3, 31), date(2028, 9, 30), date(2029, 3, 31))
_SMALL_VALUES = (2_500_000, 3_500_000, 6_000_000, 8_500_000, 9_500_000)
_BIG_VALUES = (12_000_000, 15_000_000, 25_000_000, 60_000_000, 85_000_000)
_NOTICE_CYCLE = (90, 30, 120, 60, 90, 30, 60, 120)
ONE_CRORE = 10_000_000
N_AUTO_RENEWING, N_ENDING_2027, N_BIG_VALUE = 8, 6, 5


@dataclass(frozen=True)
class Deal:
    doc_id: str
    kind: str  # msa | consultancy | vendor | saas | sow | dpa | nda
    company: str
    counterparty: str
    effective: date
    written: str  # how the document writes the effective date
    law: str  # India | England and Wales | Singapore
    city: str | None  # the city whose courts have jurisdiction (Indian law only)
    company_office: str
    counterparty_office: str
    end: date | None  # None for an NDA, which has no end date
    auto_renew: bool
    renewal_term: str
    notice_days: int
    value: int | None
    long: bool = False

    def fields(self) -> dict:
        """What ``render_document`` is given - the same answers the intake forms collect."""
        out: dict = {"governing_law": self.law}
        if self.end:
            out |= {
                "start_date": self.effective.isoformat(),
                "end_date": self.end.isoformat(),
                "term": "Renews automatically" if self.auto_renew else "Fixed end date",
                "renewal_term": self.renewal_term,
                "notice_days": f"{self.notice_days} days",
            }
        if self.value:
            out |= {"value": str(self.value), "currency": "INR"}
        return out


def portfolio_ids() -> list[str]:
    """The 40 portfolio contracts, in the order the exam lists them."""
    return [
        *[f"ae-{kind}-{n:02d}" for kind in AEGIS_PLAYBOOK for n in range(1, 5)],
        *[doc_id for doc_id, _, _ in LONG_DOCS],
        *[f"pf-{n:02d}" for n in range(1, 13)],
    ]


def _kind_of(doc_id: str) -> str:
    if doc_id.startswith("pf-"):
        return _PF_KINDS[int(doc_id[3:]) - 1]
    if doc_id.startswith("ae-long-"):
        return next(kind for i, kind, _ in LONG_DOCS if i == doc_id)
    return doc_id.split("-")[1]


def make_deals() -> dict[str, Deal]:
    ids = portfolio_ids()
    kinds = {i: _kind_of(i) for i in ids}
    law = {i: "India" for i in ids}
    law |= {f"pf-{n:02d}": _PF_LAWS[n - 1] for n in range(1, 13)}
    term_ids = [i for i in ids if kinds[i] != "nda"]
    auto = _select(term_ids, N_AUTO_RENEWING, "auto")
    ends_2027 = _select([i for i in term_ids if i not in auto], N_ENDING_2027, "end2027")
    value_ids = [i for i in ids if kinds[i] in _VALUE_KINDS]
    big = sorted(_select(value_ids, N_BIG_VALUE, "bigvalue"), key=lambda i: _rank(f"big:{i}"))
    notice = dict(zip(sorted(auto, key=lambda i: _rank(f"notice:{i}")), _NOTICE_CYCLE, strict=True))
    deals: dict[str, Deal] = {}
    indian = 0
    abroad: Counter = Counter()
    for doc_id in ids:
        city = _pick(_CITIES, doc_id, "city") if law[doc_id] == "India" else None
        if doc_id in _KESTREL_DOCS:
            counterparty, office = KESTREL, _pick(_OTHER_OFFICES, doc_id, "office")
        elif law[doc_id] != "India":
            names, office = _ABROAD[law[doc_id]]
            counterparty = names[abroad[law[doc_id]] % len(names)]
            abroad[law[doc_id]] += 1
        else:
            first = _IN_FIRST[indian]
            trade = _IN_TRADE[(indian * 5 + 2) % len(_IN_TRADE)]
            form = _IN_FORM[(indian * 3 + 1) % len(_IN_FORM)]
            counterparty = f"{'M/s ' if indian % 6 == 4 else ''}{first} {trade} {form}"
            office, indian = _pick(_OTHER_OFFICES, doc_id, "office"), indian + 1
        day, written = _effective(doc_id)
        has_term = kinds[doc_id] != "nda"
        end = None
        if has_term:
            end = _pick(_END_2027 if doc_id in ends_2027 else _END_OTHER, doc_id, "end")
        value = None
        if kinds[doc_id] in _VALUE_KINDS:
            value = (
                _BIG_VALUES[big.index(doc_id)] if doc_id in big
                else _pick(_SMALL_VALUES, doc_id, "value")
            )
        deals[doc_id] = Deal(
            doc_id=doc_id,
            kind=kinds[doc_id],
            company=_pick(_ENTITIES, doc_id, "company"),
            counterparty=counterparty,
            effective=day,
            written=written,
            law=law[doc_id],
            city=city,
            company_office=_OFFICE[city or _pick(_CITIES, doc_id, "hq")],
            counterparty_office=office,
            end=end,
            auto_renew=doc_id in auto,
            renewal_term=_pick(("1 year", "2 years"), doc_id, "renewal"),
            notice_days=notice.get(doc_id, 0),
            value=value,
            long=doc_id.startswith("ae-long-"),
        )
    return deals


def _render(deal: Deal) -> str:
    """The deal as the app would draft it, plus the registered-office recital Indian paper carries
    (and, for Indian law, the city whose courts have jurisdiction), plus the schedules of a long
    document."""
    # Imported here: drafting pulls the database layer in, and the exam process repoints its
    # settings before `build` gets this far (env.isolate).
    from app.intake.drafting import render_document

    def draft(kind: str) -> str:
        return render_document(
            kind, company=deal.company, counterparty=deal.counterparty, effective=deal.written,
            fields=deal.fields(),
        )

    recital = (
        f"{deal.company} has its registered office at {deal.company_office}. "
        f"{deal.counterparty} has its registered office at {deal.counterparty_office}."
    )
    if deal.city:
        recital += (
            f" The courts at {deal.city} shall have exclusive jurisdiction over any dispute "
            "arising out of this Agreement."
        )
    title, opening, rest = draft(deal.kind).split("\n\n", 2)
    text = f"{title}\n\n{opening}\n\n{recital}\n\n{rest}"
    if deal.long:
        text += f"\n\nSCHEDULE 1 - STATEMENT OF WORK\n\n{draft('sow')}"
        text += f"\n\nSCHEDULE 2 - DATA PROCESSING AGREEMENT\n\n{draft('dpa')}"
    return text


# --- extraction keys of a constructed contract ---------------------------------------------------


def _sentence_at(text: str, at: int) -> str:
    """The sentence around ``at``, without a leading clause number ("19.1 ")."""
    line = text.rfind("\n", 0, at) + 1
    dot = text.rfind(". ", line, at)
    start = dot + 2 if dot >= 0 else line
    stops = [i for i in (text.find(". ", at), text.find("\n", at)) if i >= 0]
    stop = min(stops) + (1 if text.startswith(". ", min(stops)) else 0)
    return re.sub(r"^\d+(?:\.\d+)*\s+", "", text[start:stop])


def deal_extraction(deal: Deal, text: str) -> ExtractionItem:
    """The facts the contract states, each with the exact words that state it. A fact that a plant
    could have changed is read from the final text, and every quote is checked to be in it."""
    # Imported here for the same reason as in _render: drafting pulls the database layer in.
    from app.intake.drafting import _long_date

    opening = text.index("entered into as of")
    parties_end = text.index(deal.counterparty, opening) + len(deal.counterparty)
    effective = re.compile(r'\(the "(?:SOW )?Effective Date"\)').search(text, opening)
    fields = [
        FieldKey(
            field="parties", kind="parties", value=[deal.company, deal.counterparty],
            quote=text[opening:parties_end],
        ),
        FieldKey(
            field="effective_date", kind="date", value=deal.effective.isoformat(),
            quote=text[opening : effective.end()],
        ),
        FieldKey(
            field="governing_law", kind="jurisdiction", value=normalize_jurisdiction(deal.law),
            quote=_sentence_at(text, text.index(f"laws of {deal.law}")),
        ),
    ]
    if deal.value:
        sentence = f"The total value of this Agreement shall not exceed INR {deal.value:,.2f}."
        fields.append(
            FieldKey(field="value", kind="amount", value=f"INR {deal.value:,.2f}", quote=sentence)
        )
    if deal.end and not deal.auto_renew:
        fields.append(
            FieldKey(
                field="expiration_date", kind="date", value=deal.end.isoformat(),
                quote=f"continues until {_long_date(deal.end)}",
            )
        )
    if deal.auto_renew:
        renewal = re.search(
            r"continues until [^,]+, and then renews automatically.*?non-renewal", text
        ).group(0)
        for name in ("renewal_term", "notice_period"):
            fields.append(FieldKey(field=name, kind="passage", value=renewal, quote=renewal))
    for f in fields:
        if f.quote not in text:
            raise ValueError(f"{deal.doc_id}: the quote for {f.field} is not in the text")
    return ExtractionItem(
        item_id=f"ex-{deal.doc_id}",
        doc_id=deal.doc_id,
        category=SourceCategory.CONSTRUCTED,
        source="constructed",
        fields=fields,
    )


# --- scanned twins (FR-8) ------------------------------------------------------------------------


def scan_stamp(doc_id: str) -> str:
    """A DocuSign-style stamp, seeded by the document so every build writes the same one."""
    h = _rank(f"stamp:{doc_id}").upper()
    return f"{SCAN_STAMP}: {h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def render_scan(text: str, stamp: str) -> bytes:
    """The text laid out on A4 pages, each page rasterised to an image, in a PDF whose only text
    layer is one stamp line per page - what a scan of a signed contract looks like to the app,
    and how the two "stamp-only" owner scans read today.

    ponytail: byte-for-byte output depends on the PyMuPDF version (pinned in requirements.lock). A
    different version writes different bytes, which changes the file hash and so the exam's content
    fingerprint: loud, never silent. The version is recorded in manifest.json.
    """
    import pymupdf

    lines: list[str] = []
    for paragraph in text.split("\n"):
        lines += textwrap.wrap(paragraph, WRAP_CHARS) or [""]
    pdf = pymupdf.open()
    for first in range(0, len(lines), LINES_PER_PAGE):
        sheet = pymupdf.open().new_page(width=PAGE_W, height=PAGE_H)
        for n, line in enumerate(lines[first : first + LINES_PER_PAGE]):
            sheet.insert_text((50, 50 + 12 * n), line, fontsize=9, fontname="helv")
        png = sheet.get_pixmap(dpi=SCAN_DPI, colorspace=pymupdf.csGRAY).tobytes("png")
        page = pdf.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_image(page.rect, stream=png)
        page.insert_text((50, PAGE_H - 12), stamp, fontsize=6, fontname="helv")
    pdf.set_metadata({})
    return pdf.tobytes(deflate=True, garbage=3, no_new_id=True)


# --- AEGIS documents ---------------------------------------------------------------------------


def _aegis_severity(book: str, library: list[dict]) -> Callable[[str], tuple[str, str] | None]:
    def severity_of(rule: str):
        level = library_severity(library, book, rule)
        return (level, f"{book} / {rule}") if level else None

    return severity_of


def _template_label(kind: str) -> str:
    from app.drafting_templates.service import TEMPLATES

    return TEMPLATES[kind]["label"]


def _aegis_docs(library: list[dict], deals: dict[str, Deal], report: dict) -> list[_Doc]:
    """The 24 review documents (per type: one control, then three with 2-4 plants each), the 4 long
    documents and the 12 extra portfolio documents."""
    plans: dict[str, tuple[str, list[Plant]]] = {}
    skipped = report["rules_without_severity"]
    for kind, book in AEGIS_PLAYBOOK.items():
        severity_of = _aegis_severity(book, library)
        usable = [p for p in AEGIS_CATALOGUE[kind] if severity_of(p.rule)]
        skipped += [f"{book} / {p.rule}" for p in AEGIS_CATALOGUE[kind] if not severity_of(p.rule)]
        by_rule = {p.rule: p for p in usable}
        spread = assign_rules(list(by_rule), 3, f"ae-{kind}")
        for n in range(1, 5):
            chosen = [] if n == 1 else [by_rule[r] for r in spread[n - 2]]  # 01 is the control
            plans[f"ae-{kind}-{n:02d}"] = (kind, chosen)
    for doc_id, kind, plants in LONG_DOCS:
        book = AEGIS_PLAYBOOK[kind]
        severity_of = _aegis_severity(book, library)
        plans[doc_id] = (kind, [p for p in plants if severity_of(p.rule)])
        skipped += [f"{book} / {p.rule} (in {doc_id})" for p in plants if not severity_of(p.rule)]
    docs: list[_Doc] = []
    for doc_id in portfolio_ids():
        deal = deals[doc_id]
        title = f"{_template_label(deal.kind)} - {deal.counterparty}"
        steps = ["metadata", "clauses", "brain"]
        if doc_id.startswith("pf-"):
            text = _render(deal)
            docs.append(
                _Doc(doc_id, title, text, None, steps, "portfolio",
                     extraction=deal_extraction(deal, text))
            )
            continue
        kind, plants = plans[doc_id]
        book = AEGIS_PLAYBOOK[kind]
        final, applied = apply_plants(_render(deal), plants)
        changes = planted_changes(final, applied, _aegis_severity(book, library))
        listed = {p.rule for p in AEGIS_CATALOGUE[kind]}
        order = [
            r["clause_type"] for r in next(b for b in library if b["name"] == book)["rules"]
            if r["clause_type"] in listed
        ]
        if deal.long and any(c.char_start <= CUTOFF for c in changes.values()):
            raise ValueError(f"{doc_id}: a long document's plants must all start past 25,000")
        docs.append(
            _Doc(
                doc_id, title, final,
                ReviewItem(
                    item_id=f"rv-{doc_id}", doc_id=doc_id, category=SourceCategory.CONSTRUCTED,
                    source="constructed", playbook=book, control=not plants,
                    rules=_review_rules(order, changes),
                ),
                steps, "portfolio", extraction=deal_extraction(deal, final),
            )
        )
    return docs


def _scan_twins(docs: dict[str, _Doc]) -> list[_Doc]:
    twins = []
    for original_id in SCAN_TWINS:
        original = docs.get(original_id)
        if original is None:  # its Common Paper source is missing: reported, not replaced
            continue
        twins.append(
            _Doc(
                doc_id=f"scan-{original_id}",
                title=f"{original.title} (scanned)",
                text="",
                review=None,
                steps=["metadata"],
                dataset=original.dataset,
                twin_of=original_id,
                pdf=render_scan(original.text, scan_stamp(original_id)),
            )
        )
    return twins


# --- the portfolio questions (FR-9) ------------------------------------------------------------

# (question, fact key, who the answer is, and what a contract's own text says that makes it so). The
# list comes from the deals; the text check proves the rendered contracts say the same.
def _says_value_above_a_crore(text: str) -> bool:
    return any(
        int(amount.replace(",", "")) > ONE_CRORE
        for amount in re.findall(r"value of this Agreement shall not exceed INR ([\d,]+)\.00", text)
    )


# Every way the paper puts "this term continues unless someone says no". A plant or a template edit
# that adds one to a contract whose deal does not auto-renew makes the build refuse (the answer
# lists below are proved against these, not against one literal phrase).
_AUTO_RENEWAL = re.compile(
    r"renews automatically|automatically (?:renew|extend)|extended for a further"
)
_NON_RENEWAL_NOTICE = re.compile(
    r"gives (\d+) days' written notice of non-renewal"
    r"|notice of non-renewal at least [^.]{0,80}?\((\d+)\) days"
)


def _says_auto_renewal(text: str) -> bool:
    return _AUTO_RENEWAL.search(text) is not None


def _says_notice_of_90_days_or_more(text: str) -> bool:
    return any(int(m.group(1) or m.group(2)) >= 90 for m in _NON_RENEWAL_NOTICE.finditer(text))


PORTFOLIO_QUESTIONS = (
    ("Which contracts renew automatically?", "auto_renewal",
     lambda d: d.auto_renew, _says_auto_renewal),
    ("Which contracts are governed by the laws of England and Wales?", "governing_law",
     lambda d: d.law == "England and Wales", lambda t: "laws of England and Wales" in t),
    ("Which contracts are governed by the laws of Singapore?", "governing_law",
     lambda d: d.law == "Singapore", lambda t: "laws of Singapore" in t),
    ("Which contracts expire in 2027?", "expiry_year",
     lambda d: bool(d.end and d.end.year == 2027),
     lambda t: re.search(r"continues until \d{1,2} [A-Z][a-z]+ 2027\b", t) is not None),
    ("Which contracts have a total value above INR 1,00,00,000 (one crore)?", "value",
     lambda d: bool(d.value and d.value > ONE_CRORE), _says_value_above_a_crore),
    ("Which contracts need at least 90 days' notice to stop them renewing?", "renewal_notice",
     lambda d: d.auto_renew and d.notice_days >= 90, _says_notice_of_90_days_or_more),
    (f"Which contracts are with {KESTREL}?", "counterparty",
     lambda d: d.counterparty == KESTREL, lambda t: KESTREL in t),
    ("Which contracts are non-disclosure agreements?", "agreement_type",
     lambda d: d.kind == "nda", lambda t: "NON-DISCLOSURE AGREEMENT" in t.split("\n", 1)[0]),
    # Last, so the fixed sample (the first questions a six-contract sub-portfolio can partly
    # answer) does not move. 33 of the 40: far more than the 8 passages a search returns per lens,
    # which is the point -- a list this long is only right if every contract was checked.
    ("Which contracts are governed by Indian law?", "governing_law",
     lambda d: d.law == "India", lambda t: "laws of India" in t),
)


def portfolio_questions(deals: dict[str, Deal], texts: dict[str, str]) -> list[PortfolioQuestion]:
    """The 9 questions with their exact answer lists. Each list is computed from the deals and then
    checked both ways against the contracts' own text: every listed contract says the fact and no
    other one does (so a wrong key cannot get in)."""
    questions = []
    for number, (question, fact, holds, says) in enumerate(PORTFOLIO_QUESTIONS, 1):
        expected = [i for i, d in deals.items() if holds(d)]
        said = [i for i in deals if says(texts[i])]
        if said != expected:
            raise ValueError(
                f"portfolio question {number}: the texts say {said}, the deals {expected}"
            )
        if not expected:
            raise ValueError(f"portfolio question {number} has an empty answer list")
        questions.append(
            PortfolioQuestion(
                item_id=f"pq-{number:02d}", question=question, fact=fact, expected_doc_ids=expected
            )
        )
    return questions


# --- the constructed build -----------------------------------------------------------------------


@dataclass
class ConstructedBuild:
    documents: list[ExamDocument] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)
    # Extraction keys of the constructed contracts, written after the CUAD ones in
    # items/extraction.json (scan twins have none: they are scored against their original's).
    extraction: list[ExtractionItem] = field(default_factory=list)
    portfolio: list[PortfolioQuestion] = field(default_factory=list)
    playbooks: dict[str, dict] = field(default_factory=dict)  # cp_msa, cp_dpa
    texts: dict[str, bytes] = field(default_factory=dict)  # path under DOCS_DIR -> file bytes
    deals: dict[str, Deal] = field(default_factory=dict)
    report: dict = field(default_factory=dict)


def _exam_document(doc: _Doc) -> tuple[ExamDocument, str, bytes]:
    if doc.pdf is not None:
        rel, data, mime = f"constructed/{doc.doc_id}.pdf", doc.pdf, "application/pdf"
    else:
        rel, data, mime = (*_write_text_file(doc), "text/plain")
    built = ExamDocument(
        doc_id=doc.doc_id,
        category=SourceCategory.CONSTRUCTED,
        dataset=doc.dataset,
        file=rel,
        mime_type=mime,
        file_sha256=hashlib.sha256(data).hexdigest(),
        title=doc.title,
        uploader=doc.uploader,
        twin_of=doc.twin_of,
        ai_steps=doc.steps,
    )
    return built, rel, data


def build_constructed(
    raw_dir: Path = RAW_DIR, *, library: list[dict] | None = None
) -> ConstructedBuild:
    """Build every constructed set. Pure: writes nothing, asks no model, needs no network.

    ``library`` is the platform playbook library the severities are read from (the real one by
    default). A rule that is not in it, or has no severity, gets no plant and is named in
    ``report["rules_without_severity"]`` (spec edge case). A missing Common Paper download leaves
    those documents and their twins out and is named in ``report["missing"]``.
    """
    library = _library() if library is None else library
    out = ConstructedBuild()
    report = out.report
    report["missing"], report["rules_without_severity"] = [], []
    deals = out.deals = make_deals()

    cp = _cp_docs(raw_dir, library, report)
    if cp:
        standard = {k: next(d.text for d in cp if d.doc_id == f"cp-{k}-01") for k in ("msa", "dpa")}
        out.playbooks["cp_msa"] = _cp_playbook(
            CP_MSA_RULES, _CSA_PLANTS, standard["msa"], library, "Common Paper CSA review (exam)",
            "Exam playbook for the Common Paper Cloud Service Agreement. Each rule's severity is "
            "the one set on the platform playbook rule it names in severity_from.",
        )
        out.playbooks["cp_dpa"] = _cp_playbook(
            CP_DPA_RULES, _DPA_PLANTS, standard["dpa"], library, "Common Paper DPA review (exam)",
            "Exam playbook for the Common Paper Data Processing Agreement. Each rule's severity is "
            "the one set on the platform playbook rule it names in severity_from.",
        )
    aegis = _aegis_docs(library, deals, report)
    everything = {d.doc_id: d for d in [*cp, *aegis]}
    twins = _scan_twins(everything)
    docs = [*cp, *aegis, *twins]

    for doc in docs:
        built, rel, data = _exam_document(doc)
        out.documents.append(built)
        out.texts[rel] = data
        if doc.review:
            out.review.append(doc.review)
        if doc.extraction:
            out.extraction.append(doc.extraction)
    out.portfolio = portfolio_questions(deals, {i: everything[i].text for i in deals})

    changes = [r.planted for item in out.review for r in item.rules if r.planted]
    past = [c for c in changes if c.char_start > CUTOFF]
    if len(past) < MIN_PLANTS_PAST_25K:
        raise ValueError(
            f"only {len(past)} planted changes start past 25,000, need {MIN_PLANTS_PAST_25K}"
        )
    long_lengths = {d.doc_id: len(d.text) for d in aegis if d.doc_id.startswith("ae-long-")}
    if any(n <= MIN_LONG_CHARS for n in long_lengths.values()):
        raise ValueError(f"a long document is under {MIN_LONG_CHARS} characters: {long_lengths}")
    report["documents"] = {
        "common_paper": len(cp),
        "aegis_review": len([d for d in aegis if d.review and not d.doc_id.startswith("ae-long-")]),
        "aegis_long": len(long_lengths),
        "scan_twins": [d.doc_id for d in twins],
        "portfolio_extra": len([d for d in aegis if d.doc_id.startswith("pf-")]),
        "portfolio_total": len(deals),
    }
    report["review"] = {
        "documents": len(out.review),
        "controls": sum(i.control for i in out.review),
        "common_paper_controls": sum(i.control for i in out.review if i.playbook.startswith("cp_")),
        "aegis_controls": sum(i.control for i in out.review if not i.playbook.startswith("cp_")),
    }
    report["plants"] = {
        "total": len(changes),
        "by_severity": dict(sorted(Counter(c.severity for c in changes).items())),
        "by_status": dict(sorted(Counter(c.status for c in changes).items())),
        "past_25k": len(past),
        "past_25k_documents": sorted(
            {
                i.doc_id for i in out.review for r in i.rules
                if r.planted and r.planted.char_start > CUTOFF
            }
        ),
    }
    report["long_document_lengths"] = long_lengths
    report["portfolio"] = {q.item_id: len(q.expected_doc_ids) for q in out.portfolio}
    report["extraction"] = {
        "items": len(out.extraction),
        "fields": sum(len(i.fields) for i in out.extraction),
    }
    return out


# --- the fixed sample (FR-30) ------------------------------------------------------------------

SAMPLE_CUAD = 3
SAMPLE_PAST_QA = 4  # at least this many sampled questions have their answer past 25k
SAMPLE_PORTFOLIO_DOCS = ("ae-msa-01", "ae-msa-02", "ae-long-01", "pf-02", "pf-03", "pf-05")
SAMPLE_REVIEW_OTHERS = (
    "rv-cnli-01", "rv-ae-msa-01", "rv-ae-msa-02", "rv-scan-ae-msa-02", "rv-ae-long-01",
)
SAMPLE_ROUTING = tuple(f"rt-{n:02d}" for n in range(1, 11))


def make_sample(public: PublicBuild, built: ConstructedBuild) -> dict[str, list[str]]:
    """The small subset that sample mode runs: some of every task type and some items past 25k.

    The three CUAD contracts are the ones asked the most questions, preferring a mix of questions
    answered before and past 25k, so both buckets of the question-answering split have members. The
    scanned twin of a review document is a review unit of its own (``rv-`` + its document id): it
    has no answer record, it shares its original's. Extraction is asked of every sampled review
    document that has a key (the three CUAD contracts, the AEGIS control, the planted Indian one and
    the long one; a ContractNLI document and a scanned twin have none of their own). The owner
    documents (one ordinary, one stamp-only with its "as read today" twin) are picked by the runner
    from the owner manifest, which is private, so they are not named here.
    """
    text = {
        d.doc_id: public.texts[d.file].decode("utf-8")
        for d in public.documents
        if d.file in public.texts
    }

    def start_of(question: QAItem) -> int:
        return min(text[question.doc_id].find(p) for p in question.passages)

    # Without the CUAD and LegalBench-RAG sets (named under "missing" in the build's report) the
    # sample has no CUAD review, extraction or Q&A items. It is not refused: the constructed sets
    # still have to land.
    asked: Counter = Counter()
    answered_past: Counter = Counter()
    chosen: list[str] = []
    if {"cuad", "qa"} <= public.built:
        asked = Counter(q.doc_id for q in public.qa)
        answered_past = Counter(q.doc_id for q in public.qa if start_of(q) >= CUTOFF)
        chosen = sorted(
            asked,
            key=lambda d: (-asked[d], -min(answered_past[d], asked[d] - answered_past[d]), d),
        )[:SAMPLE_CUAD]

    def past_labels(doc_id: str) -> int:
        quotes = [r.evidence_quote for i in public.review if i.doc_id == doc_id for r in i.rules]
        quotes += [f.quote for i in public.extraction if i.doc_id == doc_id for f in i.fields]
        return sum(text[doc_id].find(q) >= CUTOFF for q in quotes if q)

    if chosen and (
        sum(past_labels(d) > 0 for d in chosen) < 2
        or sum(answered_past[d] for d in chosen) < SAMPLE_PAST_QA
    ):
        raise ValueError(f"the sample CUAD contracts {chosen} do not reach past 25,000 enough")
    known = {i.item_id for i in [*public.review, *built.review]}
    known |= {f"rv-{d.doc_id}" for d in built.documents if d.twin_of}
    review = [f"rv-{d}" for d in sorted(chosen)] + [i for i in SAMPLE_REVIEW_OTHERS if i in known]
    keyed = {i.item_id for i in [*public.extraction, *built.extraction]}
    wanted = set(SAMPLE_PORTFOLIO_DOCS)
    return {
        "review": review,
        "extraction": [f"ex-{i[3:]}" for i in review if f"ex-{i[3:]}" in keyed],
        "qa": [q.item_id for q in public.qa if q.doc_id in chosen],
        # the first two questions the six-contract sub-portfolio can answer in part
        "portfolio": [
            q.item_id
            for q in built.portfolio
            if 0 < len(set(q.expected_doc_ids) & wanted) < len(wanted)
        ][:2],
        "routing": list(SAMPLE_ROUTING),
        "portfolio_docs": list(SAMPLE_PORTFOLIO_DOCS),
    }


# --- the build manifest and the files ------------------------------------------------------------


def make_manifest(public: PublicBuild, built: ConstructedBuild) -> dict:
    """What `build` did, so the committed record says how the content was made. Deterministic:
    nothing here may change between two builds of the same inputs (it is in the content
    fingerprint)."""
    import pymupdf

    from app.drafting_templates.service import TEMPLATES, default_body

    book_names = sorted({*AEGIS_PLAYBOOK.values(), *(r[1] for r in (*CP_MSA_RULES, *CP_DPA_RULES))})
    return {
        "schema": "aegis-exam-manifest/1",
        "seed": SEED,
        "public": public.report,
        "constructed": built.report,
        "templates": {
            key: hashlib.sha256(default_body(key).encode("utf-8")).hexdigest()
            for key in sorted(TEMPLATES)
        },
        "severity_source": {
            "module": "app/playbooks/library.py",
            "playbooks": book_names,
        },
        "pymupdf": pymupdf.__version__,
    }


def write_constructed(
    public: PublicBuild,
    built: ConstructedBuild,
    *,
    content_dir: Path = CONTENT_DIR,
    docs_dir: Path = DOCS_DIR,
) -> list[Path]:
    """Write the constructed sets and the files that merge them with the public ones
    (documents.json, items/review.json, items/extraction.json - public rows first), the portfolio,
    the Common Paper playbooks, the sample and the manifest, and the contract files under
    ``docs_dir``."""
    written: list[Path] = []

    def put(rel: str, value) -> None:
        _write_json(content_dir / rel, value)
        written.append(content_dir / rel)

    documents = [*public.documents, *built.documents]
    put("documents.json", [d.model_dump(mode="json") for d in documents])
    put("items/review.json", [i.model_dump(mode="json") for i in [*public.review, *built.review]])
    put(
        "items/extraction.json",
        [i.model_dump(mode="json") for i in [*public.extraction, *built.extraction]],
    )
    put("items/portfolio.json", [q.model_dump(mode="json") for q in built.portfolio])
    for key, playbook in built.playbooks.items():
        put(f"playbooks/{key}.json", playbook)
    put("sample.json", make_sample(public, built))
    put("manifest.json", make_manifest(public, built))
    for rel, data in built.texts.items():
        (docs_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (docs_dir / rel).write_bytes(data)
    return written


def format_constructed_report(built: ConstructedBuild) -> str:
    """The constructed build's counts as `build` prints them: documents per set, controls, plants
    by severity, plants past 25k, scan twins, and anything that was missing or left unplanted."""
    r = built.report
    lines = ["constructed sets built:"]
    for key in ("documents", "review", "extraction", "plants", "portfolio", "long_document_lengths"):
        lines.append(f"  {key}: {json.dumps(r[key], sort_keys=True)}")
    if r["rules_without_severity"]:
        lines.append(
            f"NO CHANGE PLANTED (the platform rule has no severity): {r['rules_without_severity']}"
        )
    if r["missing"]:
        lines.append(f"MISSING datasets (their documents were not built): {r['missing']}")
    return "\n".join(lines)


def run_constructed(
    raw_dir: Path = RAW_DIR,
    *,
    download: bool = True,
    content_dir: Path = CONTENT_DIR,
    docs_dir: Path = DOCS_DIR,
    with_public: bool = False,
) -> ConstructedBuild:
    """Download what is missing, build the public sets (needed to merge documents.json,
    items/review.json and items/extraction.json), build the constructed sets, write them, print the
    counts. ``with_public`` is the full `build`: it also writes the purely public files and prints
    the public counts first, so one run leaves every content file fresh and one report behind
    (`--only constructed` merges a public build in memory but leaves the public files as they are).
    """
    if download:
        for problem in download_datasets(raw_dir):
            print(f"download failed: {problem}")
    public = build_public(raw_dir)
    built = build_constructed(raw_dir)
    if with_public:
        write_public(public, content_dir=content_dir, docs_dir=docs_dir)
        print(format_report(public))
    write_constructed(public, built, content_dir=content_dir, docs_dir=docs_dir)
    print(format_constructed_report(built))
    if public.report["missing"]:
        print(
            f"MISSING public datasets {public.report['missing']}: their documents and items are "
            "left out of documents.json, items/review.json, items/extraction.json and sample.json"
        )
    return built
