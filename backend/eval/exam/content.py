"""The exam's content: frozen paths, the owner-file allowlist, models, loaders, fingerprints.

Every other exam module imports its paths and models from here and must not edit it
(specs/005-ai-eval-baseline/plan.md, "Python interfaces between exam modules"). Stdlib +
pydantic only, on purpose: the exam process has to repoint the app's settings at the throwaway
database BEFORE any ``app.*`` module is imported, so importing this module must never pull the
app in (``tests/test_exam_content.py`` runs that check in a fresh interpreter).

On-disk layout read by ``load_content()``. Committed (``CONTENT_DIR``, written by ``build``)::

    datasets.json            list[Dataset]
    documents.json           list[ExamDocument]       public + constructed (owner ones come
                                                      from the private manifest, below)
    sample.json              dict[str, list[str]]     Task value -> item ids, + "portfolio_docs"
    manifest.json            dict                     build record (opaque to this module)
    items/review.json        list[ReviewItem]
    items/extraction.json    list[ExtractionItem]
    items/qa.json            list[QAItem]
    items/portfolio.json     list[PortfolioQuestion]
    playbooks/<key>.json     dict                     one playbook per file, key = file stem

Private (``PRIVATE_DIR``, git- and docker-ignored; absent files load as empty)::

    owner/manifest.json      list[OwnerManifestEntry]
    routing/requests.json    list[RoutingRequest]
    routing/workflows.json   dict                     the workflow snapshot (has "fingerprint")
    keys/owner_facts.json    list[OwnerFactKey]
    keys/routing.json        list[RoutingKey]
"""

import hashlib
import json
import unicodedata
from collections import Counter
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

EVAL_DIR = Path(__file__).resolve().parent.parent  # backend/eval
EXAM_DIR = EVAL_DIR / "exam"
CONTENT_DIR = EXAM_DIR / "content"
REPORTS_DIR = EXAM_DIR / "reports"
DOCS_DIR = EVAL_DIR / "contracts"
RAW_DIR = DOCS_DIR / "raw"
PRIVATE_DIR = EVAL_DIR / "private"
WORK_DIR = EVAL_DIR / ".work"
APP_DIR = EVAL_DIR.parent / "app"

CUTOFF = 25_000
PREFILL_MODEL = "claude-sonnet-5-5"
ROUTING_DRAFT_MODEL = "claude-sonnet-5-5"

# FR-15: exactly these, in this order -> owner-01 .. owner-14. Nothing else in the owner's
# folder may be opened; owner_file_path is the only way the exam reaches that folder.
OWNER_FILES: tuple[str, ...] = (
    "Employment Agreement.pdf",
    "Franklin Madison - MSA - 1-23-19.pdf",
    "INFRAHEDGE-MSA.pdf",
    "IT Services Agreement - ABC_signed.pdf",
    "MSA - Hearst.pdf",
    "MSA.Pdf",
    "MSA_1.Pdf",
    "MSA_2020_6_000676.Pdf",
    "MSA_2021_11_000942.Pdf",
    "MSA_AAA PHARMACEUTICALS.pdf",
    "MSA_Signed.pdf",
    "Mutual NDA — Northwind Robotics Inc..docx",
    "NDA Form.pdf",
    "Patent_License_Agreement 1.pdf",
)


class OwnerFileRefused(Exception):
    """A file outside the 14 allowed names was asked for (FR-15)."""


def _nfc(name: str) -> str:
    return unicodedata.normalize("NFC", name)


def owner_file_path(src_dir: Path, name: str) -> Path:
    """Resolve one of the 14 allowed owner files inside ``src_dir``.

    The allowlist check comes first and touches no file, so a personal document sitting in
    the same folder is never stat'ed, let alone opened. macOS hands back NFD file names and
    Linux bind mounts keep them, so the same name is tried in NFC then NFD spelling. The
    folder is never listed.
    """
    canonical = _nfc(name)
    if canonical not in {_nfc(n) for n in OWNER_FILES}:
        # Not echoing the name: a refused name is by definition someone's other file.
        raise OwnerFileRefused("not one of the 14 owner contract files; refused without opening it")
    for form in ("NFC", "NFD"):
        candidate = Path(src_dir) / unicodedata.normalize(form, canonical)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{canonical} is not in {src_dir}")


class SourceCategory(StrEnum):
    PUBLIC = "public"
    CONSTRUCTED = "constructed"
    OWNER = "owner"


class Task(StrEnum):
    REVIEW = "review"
    EXTRACTION = "extraction"
    QA = "qa"
    PORTFOLIO = "portfolio"
    ROUTING = "routing"


Severity = Literal["low", "medium", "high", "critical"]
# notice_period (D-14) = the notice needed to end the contract or to stop it renewing — one field.
FieldName = Literal[
    "parties",
    "effective_date",
    "expiration_date",
    "end_date_or_term",
    "governing_law",
    "value",
    "renewal_term",
    "notice_period",
    "liability_cap",
    "termination_for_convenience",
]


class _Model(BaseModel):
    # extra="forbid": these models ARE the frozen file formats, so a renamed or invented field
    # fails the load instead of being dropped on the way to a run.
    model_config = ConfigDict(extra="forbid")


class DatasetFile(_Model):
    path: str  # relative to RAW_DIR
    sha256: str | None


class Dataset(_Model):
    key: Literal["cuad", "contractnli", "legalbench_rag", "commonpaper"]
    name: str
    version: str
    licence: str
    url: str
    files: list[DatasetFile]
    available: bool  # False => the set is reported missing, never substituted


class ExamDocument(_Model):
    # cuad-001 | cnli-01 | cp-msa-01 | cp-dpa-01 | ae-<type>-01 | ae-long-01 | scan-<doc_id> |
    # pf-01 | owner-01 | owner-01-asread
    doc_id: str
    category: SourceCategory
    dataset: str | None  # Dataset.key for public documents
    file: str  # relative to DOCS_DIR, or to PRIVATE_DIR/"owner/files" for owner documents
    mime_type: str
    file_sha256: str
    title: str  # what an upload of this file would be titled (the file stem)
    uploader: Literal["runner", "portfolio"] = "runner"
    text_mode: Literal["app", "forced_ocr"] = "app"  # forced_ocr only for stamp-only owner docs
    twin_of: str | None = None  # a scan twin points at its text original
    as_read_today_of: str | None = None  # owner-NN-asread -> owner-NN
    ai_steps: list[Literal["metadata", "clauses", "brain"]]  # embeddings always run (free)


class PlantedChange(_Model):
    rule: str  # PlaybookRule.clause_type the change breaks
    status: Literal["deviation", "missing"]
    original_text: str | None  # text before the change
    changed_text: str | None  # text after the change (None for deletions)
    anchor_text: str  # >= 60 chars immediately before the change point (locates deletions)
    char_start: int  # offset of the change point in the generated text
    severity: Severity  # copied from the platform rule at build time, never by an AI
    severity_source: str  # "<library playbook name> / <clause_type>"


class ReviewRuleKey(_Model):
    rule: str
    status: Literal["compliant", "deviation", "missing"]
    evidence_quote: str | None = None
    severity: Severity | None = None  # set only for constructed plants
    planted: PlantedChange | None = None


class ReviewItem(_Model):
    item_id: str  # rv-<doc_id>
    doc_id: str
    category: SourceCategory
    source: str  # dataset name + version, or "constructed"
    # exam playbook key (nda_checklist | cuad_checklist | cp_msa | cp_dpa) or the library
    # playbook's name ("Master Services Agreement Playbook", ...)
    playbook: str
    control: bool = False  # unchanged control document
    rules: list[ReviewRuleKey]


class FieldKey(_Model):
    field: FieldName
    kind: Literal["parties", "date", "date_or_term", "jurisdiction", "amount", "passage"]
    value: str | list[str] | None  # None = "not stated in the document"; parties = list
    quote: str | None  # exact supporting text (None when not stated)


class ExtractionItem(_Model):
    item_id: str  # ex-<doc_id>
    doc_id: str
    category: SourceCategory
    source: str
    fields: list[FieldKey]


class QAItem(_Model):
    item_id: str  # qa-001
    doc_id: str
    category: SourceCategory
    source: str  # "LegalBench-RAG <version> / cuad"
    question: str
    answer: str
    passages: list[str]  # exact answer passages
    # build asserts (doc_id, question) is unique, so no two items send an identical request


class PortfolioQuestion(_Model):
    item_id: str  # pq-01
    question: str
    fact: str  # fact key used to compute the list (documentation)
    expected_doc_ids: list[str]  # exact correct list (full portfolio)


class RoutingRequest(_Model):  # private: eval/private/routing/requests.json
    item_id: str  # rt-01
    channel: Literal["email", "chat", "notice"]
    subject: str
    body: str
    drafted_for_workflow_id: str
    drafting_model: str  # inputs only, never the key


class OwnerFactKey(_Model):  # private: eval/private/keys/owner_facts.json
    doc_id: str
    field: FieldName
    status: Literal["confirmed", "corrected", "unmarked"]
    value: str | list[str] | None  # final value (None = not stated); ignored when unmarked
    quote: str | None
    char_start: int | None
    char_end: int | None
    suggested_value: str | list[str] | None
    suggested_quote: str | None
    suggested_by: str  # model id (PREFILL_MODEL) or "previous key"
    file_sha256: str
    frozen_text_sha256: str
    sheet_sha256: str
    loaded_at: datetime


class RoutingKey(_Model):  # private: eval/private/keys/routing.json
    item_id: str
    status: Literal["confirmed", "corrected", "unmarked"]
    workflow_id: str | None  # None = "no workflow — a person should route it"
    workflow_fingerprint: str
    sheet_sha256: str
    loaded_at: datetime
    # Beyond the frozen list (additive, defaulted): ``routing_requests_sha256`` of the request
    # texts the owner picked against. The workflow fingerprint alone cannot tell a re-drafted
    # set (same ids, same workflows, new texts) from the one the pick was made on.
    requests_sha256: str | None = None


class OwnerManifestEntry(_Model):
    """One row of ``PRIVATE_DIR/owner/manifest.json`` (written by ``owner-import``).

    Not in the frozen interface list: ``content_fingerprint`` must include each owner text's
    frozen hash, and the owner documents themselves are derived from these rows, so the loader
    owns the format. Validating the name here means a hand-edited manifest cannot smuggle a
    fifteenth file into a run.
    """

    doc_id: str  # owner-01 .. owner-14, the file's position in OWNER_FILES
    name: str
    file_sha256: str
    mime_type: str
    pages: int
    kind: Literal["text", "scan", "stamp_only_scan"]
    frozen_text_sha256: str | None = None
    frozen_method: str | None = None
    imported_at: datetime

    @model_validator(mode="after")
    def _is_one_of_the_14(self) -> Self:
        names = [_nfc(n) for n in OWNER_FILES]
        name = _nfc(self.name)
        if name not in names or self.doc_id != f"owner-{names.index(name) + 1:02d}":
            raise ValueError("owner manifest entry is not one of the 14 allowed files")
        return self


class ExamContent(_Model):
    datasets: list[Dataset] = Field(default_factory=list)
    documents: list[ExamDocument] = Field(default_factory=list)
    review: list[ReviewItem] = Field(default_factory=list)
    extraction: list[ExtractionItem] = Field(default_factory=list)
    qa: list[QAItem] = Field(default_factory=list)
    portfolio: list[PortfolioQuestion] = Field(default_factory=list)
    routing: list[RoutingRequest] = Field(default_factory=list)
    owner_keys: list[OwnerFactKey] = Field(default_factory=list)
    routing_keys: list[RoutingKey] = Field(default_factory=list)
    playbooks: dict[str, dict] = Field(default_factory=dict)  # exam playbook key -> JSON
    sample: dict[str, list[str]] = Field(default_factory=dict)  # Task value -> item ids
    workflow_snapshot: dict | None = None
    # Beyond the frozen list (additive, defaulted): the loaded owner manifest and the build
    # manifest, so the fingerprint is computed from the content object alone.
    owner_manifest: list[OwnerManifestEntry] = Field(default_factory=list)
    manifest: dict = Field(default_factory=dict)


def _json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def _models(model: type[BaseModel], path: Path) -> list:
    return TypeAdapter(list[model]).validate_python(_json(path, []))


def _owner_documents(manifest: list[OwnerManifestEntry]) -> list[ExamDocument]:
    """Owner files as exam documents, each stamp-only scan followed by its "as read today" twin.

    A stamp-only scan's main document is read from the forced OCR text; the twin reads the same
    file the way the app does today, so the two are scored on separate lines (FR-23).
    """
    docs: list[ExamDocument] = []
    for entry in manifest:
        stamp_only = entry.kind == "stamp_only_scan"
        base = {
            "category": SourceCategory.OWNER,
            "dataset": None,
            "file": entry.name,
            "mime_type": entry.mime_type,
            "file_sha256": entry.file_sha256,
            "title": Path(entry.name).stem,
            "ai_steps": ["metadata", "clauses"],
        }
        docs.append(
            ExamDocument(
                doc_id=entry.doc_id,
                text_mode="forced_ocr" if stamp_only else "app",
                **base,
            )
        )
        if stamp_only:
            docs.append(
                ExamDocument(doc_id=f"{entry.doc_id}-asread", as_read_today_of=entry.doc_id, **base)
            )
    return docs


def routing_requests_sha256(requests: list[dict]) -> str:
    """sha256 of the drafted requests exactly as the owner saw them (``RoutingRequest`` dumps).

    By item id, over what a pick depends on: the id, the channel, the text and the workflow the
    request was drafted for. The drafting model is provenance, not part of the question.
    """
    rows = sorted(
        (
            {
                k: r[k]
                for k in ("item_id", "channel", "subject", "body", "drafted_for_workflow_id")
            }
            for r in requests
        ),
        key=lambda r: r["item_id"],
    )
    return hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()


def _check_consistency(content: ExamContent) -> None:
    """Refuse content whose ids disagree with each other.

    A dangling doc_id would not fail anywhere later: the runner would plan a unit for a
    document it never ingested and report it "not attempted" in every run.
    """
    problems: list[str] = []
    doc_ids = [d.doc_id for d in content.documents]
    known = set(doc_ids)
    problems += [f"duplicate doc_id {i}" for i, n in Counter(doc_ids).items() if n > 1]
    item_ids = [
        i.item_id
        for group in (content.review, content.extraction, content.qa, content.portfolio)
        for i in group
    ]
    problems += [f"duplicate item_id {i}" for i, n in Counter(item_ids).items() if n > 1]
    for group in (content.review, content.extraction, content.qa):
        problems += [
            f"{i.item_id}: unknown doc_id {i.doc_id}" for i in group if i.doc_id not in known
        ]
    for q in content.portfolio:
        problems += [
            f"{q.item_id}: unknown doc_id {d}" for d in q.expected_doc_ids if d not in known
        ]
    for d in content.documents:
        for ref in (d.twin_of, d.as_read_today_of):
            if ref is not None and ref not in known:
                problems.append(f"{d.doc_id}: points at unknown document {ref}")

    # Private answers. A key for a request or document that is not there is scored against
    # nothing and reads as "unpicked"/"unkeyed" forever; a duplicate would count twice.
    requests = [r.item_id for r in content.routing]
    request_ids = set(requests)
    problems += [f"duplicate routing request {i}" for i, n in Counter(requests).items() if n > 1]
    problems += [
        f"duplicate routing key for {i}"
        for i, n in Counter(k.item_id for k in content.routing_keys).items()
        if n > 1
    ]
    problems += [
        f"routing key for unknown request {k.item_id}"
        for k in content.routing_keys
        if k.item_id not in request_ids
    ]
    picked_on = {k.requests_sha256 for k in content.routing_keys} - {None}
    if picked_on and requests and picked_on != {
        routing_requests_sha256([r.model_dump() for r in content.routing])
    }:
        problems.append("the routing keys were picked against different request texts")
    imported = {e.doc_id for e in content.owner_manifest}
    problems += [
        f"duplicate owner key for {doc} / {field}"
        for (doc, field), n in Counter((k.doc_id, k.field) for k in content.owner_keys).items()
        if n > 1
    ]
    problems += [
        f"owner key for {k.doc_id} / {k.field}: not an imported owner document"
        for k in content.owner_keys
        if k.doc_id not in imported
    ]
    if problems:
        raise ValueError("exam content is inconsistent: " + "; ".join(problems))


def load_content() -> ExamContent:
    """Committed content plus the private files that exist. Missing files load as empty.

    An empty exam is caught by the fingerprint (it differs) and by the runner (nothing to
    plan); a malformed or inconsistent file raises here, before any money is estimated.
    """
    items = CONTENT_DIR / "items"
    owner_manifest = _models(OwnerManifestEntry, PRIVATE_DIR / "owner" / "manifest.json")
    content = ExamContent(
        datasets=_models(Dataset, CONTENT_DIR / "datasets.json"),
        documents=[
            *_models(ExamDocument, CONTENT_DIR / "documents.json"),
            *_owner_documents(owner_manifest),
        ],
        review=_models(ReviewItem, items / "review.json"),
        extraction=_models(ExtractionItem, items / "extraction.json"),
        qa=_models(QAItem, items / "qa.json"),
        portfolio=_models(PortfolioQuestion, items / "portfolio.json"),
        routing=_models(RoutingRequest, PRIVATE_DIR / "routing" / "requests.json"),
        owner_keys=_models(OwnerFactKey, PRIVATE_DIR / "keys" / "owner_facts.json"),
        routing_keys=_models(RoutingKey, PRIVATE_DIR / "keys" / "routing.json"),
        playbooks={
            p.stem: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((CONTENT_DIR / "playbooks").glob("*.json"))
        },
        sample=_json(CONTENT_DIR / "sample.json", {}),
        workflow_snapshot=_json(PRIVATE_DIR / "routing" / "workflows.json", None),
        owner_manifest=owner_manifest,
        manifest=_json(CONTENT_DIR / "manifest.json", {}),
    )
    _check_consistency(content)
    return content


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# Who suggested a value and which sheet carried it say nothing about the exam itself, and
# reloading an identical sheet must not retire a baseline.
_KEY_PROVENANCE = (
    "suggested_value",
    "suggested_quote",
    "suggested_by",
    "sheet_sha256",
    "loaded_at",
    "requests_sha256",  # derived from the routing requests, which are hashed themselves
)


def content_fingerprint(content: ExamContent) -> str:
    """sha256 over everything that decides what the exam asks and what counts as right.

    Items, documents (file hashes), playbooks, the build manifest, the sample subset, the
    owner files' frozen-text hashes, the owner and routing keys, the routing requests and the
    workflow snapshot's fingerprint. Any difference here forces a new baseline (FR-27).
    """
    data = content.model_dump(mode="json")
    for key in (*data["owner_keys"], *data["routing_keys"]):
        for field in _KEY_PROVENANCE:
            key.pop(field, None)
    for entry in data["owner_manifest"]:
        del entry["imported_at"]
    snapshot = data.pop("workflow_snapshot")
    data["workflow_fingerprint"] = snapshot.get("fingerprint") if snapshot else None
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def app_code_sha256() -> str:
    """sha256 over the sorted (path, bytes) of every ``backend/app/**/*.py``: "same code"."""
    digest = hashlib.sha256()
    files = sorted(APP_DIR.rglob("*.py"), key=lambda p: p.relative_to(APP_DIR).as_posix())
    for path in files:
        data = path.read_bytes()
        # Length-prefixed so one file's tail cannot be mistaken for the next file's header.
        digest.update(f"{path.relative_to(APP_DIR).as_posix()}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()
