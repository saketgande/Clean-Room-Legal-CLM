"""Exam content module: owner-file allowlist, models, loaders, fingerprints, public sets and the
constructed sets.

specs/005-ai-eval-baseline T003 (FR-1, FR-2, FR-15, FR-20, FR-27, FR-36), T008 (FR-2, FR-4: the
public sets and their builder) and T012 (FR-5..FR-9, FR-30: the constructed contracts, their
planted changes and severities, the scanned twins, the portfolio and the sample). Pure
file-and-hash tests: no database, no network, nothing paid - the downloader is only ever driven
with a fake fetch. Tests that need the downloaded datasets or the generated contract files skip
themselves when those are absent (CI has neither; the committed JSON is checked either way).
"""

import builtins
import copy
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from app.contract_brain.clause_taxonomy import canonical_clause_type
from app.playbooks.library import PLAYBOOK_LIBRARY
from eval.exam import build as b
from eval.exam import content as c

BACKEND_DIR = Path(__file__).resolve().parent.parent


# --- owner-file allowlist (FR-15) -------------------------------------------------------


def test_owner_allowlist_is_the_fourteen_files_in_order():
    """The list is the contract with the owner: a dropped, added or reordered name would
    renumber owner-NN documents and silently orphan every confirmed key."""
    assert len(c.OWNER_FILES) == 14
    assert len(set(c.OWNER_FILES)) == 14
    assert c.OWNER_FILES[0] == "Employment Agreement.pdf"
    assert c.OWNER_FILES[11] == "Mutual NDA — Northwind Robotics Inc..docx"
    assert c.OWNER_FILES[-1] == "Patent_License_Agreement 1.pdf"


class _Untouchable:
    """A folder the exam must not reference at all: any use of it blows up differently from a
    refusal, so a pass proves the refusal came first."""

    def __truediv__(self, other):
        raise AssertionError("src_dir was used before the allowlist check")

    def __getattr__(self, name):
        raise AssertionError("src_dir was used before the allowlist check")


@pytest.mark.parametrize(
    "name",
    [
        "personal-taxes.pdf",
        "../Employment Agreement.pdf",
        "employment agreement.pdf",  # case differs: a different file on a case-sensitive disk
        "Employment Agreement.pdf.bak",
        "",
    ],
)
def test_unlisted_name_is_refused_before_the_folder_is_touched(name, monkeypatch):
    """AC-15: a personal document in the owner's folder must be refused without being
    opened, statted or even looked for."""
    opened = []
    with monkeypatch.context() as m:
        m.setattr(builtins, "open", lambda *a, **k: opened.append(a))
        m.setattr(Path, "open", lambda *a, **k: opened.append(a))
        with pytest.raises(c.OwnerFileRefused) as err:
            c.owner_file_path(_Untouchable(), name)
    assert opened == []
    assert name == "" or name not in str(err.value)  # the refused name is not echoed


def test_allowed_names_resolve_and_the_folder_is_never_listed(tmp_path, monkeypatch):
    """The folder holds the owner's other files; listing it would expose their names. Each of
    the 14 must resolve by direct lookup only, and the decoy beside them must stay unseen."""
    for name in c.OWNER_FILES:
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "decoy-personal.pdf").write_bytes(b"x")

    def _no_listing(*args, **kwargs):
        raise AssertionError("the owner folder was listed")

    with monkeypatch.context() as m:
        m.setattr(os, "listdir", _no_listing)
        m.setattr(os, "scandir", _no_listing)
        m.setattr(Path, "iterdir", _no_listing)
        m.setattr(Path, "glob", _no_listing)
        m.setattr(Path, "rglob", _no_listing)
        resolved = [c.owner_file_path(tmp_path, name) for name in c.OWNER_FILES]

    assert [p.name for p in resolved] == list(c.OWNER_FILES)


@pytest.mark.parametrize("on_disk", ["NFC", "NFD"])
@pytest.mark.parametrize("asked", ["NFC", "NFD"])
def test_a_name_resolves_whichever_unicode_spelling_the_disk_or_caller_uses(
    tmp_path, monkeypatch, on_disk, asked
):
    """macOS reports file names decomposed (NFD); the allowlist is written composed (NFC). The
    same name must resolve either way, or a file the owner really has would be 'missing'."""
    name = "Café MSA.pdf"
    monkeypatch.setattr(c, "OWNER_FILES", (name,))
    (tmp_path / unicodedata.normalize(on_disk, name)).write_bytes(b"x")
    found = c.owner_file_path(tmp_path, unicodedata.normalize(asked, name))
    assert unicodedata.normalize("NFC", found.name) == name


def test_an_allowed_file_that_is_not_there_is_not_found_rather_than_refused(tmp_path):
    """Refused means 'not one of the 14'; a missing allowed file is a plain not-found so the
    owner is told to check the folder, not that their file is forbidden."""
    with pytest.raises(FileNotFoundError):
        c.owner_file_path(tmp_path, c.OWNER_FILES[0])


# --- models and loaders (FR-1, FR-2) -----------------------------------------------------

_DOC = {
    "category": "constructed",
    "dataset": None,
    "file": "generated/x.txt",
    "mime_type": "text/plain",
    "file_sha256": "a" * 64,
    "title": "x",
    "ai_steps": ["metadata", "clauses"],
}
_AT = "2026-10-06T12:00:00Z"


def _doc(doc_id: str, **overrides) -> dict:
    return {**_DOC, "doc_id": doc_id, **overrides}


def _tree() -> dict[str, object]:
    """A small consistent exam as {relative path: JSON}; 'private/...' goes under PRIVATE_DIR."""
    owner = [
        {
            "doc_id": "owner-01",
            "name": c.OWNER_FILES[0],
            "file_sha256": "b" * 64,
            "mime_type": "application/pdf",
            "pages": 9,
            "kind": "text",
            "frozen_text_sha256": "c" * 64,
            "frozen_method": "native_text",
            "imported_at": _AT,
        },
        {
            "doc_id": "owner-12",
            "name": c.OWNER_FILES[11],
            "file_sha256": "d" * 64,
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pages": 2,
            "kind": "stamp_only_scan",
            "frozen_text_sha256": "e" * 64,
            "frozen_method": "reducto_ocr",
            "imported_at": _AT,
        },
    ]
    return {
        "content/datasets.json": [
            {
                "key": "cuad",
                "name": "CUAD",
                "version": "v1",
                "licence": "CC BY 4.0",
                "url": "https://example.test/cuad",
                "files": [{"path": "cuad/a.json", "sha256": "f" * 64}],
                "available": True,
            }
        ],
        "content/documents.json": [
            _doc("cuad-001", category="public", dataset="cuad"),
            _doc("ae-msa-01"),
            _doc("scan-ae-msa-01", twin_of="ae-msa-01"),
            _doc("pf-01", uploader="portfolio", ai_steps=["metadata", "clauses", "brain"]),
        ],
        "content/items/review.json": [
            {
                "item_id": "rv-ae-msa-01",
                "doc_id": "ae-msa-01",
                "category": "constructed",
                "source": "constructed",
                "playbook": "cp_msa",
                "rules": [
                    {
                        "rule": "limitation_of_liability",
                        "status": "deviation",
                        "severity": "high",
                        "planted": {
                            "rule": "limitation_of_liability",
                            "status": "deviation",
                            "original_text": "capped at fees",
                            "changed_text": "uncapped",
                            "anchor_text": "y" * 60,
                            "char_start": 100,
                            "severity": "high",
                            "severity_source": "MSA Playbook / limitation_of_liability",
                        },
                    }
                ],
            }
        ],
        "content/items/extraction.json": [
            {
                "item_id": "ex-cuad-001",
                "doc_id": "cuad-001",
                "category": "public",
                "source": "CUAD v1",
                "fields": [
                    {
                        "field": "governing_law",
                        "kind": "jurisdiction",
                        "value": "Delaware",
                        "quote": "governed by Delaware law",
                    }
                ],
            }
        ],
        "content/items/qa.json": [
            {
                "item_id": "qa-001",
                "doc_id": "cuad-001",
                "category": "public",
                "source": "LegalBench-RAG v1 / cuad",
                "question": "Who governs?",
                "answer": "Delaware",
                "passages": ["governed by Delaware law"],
            }
        ],
        "content/items/portfolio.json": [
            {
                "item_id": "pq-01",
                "question": "Which contracts are with Acme?",
                "fact": "counterparty",
                "expected_doc_ids": ["pf-01"],
            }
        ],
        "content/playbooks/cp_msa.json": {"name": "cp_msa", "rules": []},
        "content/sample.json": {"review": ["rv-ae-msa-01"], "portfolio_docs": ["pf-01"]},
        "content/manifest.json": {"built_by": "test"},
        "private/owner/manifest.json": owner,
        "private/routing/requests.json": [
            {
                "item_id": "rt-01",
                "channel": "email",
                "subject": "NDA please",
                "body": "Need an NDA with Acme.",
                "drafted_for_workflow_id": "wf-1",
                "drafting_model": "claude-sonnet-5-5",
            }
        ],
        "private/routing/workflows.json": {
            "fingerprint": "wf-fp-1",
            "taken_at": _AT,
            "workflows": [],
        },
        "private/keys/owner_facts.json": [
            {
                "doc_id": "owner-01",
                "field": "governing_law",
                "status": "confirmed",
                "value": "India",
                "quote": "laws of India",
                "char_start": 10,
                "char_end": 23,
                "suggested_value": "India",
                "suggested_quote": "laws of India",
                "suggested_by": c.PREFILL_MODEL,
                "file_sha256": "b" * 64,
                "frozen_text_sha256": "c" * 64,
                "sheet_sha256": "1" * 64,
                "loaded_at": _AT,
            }
        ],
        "private/keys/routing.json": [
            {
                "item_id": "rt-01",
                "status": "confirmed",
                "workflow_id": "wf-1",
                "workflow_fingerprint": "wf-fp-1",
                "sheet_sha256": "2" * 64,
                "loaded_at": _AT,
            }
        ],
    }


def _load(tree: dict[str, object], root: Path, monkeypatch) -> c.ExamContent:
    """Write ``tree`` under ``root`` and load it through the real loader."""
    for rel, value in tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(c, "CONTENT_DIR", root / "content")
    monkeypatch.setattr(c, "PRIVATE_DIR", root / "private")
    return c.load_content()


def test_nothing_on_disk_loads_as_an_empty_exam_with_a_stable_fingerprint(tmp_path, monkeypatch):
    """Before `build` and before any owner step the exam is empty, not an error: `status`
    must still run. The empty exam must also not share a fingerprint with a real one."""
    empty = _load({}, tmp_path, monkeypatch)
    assert empty.documents == [] and empty.owner_keys == [] and empty.workflow_snapshot is None
    assert c.content_fingerprint(empty) == c.content_fingerprint(c.load_content())
    full = _load(_tree(), tmp_path / "full", monkeypatch)
    assert c.content_fingerprint(full) != c.content_fingerprint(empty)


def test_load_content_reads_committed_and_private_files(tmp_path, monkeypatch):
    """FR-1: one loader feeds every run, so a file the loader skipped would be an item no run
    ever asks about."""
    content = _load(_tree(), tmp_path, monkeypatch)
    assert [d.doc_id for d in content.documents][:4] == [
        "cuad-001",
        "ae-msa-01",
        "scan-ae-msa-01",
        "pf-01",
    ]
    assert (len(content.datasets), len(content.review), len(content.extraction)) == (1, 1, 1)
    assert (len(content.qa), len(content.portfolio), len(content.routing)) == (1, 1, 1)
    assert (len(content.owner_keys), len(content.routing_keys)) == (1, 1)
    assert content.playbooks == {"cp_msa": {"name": "cp_msa", "rules": []}}
    assert content.sample["portfolio_docs"] == ["pf-01"]
    assert content.workflow_snapshot["fingerprint"] == "wf-fp-1"
    assert content.review[0].rules[0].planted.severity == "high"
    assert content.documents[0].category == c.SourceCategory.PUBLIC


def test_owner_documents_come_from_the_manifest_with_an_as_read_twin_for_stamp_only_scans(
    tmp_path, monkeypatch
):
    """FR-23: a stamp-only scan is read from its forced OCR text AND, separately, the way the
    app reads it today; losing the twin would hide the app's real weakness on those files."""
    docs = {d.doc_id: d for d in _load(_tree(), tmp_path, monkeypatch).documents}
    plain, forced, twin = docs["owner-01"], docs["owner-12"], docs["owner-12-asread"]
    assert (plain.category, plain.text_mode, plain.file) == (
        c.SourceCategory.OWNER,
        "app",
        c.OWNER_FILES[0],
    )
    assert plain.title == "Employment Agreement"
    assert forced.text_mode == "forced_ocr" and forced.as_read_today_of is None
    assert twin.text_mode == "app" and twin.as_read_today_of == "owner-12"
    assert twin.file_sha256 == forced.file_sha256
    assert "owner-01-asread" not in docs


def test_a_manifest_entry_outside_the_fourteen_is_refused_on_load(tmp_path, monkeypatch):
    """FR-15 on the read side: a hand-edited manifest must not smuggle a fifteenth file, or
    move a name under another owner-NN, into a run."""
    stranger = _tree()
    stranger["private/owner/manifest.json"][0]["name"] = "personal-taxes.pdf"
    with pytest.raises(ValidationError):
        _load(stranger, tmp_path / "a", monkeypatch)
    renumbered = _tree()
    renumbered["private/owner/manifest.json"][0]["doc_id"] = "owner-02"
    with pytest.raises(ValidationError):
        _load(renumbered, tmp_path / "b", monkeypatch)


def test_a_field_the_models_do_not_know_fails_the_load(tmp_path, monkeypatch):
    """The models are the frozen file formats: an invented or renamed field must stop the
    load rather than be dropped on the way into a run."""
    tree = _tree()
    tree["content/items/qa.json"][0]["answer_text"] = "typo for answer"
    with pytest.raises(ValidationError):
        _load(tree, tmp_path, monkeypatch)


@pytest.mark.parametrize(
    ("what", "mutate"),
    [
        ("review doc", lambda t: t["content/items/review.json"][0].update(doc_id="nope")),
        ("qa doc", lambda t: t["content/items/qa.json"][0].update(doc_id="nope")),
        ("extraction doc", lambda t: t["content/items/extraction.json"][0].update(doc_id="nope")),
        (
            "portfolio doc",
            lambda t: t["content/items/portfolio.json"][0].update(expected_doc_ids=["nope"]),
        ),
        ("scan twin", lambda t: t["content/documents.json"][2].update(twin_of="nope")),
        ("duplicate doc", lambda t: t["content/documents.json"].append(_doc("cuad-001"))),
        (
            "duplicate item",
            lambda t: t["content/items/qa.json"].append(
                copy.deepcopy(t["content/items/qa.json"][0])
            ),
        ),
    ],
)
def test_content_whose_ids_disagree_is_refused(what, mutate, tmp_path, monkeypatch):
    """A dangling or duplicated id would not fail later: the runner would plan a unit for a
    document it never ingested and report it 'not attempted' in every run."""
    tree = _tree()
    mutate(tree)
    with pytest.raises(ValueError, match="inconsistent"):
        _load(tree, tmp_path, monkeypatch)


# --- fingerprint (FR-1, FR-20, FR-27) ----------------------------------------------------

_CHANGES = [
    ("owner key value", lambda t: t["private/keys/owner_facts.json"][0].update(value="USA")),
    (
        "owner key status",
        lambda t: t["private/keys/owner_facts.json"][0].update(status="corrected"),
    ),
    (
        "owner key frozen text",
        lambda t: t["private/keys/owner_facts.json"][0].update(frozen_text_sha256="9" * 64),
    ),
    ("routing key pick", lambda t: t["private/keys/routing.json"][0].update(workflow_id="wf-2")),
    (
        "frozen text of an owner file",
        lambda t: t["private/owner/manifest.json"][0].update(frozen_text_sha256="9" * 64),
    ),
    (
        "owner file bytes",
        lambda t: t["private/owner/manifest.json"][0].update(file_sha256="9" * 64),
    ),
    ("document hash", lambda t: t["content/documents.json"][1].update(file_sha256="9" * 64)),
    ("qa answer", lambda t: t["content/items/qa.json"][0].update(answer="Texas")),
    (
        "review key",
        lambda t: t["content/items/review.json"][0]["rules"][0].update(status="missing"),
    ),
    ("playbook", lambda t: t["content/playbooks/cp_msa.json"].update(rules=[{"x": 1}])),
    ("sample subset", lambda t: t["content/sample.json"].update(review=[])),
    ("build manifest", lambda t: t["content/manifest.json"].update(built_by="other")),
    ("routing request", lambda t: t["private/routing/requests.json"][0].update(body="Changed.")),
    (
        "workflow snapshot",
        lambda t: t["private/routing/workflows.json"].update(fingerprint="wf-fp-2"),
    ),
]

_NOT_CHANGES = [
    (
        "key reloaded (loaded_at)",
        lambda t: t["private/keys/owner_facts.json"][0].update(loaded_at="2027-01-01T00:00:00Z"),
    ),
    (
        "key sheet hash",
        lambda t: t["private/keys/owner_facts.json"][0].update(sheet_sha256="8" * 64),
    ),
    (
        "key suggestion",
        lambda t: t["private/keys/owner_facts.json"][0].update(
            suggested_value="Elsewhere", suggested_by="previous key"
        ),
    ),
    ("routing sheet", lambda t: t["private/keys/routing.json"][0].update(sheet_sha256="8" * 64)),
    (
        "owner re-import time",
        lambda t: t["private/owner/manifest.json"][0].update(imported_at="2027-01-01T00:00:00Z"),
    ),
    (
        "snapshot time",
        lambda t: t["private/routing/workflows.json"].update(taken_at="2027-01-01T00:00:00Z"),
    ),
]


def test_fingerprint_is_stable_across_loads(tmp_path, monkeypatch):
    """FR-1: the same exam must fingerprint the same every time, or no two runs ever match."""
    first = c.content_fingerprint(_load(_tree(), tmp_path, monkeypatch))
    assert len(first) == 64
    assert first == c.content_fingerprint(c.load_content())


@pytest.mark.parametrize(("what", "mutate"), _CHANGES, ids=[w for w, _ in _CHANGES])
def test_fingerprint_changes_when_anything_that_decides_the_exam_changes(
    what, mutate, tmp_path, monkeypatch
):
    """FR-20/FR-27: a changed key, text, file, item, plan or workflow list must change the
    fingerprint, otherwise two runs on different exams would be accepted as a baseline pair."""
    before = c.content_fingerprint(_load(_tree(), tmp_path / "before", monkeypatch))
    tree = _tree()
    mutate(tree)
    assert c.content_fingerprint(_load(tree, tmp_path / "after", monkeypatch)) != before


@pytest.mark.parametrize(("what", "mutate"), _NOT_CHANGES, ids=[w for w, _ in _NOT_CHANGES])
def test_fingerprint_ignores_provenance_that_does_not_change_the_exam(
    what, mutate, tmp_path, monkeypatch
):
    """Re-loading an identical sheet, or re-importing the same files, changes timestamps and
    hashes of the sheet only; it must not retire a baseline whose answers are untouched."""
    before = c.content_fingerprint(_load(_tree(), tmp_path / "before", monkeypatch))
    tree = _tree()
    mutate(tree)
    assert c.content_fingerprint(_load(tree, tmp_path / "after", monkeypatch)) == before


# --- app code fingerprint ----------------------------------------------------------------


def test_app_code_sha256_follows_python_sources_and_their_paths(tmp_path, monkeypatch):
    """'Same code' (FR-27) means same bytes at the same paths: editing a file, or swapping two
    files' contents, must change the hash; a non-Python file must not."""
    monkeypatch.setattr(c, "APP_DIR", tmp_path)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "pkg" / "b.py").write_text("B = 2\n")
    base = c.app_code_sha256()
    assert len(base) == 64 and base == c.app_code_sha256()

    (tmp_path / "notes.txt").write_text("not code")
    (tmp_path / "pkg" / "x.pyc").write_bytes(b"\0")
    assert c.app_code_sha256() == base

    (tmp_path / "a.py").write_text("A = 1  # edited\n")
    edited = c.app_code_sha256()
    assert edited != base

    (tmp_path / "a.py").write_text("B = 2\n")
    (tmp_path / "pkg" / "b.py").write_text("A = 1  # edited\n")
    assert c.app_code_sha256() not in (base, edited)


def test_app_code_sha256_hashes_the_real_app_tree():
    """The default points at backend/app: an empty tree would hash to a constant and make
    every code change look like 'same code'."""
    assert c.APP_DIR == BACKEND_DIR / "app"
    assert any(c.APP_DIR.rglob("*.py"))
    assert c.app_code_sha256() == c.app_code_sha256()


# --- isolation and hygiene (FR-31, FR-36) -------------------------------------------------


def test_frozen_paths_sit_where_the_plan_puts_them():
    """Other modules (and the ignore rules) rely on these exact locations."""
    eval_dir = BACKEND_DIR / "eval"
    assert c.EXAM_DIR == eval_dir / "exam"
    assert c.CONTENT_DIR == c.EXAM_DIR / "content"
    assert c.REPORTS_DIR == c.EXAM_DIR / "reports"
    assert c.DOCS_DIR == eval_dir / "contracts" and c.RAW_DIR == c.DOCS_DIR / "raw"
    assert c.PRIVATE_DIR == eval_dir / "private" and c.WORK_DIR == eval_dir / ".work"
    assert (c.CUTOFF, c.PREFILL_MODEL) == (25_000, "claude-sonnet-5-5")


def test_importing_the_content_module_does_not_import_the_app():
    """The exam must repoint the app's settings at the throwaway database BEFORE any app
    module is imported; if importing eval.exam.content pulled the app in, the very first
    import would build the engine against the live database."""
    code = (
        "import sys, eval.exam.content\n"
        "bad = sorted(m for m in sys.modules if m == 'app' or m.startswith('app.')"
        " or m.split('.')[0] == 'sqlalchemy')\n"
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


def test_committed_exam_files_name_no_owner_contract():
    """FR-36: only scores and counts are committed. An owner file name in committed content
    or in the ported history would leak which real contracts the owner holds."""
    names = {unicodedata.normalize("NFC", n) for n in c.OWNER_FILES}
    roots = [c.CONTENT_DIR, c.REPORTS_DIR, BACKEND_DIR / "eval" / "results" / "superseded"]
    leaks = []
    for root in roots:
        for path in root.rglob("*") if root.exists() else []:
            if path.suffix.lower() in {".json", ".md", ".txt"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                if any(n in unicodedata.normalize("NFC", text) for n in names):
                    leaks.append(path.name)
    assert leaks == []


# --- public sets: the parsers behind the answer keys (T008, FR-4) ------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2nd  day  of  September  1998", "1998-09-02"),
        ("December 31, 2005", "2005-12-31"),
        ("Nov. 02, 2019", "2019-11-02"),
        ("September 13th, 2019", "2019-09-13"),
        ("the 1st of April 2010", "2010-04-01"),
        ("dated May 5, 2001 (May 5, 2001)", "2001-05-05"),
        ("September 1999", None),
        ("June , 2010", None),
        ("this day of , 19", None),
        ("January 1, 2000 to December 31, 2000", None),
        ("February 30, 2001", None),
        ("The Agreement begins on the Effective Date", None),
    ],
)
def test_a_date_key_is_one_complete_calendar_date_or_nothing(text, expected):
    """A key built from "June , 2010" or from a two-date range would have to invent or pick a
    date, and a wrong key marks a correct AI answer wrong: an unusable label must give nothing."""
    assert b.parse_full_date(text) == expected


@pytest.mark.parametrize(
    ("passage", "expected"),
    [
        ("governed by the laws of the State of New York, US, without regard", "new york"),
        (
            (
                "Except as governed by the U.S. Trademark Act of 1946, this Agreement is "
                "governed by the laws of the Commonwealth of Pennsylvania"
            ),
            "pennsylvania",
        ),
        ("the laws of England and Wales", "england and wales"),
        ("laws of West Virginia", "west virginia"),
        ("Delaware law shall govern this Agreement", "delaware"),
        ("governed by the laws of [***], without regard to conflicts", None),
        ("This Agreement is governed by the rules of the arbitral tribunal", None),
    ],
)
def test_governing_law_names_the_place_after_the_law_cue_or_nothing(passage, expected):
    """A statute named before the cue ('Trademark Act') must not become the jurisdiction, 'West
    Virginia' must not be read as 'Virginia', and a redacted clause names no place at all."""
    assert b.parse_jurisdiction(passage) == expected


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (["Zogenix, Inc.", "Zogenix", "Distributor", "ZOGENIX, INC."], ["Zogenix, Inc."]),
        (["FMI", "Foundation Medicine, Inc.", "the Company"], ["Foundation Medicine, Inc."]),
        (['"you" or the "Franchisee"', "Acme Pvt Ltd"], ["Acme Pvt Ltd"]),
        (['Ginkgo and BLI may each be referred to herein as a "Party"'], []),
        (["doing business as Pretzel Time", "Pretzel Time, Inc."], ["Pretzel Time, Inc."]),
        (["Party A", "Party 2", "Didi Chuxing Co., Ltd."], ["Didi Chuxing Co., Ltd."]),
        (["Plan B Ltd", "Inc"], ["Plan B Ltd"]),
        (["Acme", "Acme Holdings Ltd"], ["Acme Holdings Ltd"]),
        (["Telia AB (publ)"], ["Telia AB"]),
        (["Distributor", "Licensee"], []),
    ],
)
def test_the_parties_key_keeps_full_names_and_drops_aliases_roles_and_sentences(labels, expected):
    """CUAD labels every mention of a party, aliases included. If 'FMI' stayed in the key the AI
    would be marked wrong for writing the company's full name; if a role word, a 'Party B' label
    or a defined-term sentence stayed, the key would ask for something no contract party is called.
    A real name that happens to end in a letter ('Plan B Ltd') has a legal form and stays."""
    assert b.party_names(labels) == expected


def _span(start: int, text: str) -> b._Span:
    return b._Span(start, text)


def test_extraction_keys_state_what_is_labelled_and_say_not_stated_where_nothing_is():
    """FR-23: a label becomes a normalised value with its quote; no label is 'not stated' (an
    invented value is then wrong); a start date falls back to the agreement date because the app
    has one start-date field; a duration is not a date, so that field is left out and counted;
    a renewal clause with no notice label leaves notice_period out rather than 'not stated'."""
    keys, starts, left_out = b.extraction_keys(
        {
            "Parties": [_span(10, "Acme Ltd"), _span(30, "Beta Pvt Ltd"), _span(50, "the Company")],
            "Agreement Date": [_span(5, "1 April 2024")],
            "Expiration Date": [_span(900, "for five years from the Effective Date")],
            "Governing Law": [_span(30_000, "governed by the laws of the State of Texas")],
            "Renewal Term": [_span(40_000, "renews for successive one-year terms")],
        }
    )
    by_field = {k.field: k for k in keys}
    assert by_field["parties"].value == ["Acme Ltd", "Beta Pvt Ltd"]
    assert by_field["parties"].quote == "Acme Ltd" and by_field["parties"].kind == "parties"
    assert (by_field["effective_date"].value, by_field["effective_date"].quote) == (
        "2024-04-01",
        "1 April 2024",
    )
    assert by_field["governing_law"].value == "texas"
    assert by_field["renewal_term"].value == by_field["renewal_term"].quote
    for absent in ("liability_cap", "termination_for_convenience"):
        assert (by_field[absent].value, by_field[absent].quote) == (None, None)
    assert "expiration_date" not in by_field and "notice_period" not in by_field
    assert left_out == {
        "expiration_date: no single complete date": 1,
        "notice_period: notice may sit in the termination or renewal clause": 1,
    }
    assert starts == {
        "parties": 10,
        "effective_date": 5,
        "governing_law": 30_000,
        "renewal_term": 40_000,
    }


@pytest.mark.parametrize(
    ("labels", "outcome"),
    [
        ({}, "not stated"),
        ({"Governing Law": "the laws of Texas"}, "not stated"),
        ({"Termination For Convenience": "either party may terminate on 60 days' notice"}, "left out"),
        ({"Renewal Term": "renews for one-year terms unless notice is given"}, "left out"),
        (
            {
                "Notice Period To Terminate Renewal": "30 days before the term ends",
                "Termination For Convenience": "either party may terminate on 60 days' notice",
            },
            "30 days before the term ends",
        ),
    ],
)
def test_a_notice_period_is_not_stated_only_where_no_clause_could_carry_one(labels, outcome):
    """D-14 defines notice_period as the notice to end the contract or to stop it renewing, but
    CUAD labels only the renewal one. A key of 'not stated' on a contract whose labelled
    termination clause gives 60 days would mark the AI's correct '60 days' wrong (29 of 40 keys
    were wrong this way): such a contract's field must be left out and counted instead."""
    keys, _, left_out = b.extraction_keys(
        {cat: [_span(100, text)] for cat, text in labels.items()}
    )
    notice = {k.field: k for k in keys}.get("notice_period")
    counted = left_out["notice_period: notice may sit in the termination or renewal clause"]
    if outcome == "not stated":
        assert (notice.value, notice.quote) == (None, None) and counted == 0
    elif outcome == "left out":
        assert notice is None and counted == 1
    else:
        assert notice.value == outcome and counted == 0


def test_a_start_date_label_that_cannot_be_read_is_not_replaced_by_another_date():
    """A present but unusable Effective Date ('the date of last signature') must leave the field
    out, not quietly fall back to a different date and key the wrong answer."""
    keys, _, left_out = b.extraction_keys(
        {
            "Effective Date": [_span(300, "the date of the last signature of this Agreement")],
            "Agreement Date": [_span(5, "1 April 2024")],
        }
    )
    assert "effective_date" not in {k.field for k in keys}
    assert left_out["effective_date: no single complete date"] == 1


def test_a_consortium_contract_has_no_parties_key_and_no_law_is_not_stated():
    """A 45-party consortium agreement would put 45 units into the parties score that no
    extraction can be expected to list: its parties field is left out and counted. A contract
    with no governing-law label is 'not stated', which is different from an unreadable label."""
    names = [_span(i * 10, f"Carrier Number{chr(97 + i)} Ltd") for i in range(b.MAX_PARTIES + 1)]
    keys, _, left_out = b.extraction_keys({"Parties": names})
    fields = {k.field: k for k in keys}
    assert "parties" not in fields
    assert left_out == {f"parties: more than {b.MAX_PARTIES} named": 1}
    assert (fields["governing_law"].value, fields["governing_law"].quote) == (None, None)

    _, _, redacted = b.extraction_keys({"Governing Law": [_span(9, "the laws of [***]")]})
    assert redacted == {"parties: no usable name": 1, "governing_law: no jurisdiction named": 1}


def test_past_25k_means_starting_at_or_after_the_first_25000_characters():
    """The app reads characters 0-24,999; a passage starting at 25,000 is wholly unseen, and one
    starting at 24,999 is partly seen. Off by one here moves items between the two buckets."""
    assert not b._past(c.CUTOFF - 1) and b._past(c.CUTOFF)


# --- public sets: the builder on a miniature raw folder (T008, FR-2, FR-4) -----------------

_FILLER = "The parties agree to the following standard terms. "
_GOV_NY = "This Agreement shall be governed by the laws of the State of New York."


def _long_text(early: list[str], late: list[str]) -> str:
    """Contract text whose ``early`` sentences start before 25,000 and ``late`` ones after."""
    body = _FILLER * (c.CUTOFF // len(_FILLER) + 2)
    return " ".join([*early, body, *late])


def _cuad_entry(title: str, context: str, labels: dict[str, list[str]]) -> dict:
    qas = [
        {
            "id": f"{title}__{category}",
            "question": (
                f'Highlight the parts (if any) of this contract related to "{category}" that '
                f"should be reviewed by a lawyer. Details: What does {category} mean?"
            ),
            "answers": [{"text": t, "answer_start": context.index(t)} for t in texts],
            "is_impossible": not texts,
        }
        for category, texts in labels.items()
    ]
    return {"title": title, "paragraphs": [{"context": context, "qas": qas}]}


def _lbr_test(query: str, *snippets: tuple[str, str, int, int]) -> dict:
    """(title, corpus text, start, end) per snippet; the answer is the real slice."""
    return {
        "query": query,
        "snippets": [
            {"file_path": f"cuad/{t}.txt", "span": [a, z], "answer": text[a:z]}
            for t, text, a, z in snippets
        ],
    }


def _nli_doc(file_name: str, text: str, nda2: str) -> dict:
    return {
        "id": 1,
        "file_name": file_name,
        "text": text,
        "spans": [[0, 9], [10, 19]],
        "annotation_sets": [
            {
                "annotations": {
                    "nda-2": {"choice": nda2, "spans": [] if nda2 == "NotMentioned" else [1]},
                    "nda-1": {"choice": "Entailment", "spans": [0]},
                }
            }
        ],
        "document_type": "x",
        "url": "x",
    }


class _Raw:
    """A miniature raw-data folder with known answers, and the texts behind it."""

    def __init__(self, root: Path):
        self.root = root
        early = (
            "This Agreement is made on March 3, 2001 between Alpha Holdings Ltd and "
            "Beta Services Pvt Ltd."
        )
        late = [
            _GOV_NY,
            "Neither party shall compete with the other.",
            "Liability under this Agreement shall be unlimited.",
            "The Distributor shall deal exclusively with the Supplier.",
        ]
        self.alpha = _long_text([early], late)
        self.bravo = _long_text(
            ["Bravo Corp and Echo Ltd sign this."], ["Governed by the laws of Delaware."]
        )
        self.charlie = _long_text(
            ["Charlie Inc and Echo Ltd sign this."], ["Governed by the laws of Texas."]
        )
        self.delta = "Delta Short Agreement. " * 20  # under 25,000 characters
        self.echo = _long_text(["Echo Agreement."], ["Governed by the laws of Ohio."])
        self.foxtrot = _long_text(["Foxtrot Agreement."], ["Governed by the laws of Utah."])
        entries = [
            _cuad_entry(
                "ALPHA AGREEMENT",
                self.alpha,
                {
                    "Parties": ["Alpha Holdings Ltd", "Beta Services Pvt Ltd"],
                    "Agreement Date": ["March 3, 2001"],
                    "Governing Law": [_GOV_NY],
                    "Non-Compete": [late[1]],
                    "Uncapped Liability": [late[2]],
                    "Exclusivity": [late[3]],
                    "Most Favored Nation": [],
                },
            ),
            _cuad_entry(
                "BRAVO AGREEMENT",
                self.bravo,
                {"Governing Law": ["Governed by the laws of Delaware."]},
            ),
            _cuad_entry(
                "CHARLIE AGREEMENT",
                self.charlie,
                {"Governing Law": ["Governed by the laws of Texas."]},
            ),
            _cuad_entry("DELTA SHORT", self.delta, {"Parties": ["Delta Short Agreement."]}),
            _cuad_entry(
                "ECHO NO COPY", self.echo, {"Governing Law": ["Governed by the laws of Ohio."]}
            ),
            _cuad_entry("FOXTROT DIFFERENT COPY", self.foxtrot, {}),
        ]
        self.write("cuad/CUADv1.json", {"version": "aok_v1.0", "data": entries})
        corpus = {
            "ALPHA AGREEMENT": self.alpha,
            "BRAVO AGREEMENT": self.bravo,
            "CHARLIE AGREEMENT": self.charlie,
            "DELTA SHORT": self.delta,
            "FOXTROT DIFFERENT COPY": self.foxtrot + "!",  # not the same text as CUAD's
        }
        for title, text in corpus.items():
            self.write_text(f"lbr/corpus/cuad/{title}.txt", text)
        self.write("lbr/benchmarks/cuad.json", {"tests": self._questions()})
        self.write("cnli/contract-nli/test.json", self._nli())
        self.write_text("cnli/contract-nli/LICENSE", "CC BY 4.0")
        self.write_text(
            "commonpaper/CSA.md",
            "see https://commonpaper.com/standards/cloud-service-agreement/3.0/",
        )
        self.write_text("commonpaper/DPA.md", "a data processing agreement")
        self.write_text(
            "commonpaper/Mutual-NDA.md",
            "[Version 1.0](https://commonpaper.com/standards/mutual-nda/1.0/)",
        )

    def _questions(self) -> list[dict]:
        a = ("ALPHA AGREEMENT", self.alpha)
        gov = self.alpha.index(_GOV_NY)
        early = self.alpha.index("March 3, 2001")
        tests = [
            _lbr_test("Q-PAST-GOV what law governs?", (*a, gov, gov + len(_GOV_NY))),
            _lbr_test("Q-BEFORE-DATE what is the date?", (*a, early, early + 13)),
            _lbr_test("Q-FOUR-SNIPPETS", *[(*a, gov + i, gov + i + 5) for i in range(4)]),
            _lbr_test("Q-TOO-LONG", (*a, 100, 100 + b.PASSAGE_MAX + 100)),
            _lbr_test("Q-TWO-FILES", (*a, gov, gov + 5), ("BRAVO AGREEMENT", self.bravo, 0, 5)),
            _lbr_test("Q-NOT-SELECTED", ("DELTA SHORT", self.delta, 0, 20)),
        ]
        wrong = _lbr_test("Q-WRONG-TEXT", (*a, gov, gov + 20))
        wrong["snippets"][0]["answer"] = "not what the corpus says"
        tests.append(wrong)
        for i in range(5):  # more past-25k questions than the three-per-contract limit
            tests.append(_lbr_test(f"Q-PAST-EXTRA-{i}", (*a, gov + i * 3, gov + i * 3 + 10)))
        end = len(self.bravo)
        tests.append(_lbr_test("Q-BRAVO", ("BRAVO AGREEMENT", self.bravo, end - 30, end - 5)))
        return tests

    def _nli(self) -> dict:
        texts = [f"{'A' * n} NDA text with some sentences." for n in (900, 800, 700, 600, 500, 400)]
        names = [" NDA%20one.pdf", "two.txt", "three.txt", "four.txt", "five.txt", "six.txt"]
        verdicts = ["NotMentioned", "Contradiction", "Entailment"] + ["NotMentioned"] * 3
        return {
            "labels": {
                "nda-2": {"short_description": "Second point", "hypothesis": "H2"},
                "nda-1": {"short_description": "Explicit identification", "hypothesis": "H1"},
            },
            "documents": [
                _nli_doc(n, t, v) for n, t, v in zip(names, texts, verdicts, strict=True)
            ],
        }

    def write(self, rel: str, value) -> None:
        self.write_text(rel, json.dumps(value))

    def write_text(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def raw(tmp_path) -> _Raw:
    return _Raw(tmp_path / "raw")


def _build(raw: _Raw, **kw) -> b.PublicBuild:
    return b.build_public(raw.root, n_cuad=kw.pop("n_cuad", 10), n_nda=5, n_qa=kw.pop("n_qa", 8))


def test_the_cuad_set_is_the_long_contracts_with_most_labels_past_25k_ties_by_title(raw):
    """FR-4a: preference for labelled passages past 25k is the point of the set. A short contract,
    one the question corpus lacks, and one whose two copies differ must all be left out, and a
    tie must resolve the same way every time (by title) or two builds would differ."""
    built = _build(raw)
    titles = [d.title for d in built.documents if d.doc_id.startswith("cuad-")]
    assert titles == ["ALPHA AGREEMENT", "BRAVO AGREEMENT", "CHARLIE AGREEMENT"]
    assert [d.doc_id for d in built.documents][:3] == ["cuad-001", "cuad-002", "cuad-003"]
    assert [d.title for d in built.documents if d.doc_id.startswith("cuad-")] == titles
    assert [d.doc_id for d in _build(raw, n_cuad=2).documents if d.doc_id.startswith("cuad-")] == [
        "cuad-001",
        "cuad-002",
    ]
    assert built.report["cuad"]["candidates_over_25k"] == 3
    assert built.report["cuad"]["documents"] == 3


def test_public_documents_carry_their_source_and_the_exact_text_hash(raw):
    """FR-2: a document must name its dataset, and its recorded hash must be the hash of the
    bytes written, or the run would ingest a file other than the one that was fingerprinted."""
    built = _build(raw)
    for doc in built.documents:
        data = built.texts[doc.file]
        assert doc.file == f"public/{doc.doc_id}.txt" and doc.mime_type == "text/plain"
        assert doc.category == c.SourceCategory.PUBLIC
        assert doc.file_sha256 == hashlib.sha256(data).hexdigest()
        assert doc.dataset == ("cuad" if doc.doc_id.startswith("cuad-") else "contractnli")
        steps = ["metadata", "clauses", "brain"] if doc.dataset == "cuad" else ["metadata"]
        assert doc.ai_steps == steps
    assert built.texts["public/cuad-001.txt"] == raw.alpha.encode()


def test_cuad_review_flags_each_labelled_provision_and_clears_the_rest(raw):
    """FR-4a/FR-22: a labelled span means the contract has the provision (deviation, with the
    lawyer's span as evidence); no label means compliant. Rules appear in the playbook's order so
    the review prompt's rule index and the key agree."""
    built = _build(raw)
    item = next(i for i in built.review if i.item_id == "rv-cuad-001")
    assert item.playbook == "cuad_checklist" and item.category == c.SourceCategory.PUBLIC
    assert item.source == "CUAD v1" and not item.control
    assert [r.rule for r in item.rules] == list(b.CUAD_REVIEW_RULES.values())
    by_rule = {r.rule: r for r in item.rules}
    assert by_rule["non_compete"].status == "deviation"
    assert by_rule["non_compete"].evidence_quote == "Neither party shall compete with the other."
    assert by_rule["most_favored_nation"].status == "compliant"
    assert by_rule["most_favored_nation"].evidence_quote is None
    assert all(r.severity is None and r.planted is None for r in item.rules)
    assert [r.rule for r in item.rules] == [
        r["clause_type"] for r in built.playbooks["cuad_checklist"]["rules"]
    ]


def test_cuad_extraction_keys_use_the_labels_and_name_where_they_start(raw):
    """FR-4a/FR-23: the key is built from the lawyer's labels; a contract with no expiration
    label is 'not stated' (not left out), and the report counts what sits past 25k."""
    built = _build(raw)
    item = next(i for i in built.extraction if i.item_id == "ex-cuad-001")
    fields = {f.field: f for f in item.fields}
    assert fields["parties"].value == ["Alpha Holdings Ltd", "Beta Services Pvt Ltd"]
    assert (fields["effective_date"].value, fields["effective_date"].quote) == (
        "2001-03-03",
        "March 3, 2001",
    )
    assert fields["governing_law"].value == "new york"
    assert fields["liability_cap"].value is None and fields["renewal_term"].quote is None
    assert item.source == "CUAD v1" and item.category == c.SourceCategory.PUBLIC
    assert built.report["cuad"]["extraction_fields_past_25k"] >= 3  # three governing-law quotes
    assert built.report["cuad"]["review_deviations_past_25k"] == 3


def test_qa_questions_pass_every_filter_and_keep_to_three_per_contract(raw):
    """FR-4c: a question the search could never be graded on (four snippets, two files, a passage
    over 1,500 characters, a passage that is not what the corpus says, a contract that is not in
    the set) or that repeats another on the same contract must be skipped, and no contract may
    supply more than three."""
    built = _build(raw)
    asked = [q.question for q in built.qa]
    for bad in ("FOUR-SNIPPETS", "TOO-LONG", "TWO-FILES", "NOT-SELECTED", "WRONG-TEXT"):
        assert not any(bad in q for q in asked)
    per_doc = {d: sum(q.doc_id == d for q in built.qa) for d in {q.doc_id for q in built.qa}}
    assert per_doc == {"cuad-001": 3, "cuad-002": 1}
    assert [q.item_id for q in built.qa] == [f"qa-{i:03d}" for i in range(1, 5)]
    assert [q.doc_id for q in built.qa] == sorted(q.doc_id for q in built.qa)
    assert len({(q.doc_id, q.question) for q in built.qa}) == len(built.qa)
    for q in built.qa:
        assert q.category == c.SourceCategory.PUBLIC and q.source.startswith("LegalBench-RAG ")
        assert q.source.endswith(" / cuad") and q.passages and q.answer


def test_the_same_question_is_not_asked_twice_of_one_contract(raw):
    """Two items with the same (contract, question) would send an identical request, so the
    second would be a free repeat that inflates the count without adding a measurement."""
    text = raw.alpha
    gov = text.index(_GOV_NY)
    twice = [
        _lbr_test("Q-SAME", ("ALPHA AGREEMENT", text, gov, gov + 20)),
        _lbr_test("Q-SAME", ("ALPHA AGREEMENT", text, gov + 30, gov + 50)),
    ]
    raw.write("lbr/benchmarks/cuad.json", {"tests": twice})
    assert [q.question for q in _build(raw).qa] == ["Q-SAME"]


def test_qa_fills_the_before_25k_bucket_first_so_both_buckets_can_be_read(raw):
    """FR-24: the report splits Q&A before/past 25k. If past-25k questions were taken first they
    would use up the three-per-contract limit and the before-25k bucket would be left empty."""
    built = _build(raw)
    asked = [q.question for q in built.qa]
    assert any("Q-BEFORE-DATE" in q for q in asked)
    assert built.report["qa"]["questions"] == 4 and built.report["qa"]["past_25k"] == 3


def test_nda_review_maps_contractnli_verdicts_and_picks_the_longest_first(raw):
    """FR-4b: Entailment -> compliant, Contradiction -> deviation, NotMentioned -> missing, with
    the lawyer's evidence span; the longest NDAs come first and the seeded pick is stable."""
    built = _build(raw)
    docs = [d for d in built.documents if d.doc_id.startswith("cnli-")]
    assert [d.doc_id for d in docs] == [f"cnli-0{i}" for i in range(1, 6)]
    assert docs[0].title == "NDA one" and docs[0].ai_steps == ["metadata"]
    assert [len(built.texts[d.file]) for d in docs[:4]] == sorted(
        (len(built.texts[d.file]) for d in docs[:4]), reverse=True
    )
    assert docs[4].title in {"five", "six"} and docs[4].title == _build(raw).documents[-1].title
    item = next(i for i in built.review if i.item_id == "rv-cnli-02")
    assert item.playbook == "nda_checklist" and item.source == "ContractNLI 1.0"
    assert [(r.rule, r.status) for r in item.rules] == [
        ("nda_1_explicit_identification", "compliant"),
        ("nda_2_second_point", "deviation"),
    ]
    first = next(i for i in built.review if i.item_id == "rv-cnli-01")
    assert [r.status for r in first.rules] == ["compliant", "missing"]
    assert first.rules[0].evidence_quote == built.texts[docs[0].file].decode()[:9]
    assert first.rules[1].evidence_quote is None
    assert built.report["contractnli"]["by_verdict"] == {
        "compliant": 6,
        "deviation": 1,
        "missing": 3,
    }


def test_playbooks_are_in_the_platforms_rule_shape_with_no_invented_severity(raw):
    """FR-5: the exam loads these as PlaybookRule rows, so the keys must be the platform's column
    names; a public checklist has no lawyer-set severity and must not get one."""
    built = _build(raw)
    columns = {
        "clause_type", "rule_type", "preferred_position", "fallback_position",
        "required_language", "prohibited_language", "risk_level", "rationale",
        "negotiation_guidance", "approval_required", "escalation_role", "sample_clause",
    }  # fmt: skip
    for key, rules in (("cuad_checklist", 8), ("nda_checklist", 2)):
        playbook = built.playbooks[key]
        assert {"name", "description", "source", "rules"} <= playbook.keys()
        assert len(playbook["rules"]) == rules
        for rule in playbook["rules"]:
            assert set(rule) == columns and rule["risk_level"] is None
            assert rule["preferred_position"] and rule["rationale"]
    first_rule = built.playbooks["cuad_checklist"]["rules"][0]
    assert "What does Uncapped Liability mean?" in first_rule["preferred_position"]
    assert [r["clause_type"] for r in built.playbooks["nda_checklist"]["rules"]] == [
        "nda_1_explicit_identification",
        "nda_2_second_point",
    ]


def test_dataset_registry_records_name_version_licence_and_file_hashes(raw):
    """FR-2: every public set must name its dataset, version and licence, and carry the hash of
    the raw file it was built from, so a changed download changes the content fingerprint."""
    registry = {d.key: d for d in b.dataset_registry(raw.root)}
    assert set(registry) == {"cuad", "contractnli", "legalbench_rag", "commonpaper"}
    for d in registry.values():
        assert d.available and d.name and d.version and d.licence and d.url
    benchmark = hashlib.sha256((raw.root / b.LBR_JSON).read_bytes()).hexdigest()
    assert registry["legalbench_rag"].files[0].sha256 == benchmark
    assert benchmark[:12] in registry["legalbench_rag"].version
    assert registry["cuad"].licence == registry["contractnli"].licence == "CC BY 4.0"
    assert registry["commonpaper"].version == "CSA 3.0; DPA 1.0; Mutual NDA 1.0"
    TypeAdapter(list[c.Dataset]).validate_python([d.model_dump() for d in registry.values()])


def test_a_missing_dataset_is_reported_and_its_sets_are_not_built_or_substituted(raw):
    """Spec edge case: with a download missing, that set is reported missing; it must not be
    replaced by another set, and an existing committed file for it must not be overwritten."""
    (raw.root / b.CUAD_JSON).unlink()
    built = _build(raw)
    assert built.report["missing"] == ["cuad"]
    assert built.built == {"contractnli"} and built.extraction == [] and built.qa == []
    assert {d.dataset for d in built.documents} == {"contractnli"}
    content_dir = raw.root.parent / "content"
    (content_dir / "items").mkdir(parents=True)
    (content_dir / "items/qa.json").write_text("previous")
    b.write_public(built, content_dir=content_dir, docs_dir=raw.root.parent / "docs")
    assert (content_dir / "items/qa.json").read_text() == "previous"
    registry = json.loads((content_dir / "datasets.json").read_text())
    assert {d["key"]: d["available"] for d in registry}["cuad"] is False
    nothing = b.build_public(raw.root.parent / "empty")
    assert nothing.built == set() and len(nothing.report["missing"]) == 4


def test_write_public_writes_only_the_public_files_and_they_load_back(raw):
    """A public-only refresh writes datasets, qa and the two public playbooks. documents.json,
    review.json and extraction.json also hold the constructed sets (part 2): rewriting them from the
    public build alone would silently drop every constructed row, so they must not be written here.
    What is written must load back through the frozen models unchanged."""
    built = _build(raw)
    root = raw.root.parent
    written = b.write_public(built, content_dir=root / "content", docs_dir=root / "docs")
    assert sorted(p.relative_to(root / "content").as_posix() for p in written) == [
        "datasets.json",
        "items/qa.json",
        "playbooks/cuad_checklist.json",
        "playbooks/nda_checklist.json",
    ]
    for merged in ("documents.json", "items/review.json", "items/extraction.json"):
        assert not (root / "content" / merged).exists(), merged
    qa = json.loads((root / "content/items/qa.json").read_text())
    assert TypeAdapter(list[c.QAItem]).validate_python(qa) == built.qa
    assert (root / "docs/public/cuad-001.txt").read_bytes() == raw.alpha.encode()


def test_the_public_build_is_deterministic(raw):
    """FR-1: two builds from the same downloads must be identical, or two baseline runs would
    ask different questions and never be comparable."""
    first, second = _build(raw), _build(raw)
    assert first.texts == second.texts and first.report == second.report
    for name in ("documents", "review", "extraction", "qa", "playbooks", "datasets"):
        left, right = getattr(first, name), getattr(second, name)
        assert left == right


def test_building_the_public_sets_asks_no_model_and_touches_no_database():
    """AC-3 / FR-3: no answer key may come from the model under test, so the builder must not be
    able to call one; and it runs before the exam repoints the database, so importing it must
    not load the database or the AI client."""
    code = (
        "import sys, eval.exam.build\n"
        "heavy = ('anthropic', 'sqlalchemy', 'httpx')\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in heavy"
        " or m in ('app.core.database', 'app.integrations', 'app.integrations.claude'))\n"
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


# --- the downloader (T008; only ever driven with a fake fetch, never the network) ------------


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _fake_fetch(calls: list[str], fail: set[str] = frozenset()):
    archives = {
        b.CUAD_URL: _zip({"CUADv1.json": "{}", "test.json": "{}"}),
        b.CNLI_URL: _zip(
            {
                "contract-nli/test.json": "{}",
                "contract-nli/LICENSE": "CC BY 4.0",
                "contract-nli/train.json": "{}",
                "contract-nli/raw/a.pdf": "x",
            }
        ),
        b.LBR_URL: _zip(
            {
                "/": "",
                "benchmarks/cuad.json": "{}",
                "benchmarks/maud.json": "{}",
                "corpus/cuad/a.txt": "text",
                "corpus/maud/b.txt": "text",
            }
        ),
    }

    def fetch(url: str, dest: Path) -> None:
        calls.append(url)
        if url in fail:
            raise OSError("network down")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archives.get(url, b"markdown"))

    return fetch


def test_download_fetches_only_what_is_missing_and_keeps_only_what_is_read(tmp_path):
    """The raw folder is ~200 MB when only what the exam reads is kept; a second run on a
    complete folder must make no request at all."""
    calls: list[str] = []
    assert b.download_datasets(tmp_path, fetch=_fake_fetch(calls)) == []
    assert len(calls) == 3 + len(b.COMMONPAPER_FILES)
    kept = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert "cuad/CUADv1.json" in kept and "cuad/test.json" not in kept
    assert "cnli/contract-nli/test.json" in kept and "cnli/contract-nli/train.json" not in kept
    assert not any(k.startswith("cnli/contract-nli/raw") for k in kept)
    assert "lbr/benchmarks/cuad.json" in kept and "lbr/corpus/cuad/a.txt" in kept
    assert "lbr/benchmarks/maud.json" not in kept and "lbr/corpus/maud/b.txt" not in kept
    before = len(calls)
    assert b.download_datasets(tmp_path, fetch=_fake_fetch(calls)) == []
    assert len(calls) == before


def test_one_failed_download_is_reported_and_does_not_stop_the_others(tmp_path):
    """Spec edge case: an unavailable dataset is reported missing, not substituted; the rest of
    the downloads must still happen so the owner only re-fetches what failed."""
    calls: list[str] = []
    problems = b.download_datasets(tmp_path, fetch=_fake_fetch(calls, fail={b.CNLI_URL}))
    assert len(problems) == 1 and problems[0].startswith("cnli:") and "network down" in problems[0]
    assert (tmp_path / "cuad/CUADv1.json").is_file()
    assert (tmp_path / "lbr/benchmarks/cuad.json").is_file()
    assert not (tmp_path / "cnli/contract-nli/test.json").exists()


def test_an_archive_member_cannot_write_outside_the_raw_folder(tmp_path):
    """The archives come from the internet: a member named '../x' must be refused before any
    byte is written, or a hostile download could overwrite files outside the raw folder."""
    archive = tmp_path / "evil.zip"
    archive.write_bytes(_zip({"ok.txt": "fine", "../escaped.txt": "boom"}))
    with pytest.raises(ValueError, match="escapes"):
        b._extract(archive, tmp_path / "out", lambda name: True)
    assert not (tmp_path / "escaped.txt").exists()


def test_an_interrupted_unpack_is_redone_not_trusted(tmp_path):
    """A folder is complete only when its marker is written last; one with files but no marker
    (an interrupted unpack) must be extracted again, or the build would silently drop contracts."""
    calls: list[str] = []
    (tmp_path / "cuad").mkdir()
    (tmp_path / "cuad/CUADv1.json").write_text("half-written")
    assert b.download_datasets(tmp_path, fetch=_fake_fetch(calls)) == []
    assert (tmp_path / "cuad/CUADv1.json").read_text() == "{}"
    assert (tmp_path / "cuad" / b.EXTRACTED).is_file()


def test_a_bad_archive_is_deleted_so_the_next_run_fetches_it_again(tmp_path):
    """An error page saved as the archive passed the 'already on disk' check, failed to unzip,
    and was reused on every later run: the owner had to delete it by hand to get a retry."""
    calls: list[str] = []
    good = _fake_fetch(calls)

    def error_page_for_cuad(url: str, dest: Path) -> None:
        if url != b.CUAD_URL:
            return good(url, dest)
        calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"<html>503 Service Unavailable</html>")

    problems = b.download_datasets(tmp_path, fetch=error_page_for_cuad)
    assert len(problems) == 1 and problems[0].startswith("cuad:")
    assert not (tmp_path / "archives/cuad-data.zip").exists()
    assert b.download_datasets(tmp_path, fetch=good) == []
    assert calls.count(b.CUAD_URL) == 2
    assert (tmp_path / "cuad/CUADv1.json").is_file()


def test_an_archive_with_none_of_the_wanted_files_is_a_problem_every_run_not_a_skip(tmp_path):
    """After an upstream re-layout nothing matches the keep filter. Writing the 'complete'
    marker anyway made every later run skip the dataset and report its set silently missing."""
    calls: list[str] = []
    good = _fake_fetch(calls)

    def relaid_out(url: str, dest: Path) -> None:
        if url != b.CUAD_URL:
            return good(url, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_zip({"v2/CUAD.json": "{}"}))

    for _ in range(2):
        problems = b.download_datasets(tmp_path, fetch=relaid_out)
        assert len(problems) == 1 and problems[0].startswith("cuad:")
        assert not (tmp_path / "cuad" / b.EXTRACTED).exists()


# --- the committed public content (T008, FR-2, FR-4) ---------------------------------------

_ITEMS = c.CONTENT_DIR / "items"


def _committed(path: Path, model: type):
    return TypeAdapter(list[model]).validate_python(json.loads(path.read_text(encoding="utf-8")))


def test_every_public_dataset_names_its_version_licence_and_file_hashes():
    """AC-2: the committed registry is what the report quotes. Each dataset must have all three
    descriptors and every raw file hashed, and all four must have been available when built."""
    datasets = _committed(c.CONTENT_DIR / "datasets.json", c.Dataset)
    assert {d.key for d in datasets} == {"cuad", "contractnli", "legalbench_rag", "commonpaper"}
    for d in datasets:
        assert d.name and d.version and d.licence and d.url and d.available, d.key
        assert d.files, d.key
        assert all(re.fullmatch(r"[0-9a-f]{64}", f.sha256 or "") for f in d.files), d.key


def test_committed_extraction_is_forty_cuad_contracts_with_well_formed_keys():
    """AC-4 / FR-4a: 40 CUAD contracts, each field in its normalised form, with its quote. They
    come first in the file; the constructed contracts' keys follow them."""
    items = [
        i
        for i in _committed(_ITEMS / "extraction.json", c.ExtractionItem)
        if i.category == c.SourceCategory.PUBLIC
    ]
    registry = {d.key: d for d in _committed(c.CONTENT_DIR / "datasets.json", c.Dataset)}
    source = registry["cuad"]
    assert [i.item_id for i in items] == [f"ex-cuad-{n:03d}" for n in range(1, 41)]
    assert [i.doc_id for i in items] == [f"cuad-{n:03d}" for n in range(1, 41)]
    names_per_field: dict[str, int] = {}
    for item in items:
        assert item.category == c.SourceCategory.PUBLIC and item.source == f"CUAD {source.version}"
        fields = [f.field for f in item.fields]
        assert len(fields) == len(set(fields)), item.item_id
        for f in item.fields:
            names_per_field[f.field] = names_per_field.get(f.field, 0) + 1
            if f.value is None:
                assert f.quote is None, (item.item_id, f.field)
                continue
            assert f.quote and len(f.quote) <= b.QUOTE_MAX
            if f.kind == "parties":
                assert isinstance(f.value, list) and 1 <= len(f.value) <= b.MAX_PARTIES
            elif f.kind == "date":
                assert re.fullmatch(r"(19|20)\d\d-\d\d-\d\d", f.value)
            elif f.kind == "jurisdiction":
                assert f.value == f.value.lower() and f.value
            else:
                assert f.kind == "passage" and f.value == f.quote
    assert names_per_field["governing_law"] >= 35 and names_per_field["parties"] >= 35


def test_committed_qa_is_one_hundred_questions_on_the_cuad_contracts():
    """AC-4 / FR-4c: 100 questions with their exact passages, at most three per contract, none
    repeated, all on contracts that are in the CUAD set (so each is ingested once)."""
    qa = _committed(_ITEMS / "qa.json", c.QAItem)
    docs = {d.doc_id for d in _ALL_DOCS if d.dataset == "cuad"}
    registry = {d.key: d for d in _committed(c.CONTENT_DIR / "datasets.json", c.Dataset)}
    assert len(docs) == 40
    assert [q.item_id for q in qa] == [f"qa-{n:03d}" for n in range(1, 101)]
    assert {q.doc_id for q in qa} <= docs
    assert len({(q.doc_id, q.question) for q in qa}) == 100
    assert max(sum(q.doc_id == d for q in qa) for d in docs) <= b.QA_PER_DOC
    lbr = registry["legalbench_rag"]
    for q in qa:
        assert q.category == c.SourceCategory.PUBLIC
        assert q.source == f"LegalBench-RAG {lbr.version} / cuad"
        assert 1 <= len(q.passages) <= 3 and sum(map(len, q.passages)) <= b.PASSAGE_MAX


def test_committed_public_playbooks_have_the_checklist_rules_and_no_severity():
    """AC-5 / D-9: the review keys name rules by clause type, and attribution by clause type
    only works if no two rules collapse to one canonical type; a public rule carries no severity."""
    for key, rules in (("cuad_checklist", 8), ("nda_checklist", 17)):
        path = c.CONTENT_DIR / "playbooks" / f"{key}.json"
        playbook = json.loads(path.read_text(encoding="utf-8"))
        types = [r["clause_type"] for r in playbook["rules"]]
        assert len(types) == rules and len(set(types)) == rules
        assert [canonical_clause_type(t) for t in types] == types
        assert all(r["risk_level"] is None and r["preferred_position"] for r in playbook["rules"])
        assert playbook["name"] and playbook["description"] and playbook["source"]
    cuad = json.loads((c.CONTENT_DIR / "playbooks/cuad_checklist.json").read_text(encoding="utf-8"))
    assert [r["clause_type"] for r in cuad["rules"]] == list(b.CUAD_REVIEW_RULES.values())


_RAW_PRESENT = (c.RAW_DIR / b.LBR_JSON).is_file() and (c.RAW_DIR / b.CUAD_JSON).is_file()
needs_raw = pytest.mark.skipif(
    not (_RAW_PRESENT and (c.RAW_DIR / b.CNLI_JSON).is_file()),
    reason="public datasets not downloaded (eval/contracts/raw)",
)


@needs_raw
def test_committed_public_content_is_exactly_what_the_build_produces():
    """FR-1: the committed files must be reproducible from the downloads. A hand edit, a
    different selection or a non-deterministic build shows up here, before it shows up as a
    baseline that cannot be reproduced. Also states the exact counts FR-4 asks the report for."""
    built = b.build_public()
    assert built.report["missing"] == [] and built.built == {"cuad", "contractnli", "qa"}
    assert [d.model_dump(mode="json") for d in built.datasets] == json.loads(
        (c.CONTENT_DIR / "datasets.json").read_text(encoding="utf-8")
    )
    qa = json.loads((_ITEMS / "qa.json").read_text(encoding="utf-8"))
    assert [i.model_dump(mode="json") for i in built.qa] == qa
    # extraction.json also holds the constructed keys, after the public ones
    committed = json.loads((_ITEMS / "extraction.json").read_text(encoding="utf-8"))
    assert [i.model_dump(mode="json") for i in built.extraction] == [
        i for i in committed if i["category"] == "public"
    ]
    for key, playbook in built.playbooks.items():
        assert playbook == json.loads(
            (c.CONTENT_DIR / "playbooks" / f"{key}.json").read_text(encoding="utf-8")
        )
    report = built.report
    assert report["cuad"]["documents"] == 40 and report["contractnli"]["documents"] == 10
    assert report["contractnli"]["verdicts"] == 170 and report["qa"]["questions"] == 100
    assert sum(1 for i in built.review if i.doc_id.startswith("cuad-")) == 40
    assert report["cuad"]["review_deviations_past_25k"] > 0
    assert report["cuad"]["extraction_fields_past_25k"] > 0 and 0 < report["qa"]["past_25k"] < 100
    assert report["cuad"]["labels_not_found_in_text"] == 0


@needs_raw
def test_every_committed_quote_occurs_in_its_contract_text():
    """AC-3 / FR-12: a key quote that is not in the text it is scored against could never be
    cited by any answer. Compared ignoring spacing, as the AI's quotes are."""
    built = b.build_public()
    flat = {d.doc_id: " ".join(built.texts[d.file].decode().split()) for d in built.documents}

    def present(doc_id: str, quote: str) -> bool:
        return " ".join(quote.split()) in flat[doc_id]

    for item in built.extraction:
        for f in item.fields:
            assert f.quote is None or present(item.doc_id, f.quote), (item.item_id, f.field)
    for q in built.qa:
        assert all(present(q.doc_id, p) for p in q.passages), q.item_id
    for review in built.review:
        for r in review.rules:
            assert r.evidence_quote is None or present(review.doc_id, r.evidence_quote)


# =============================================================================================
# The constructed sets (T012: FR-5, FR-6, FR-7, FR-8, FR-9, FR-30)
# =============================================================================================

_DOCS_ON_DISK = c.DOCS_DIR / "constructed"
_CP_RAW = all((c.RAW_DIR / "commonpaper" / f).is_file() for f in ("CSA.md", "DPA.md"))
needs_cp = pytest.mark.skipif(not _CP_RAW, reason="Common Paper files not downloaded")
needs_everything = pytest.mark.skipif(
    not (_CP_RAW and _RAW_PRESENT and (c.RAW_DIR / b.CNLI_JSON).is_file()),
    reason="public datasets and Common Paper files not downloaded (eval/contracts/raw)",
)


@pytest.fixture(scope="module")
def aegis(tmp_path_factory) -> b.ConstructedBuild:
    """Everything that needs no download: the AEGIS documents, the long ones, their scanned twins
    and the portfolio. (An empty raw folder leaves the Common Paper documents out, by design.)"""
    return b.build_constructed(tmp_path_factory.mktemp("no-raw"))


@pytest.fixture(scope="module")
def full() -> b.ConstructedBuild:
    return b.build_constructed(c.RAW_DIR)


def _text(built: b.ConstructedBuild, doc_id: str) -> str:
    return built.texts[f"constructed/{doc_id}.txt"].decode("utf-8")


def _library_rule(book: str, clause: str) -> dict:
    return next(
        r
        for p in PLAYBOOK_LIBRARY
        if p["name"] == book
        for r in p["rules"]
        if r["clause_type"] == clause
    )


# --- planting a change: the mechanics ----------------------------------------------------------

_BASE = (
    "TITLE\n\nFirst paragraph that sits well above the changes, long enough to anchor them.\n\n"
    "1. Cap. The cap is twelve months of fees.\n\n2. Notice. Notice is thirty days.\n\n"
    "3. Law. The law is Delaware.\n\n4. Last. Nothing else here at all.\n"
)


def test_plants_land_where_the_record_says_even_when_earlier_ones_change_the_length():
    """FR-5: the answer record's offset is where the change is in the *generated* text. A longer
    replacement before a plant, or a deletion before it, must shift its offset by exactly that
    much, or every later plant in the document would be located in the wrong place."""
    plants = [
        b.Plant("law", "The law is Delaware.", "The law is the Cayman Islands, for any claim."),
        b.Plant("cap", "twelve months of fees", "one month"),
        b.Plant("notice", b.Line("2. Notice."), None),
    ]
    final, applied = b.apply_plants(_BASE, plants)
    by_rule = {p.rule: (original, at) for p, original, at in applied}
    assert final.startswith(_BASE[: _BASE.index("twelve")])
    assert final[by_rule["cap"][1] :].startswith("one month")
    assert final[by_rule["law"][1] :].startswith("The law is the Cayman Islands, for any claim.")
    assert by_rule["cap"][0] == "twelve months of fees"
    assert "Notice is thirty days" not in final
    at_notice = by_rule["notice"][1]
    assert final[at_notice:].startswith("3. Law.") and final[:at_notice].endswith("one month.\n\n")


def test_a_deleted_paragraph_takes_its_line_break_with_it():
    """A deletion that left a blank gap (or half a numbered line) would hand the model a
    visible hint of where to look, and make the document unlike a real one."""
    final, _ = b.apply_plants("a\n\n2. Gone.\n\nb\n", [b.Plant("x", b.Line("Gone."), None)])
    assert final == "a\n\nb\n"
    items = "(a) one;\n(b) two;\n(c) three\n"
    final, _ = b.apply_plants(items, [b.Plant("x", b.Line("(b) two"), None)])
    assert final == "(a) one;\n(c) three\n"


def test_a_find_must_match_exactly_once_or_the_build_refuses():
    """A plant whose text drifted (a template edit, an upstream change to a Common Paper file) must
    stop the build - silently planting nothing, or in the wrong clause, would leave an answer key
    that describes a change which is not there."""
    for find in ("not in the document", "Notice", ("Cap.", "never ends")):
        with pytest.raises(ValueError, match="occurs|does not follow"):
            b.apply_plants(_BASE, [b.Plant("x", find, "y")])


def test_two_plants_on_the_same_text_are_refused():
    """Two changes to one sentence leave neither with a clean answer."""
    plants = [
        b.Plant("a", "The cap is twelve months of fees.", "The cap is nil."),
        b.Plant("b", "twelve months", "six months"),
    ]
    with pytest.raises(ValueError, match="overlaps"):
        b.apply_plants(_BASE, plants)


def test_a_find_is_a_string_a_span_or_a_whole_line():
    """The three ways a plant names its text: an exact string, from one string through the next
    one after it, or the whole line (list number and indent included) holding a phrase."""
    text = "x\n    3. From Provider.  It will not reduce.\ny One. Two end. Three end.\n"
    start, stop = b._locate(text, b.Line("will not reduce"))
    assert text[start:stop] == "    3. From Provider.  It will not reduce."
    start, stop = b._locate(text, b.span("One.", "end."))
    assert text[start:stop] == "One. Two end."
    start, stop = b._locate(text, "Three")
    assert text[start:stop] == "Three"


def test_a_change_too_near_the_top_to_have_an_anchor_is_refused():
    """The anchor (the text just before a change) is what locates a deletion; one with fewer than
    60 characters before it could match anywhere."""
    final, applied = b.apply_plants("short\n\nThe cap is nil.\n", [b.Plant("x", "nil", "zero")])
    with pytest.raises(ValueError, match="anchored"):
        b.planted_changes(final, applied, lambda rule: ("high", "x / x"))


def test_two_changes_to_one_rule_in_one_document_are_refused():
    """The answer record is one finding per rule; a second plant on the same rule would silently
    overwrite the first and leave a key that no longer matches the text."""
    final, applied = b.apply_plants(
        _BASE,
        [b.Plant("cap", "twelve months", "one month"), b.Plant("cap", "thirty days", "one day")],
    )
    with pytest.raises(ValueError, match="two changes break cap"):
        b.planted_changes(final, applied, lambda rule: ("high", "x / x"))


def test_a_change_records_its_status_text_anchor_and_the_platform_severity():
    """FR-5: each planted change states what was changed, the new text, where it sits and its
    severity - the severity handed in by the platform rule, never decided here."""
    final, applied = b.apply_plants(
        _BASE,
        [
            b.Plant("cap", "twelve months of fees", "one month"),
            b.Plant("law", b.Line("3. Law."), None),
        ],
    )
    changes = b.planted_changes(final, applied, lambda rule: ("critical", f"Book / {rule}"))
    cap, law = changes["cap"], changes["law"]
    assert cap.status == "deviation" and cap.changed_text == "one month"
    assert cap.original_text == "twelve months of fees"
    assert law.status == "missing" and law.changed_text is None
    assert law.original_text == "3. Law. The law is Delaware."
    assert cap.severity == law.severity == "critical" and cap.severity_source == "Book / cap"
    assert final[cap.char_start - len(cap.anchor_text) : cap.char_start] == cap.anchor_text
    assert len(cap.anchor_text) >= 60
    with pytest.raises(ValueError, match="no severity"):
        b.planted_changes(final, applied, lambda rule: None)


@pytest.mark.parametrize(("n_rules", "n_docs"), [(9, 3), (8, 3), (7, 3), (10, 9), (6, 6)])
def test_every_rule_is_planted_somewhere_and_each_document_gets_two_to_four_distinct_rules(
    n_rules, n_docs
):
    """FR-5: the planted documents together must exercise every rule (a rule never planted is never
    measured), and no document breaks a rule twice or gets more than four changes."""
    rules = [f"rule_{i}" for i in range(n_rules)]
    plan = b.assign_rules(rules, n_docs, "tag")
    assert plan == b.assign_rules(rules, n_docs, "tag")
    assert len(plan) == n_docs
    assert all(2 <= len(p) <= 4 and len(set(p)) == len(p) for p in plan)
    assert {r for p in plan for r in p} == set(rules)


def test_rules_that_cannot_be_spread_without_repeating_are_refused():
    """One usable rule cannot give two distinct plants per document."""
    with pytest.raises(ValueError, match="cannot spread"):
        b.assign_rules(["only"], 3, "tag")


def test_a_severity_is_only_ever_the_one_a_person_set_on_the_platform_rule():
    """FR-5 / D-9: a missing playbook, a missing rule and a rule with no (or an invented) severity
    all give None, so nothing is planted for them."""
    library = [
        {"name": "Book", "rules": [
            {"clause_type": "a", "risk_level": "high"},
            {"clause_type": "b", "risk_level": None},
            {"clause_type": "c", "risk_level": "urgent"},
            {"clause_type": "d"},
        ]},
    ]
    assert b.library_severity(library, "Book", "a") == "high"
    nothing = (("Book", "b"), ("Book", "c"), ("Book", "d"), ("Book", "z"), ("Nope", "a"))
    for playbook, clause in nothing:
        assert b.library_severity(library, playbook, clause) is None


# --- the Common Paper playbooks and the frozen severity mapping ---------------------------------

# plan.md, "Interface freeze": exam rule -> what it resolves to in app/playbooks/library.py
_FROZEN = {
    "limitation_of_liability": "high", "governing_law": "medium", "renewal_notice": "medium",
    "termination_for_breach": "medium", "confidentiality": "medium", "ip_indemnity": "high",
    "payment_terms": "medium", "return_of_data": "medium", "assignment": "medium",
    "service_warranty": "medium", "breach_notification": "critical", "subprocessor_notice": "high",
    "subprocessor_liability": "high", "audit_rights": "medium", "deletion_at_expiry": "medium",
    "inquiry_notice": "medium",
}


def test_the_common_paper_rules_resolve_to_the_severities_the_plan_froze():
    """FR-5: if someone edits the platform playbook library, the exam's planted severities change
    with it - which changes the content and needs a new baseline. This pins today's values so
    that change is noticed here, at review, not discovered later as a baseline that will not
    reproduce."""
    rules = {r[0]: (r[1], r[2]) for r in (*b.CP_MSA_RULES, *b.CP_DPA_RULES)}
    assert set(rules) == set(_FROZEN)
    resolved = {rule: b.library_severity(PLAYBOOK_LIBRARY, *where) for rule, where in rules.items()}
    assert resolved == _FROZEN


def test_the_common_paper_rule_types_stay_apart_when_canonicalised():
    """Review findings are attributed to a rule by clause type (the runner canonicalises both
    sides); two rules that collapse into one canonical type would take each other's findings."""
    for rules in (b.CP_MSA_RULES, b.CP_DPA_RULES):
        canonical = [canonical_clause_type(r[0]) for r in rules]
        assert len(set(canonical)) == len(canonical)


def test_the_committed_common_paper_playbooks_name_their_platform_rule_and_copy_its_severity():
    """FR-5: each exam rule says which platform rule it stands for (severity_from) and carries that
    rule's risk_level, so the review prompt and every plant read the same person-set severity."""
    for key, expected in (("cp_msa", b.CP_MSA_RULES), ("cp_dpa", b.CP_DPA_RULES)):
        path = c.CONTENT_DIR / "playbooks" / f"{key}.json"
        playbook = json.loads(path.read_text(encoding="utf-8"))
        assert [r["clause_type"] for r in playbook["rules"]] == [r[0] for r in expected]
        assert playbook["name"] and playbook["description"] and "CC BY 4.0" in playbook["source"]
        for rule, (_, book, clause, position) in zip(playbook["rules"], expected, strict=True):
            assert rule["severity_from"] == {"playbook": book, "clause_type": clause}
            assert rule["risk_level"] == _library_rule(book, clause)["risk_level"]
            assert rule["preferred_position"] == position and rule["sample_clause"]


# --- the committed constructed content ----------------------------------------------------------

_ALL_DOCS = _committed(c.CONTENT_DIR / "documents.json", c.ExamDocument)
_REVIEW = _committed(_ITEMS / "review.json", c.ReviewItem)
_CONSTRUCTED_REVIEW = [i for i in _REVIEW if i.category == c.SourceCategory.CONSTRUCTED]


def _docs(prefix: str) -> list[c.ExamDocument]:
    return [d for d in _ALL_DOCS if d.doc_id.startswith(prefix)]


def test_the_exam_holds_the_planned_public_and_constructed_documents():
    """FR-4 / FR-5 / FR-8 / FR-9: 40 CUAD + 10 NDAs, 18 Common Paper, 24 AEGIS, 4 long, 6 scanned
    twins and 12 extra portfolio contracts - 52 constructed review documents counting the twins.
    A missing group would silently shrink a measure."""
    groups = ("cuad-", "cnli-", "cp-msa-", "cp-dpa-", "ae-long-", "scan-", "pf-")
    counts = {p: len(_docs(p)) for p in groups}
    assert counts == {
        "cuad-": 40, "cnli-": 10, "cp-msa-": 11, "cp-dpa-": 7, "ae-long-": 4, "scan-": 6, "pf-": 12,
    }
    assert len([d for d in _ALL_DOCS if re.fullmatch(r"ae-(?!long)[a-z]+-\d\d", d.doc_id)]) == 24
    assert len(_ALL_DOCS) == 114 and len({d.doc_id for d in _ALL_DOCS}) == 114
    assert len(_CONSTRUCTED_REVIEW) + len(_docs("scan-")) == 52


def test_documents_say_who_uploads_them_what_the_ai_does_and_which_dataset_they_come_from():
    """FR-9 / FR-37: the 40 portfolio contracts are uploaded by the portfolio user (so the app's own
    access rules scope the portfolio questions to them); Common Paper documents record their
    dataset so the report can quote its licence; no constructed file is claimed to be public."""
    portfolio = [d for d in _ALL_DOCS if d.uploader == "portfolio"]
    assert len(portfolio) == 40
    assert all(d.ai_steps == ["metadata", "clauses", "brain"] for d in portfolio)
    constructed = [d for d in _ALL_DOCS if d.category == c.SourceCategory.CONSTRUCTED]
    runner = [d for d in constructed if d.uploader == "runner"]
    assert len(runner) == 24 and all(d.ai_steps == ["metadata"] for d in runner)
    datasets = {d.key for d in _committed(c.CONTENT_DIR / "datasets.json", c.Dataset)}
    for d in _ALL_DOCS:
        if d.category == c.SourceCategory.CONSTRUCTED:
            assert d.dataset in (None, "commonpaper") and d.file.startswith("constructed/")
            assert (d.dataset == "commonpaper") == d.doc_id.startswith(("cp-", "scan-cp-"))
            assert d.dataset is None or d.dataset in datasets
            assert d.mime_type == ("application/pdf" if d.twin_of else "text/plain")
            assert re.fullmatch(r"[0-9a-f]{64}", d.file_sha256)


def test_every_planted_change_carries_the_whole_answer_record():
    """AC-5: what was changed, the changed text, where it sits, the correct finding and the severity
    - for all of the planted changes, with the status agreeing with the text (a deletion has no
    changed text, a replacement has)."""
    plants = [r for i in _CONSTRUCTED_REVIEW for r in i.rules if r.planted]
    assert len(plants) == 96
    for rule in plants:
        p = rule.planted
        assert p.rule == rule.rule and rule.status == p.status and rule.severity == p.severity
        assert p.original_text and len(p.anchor_text) >= 60 and p.char_start >= 60
        assert p.severity in ("low", "medium", "high", "critical") and p.severity_source
        assert (p.status == "missing") == (p.changed_text is None)
        assert (rule.evidence_quote is None) == (p.status == "missing")


def test_controls_have_no_plants_and_planted_documents_have_two_to_four_on_distinct_rules():
    """AC-5: unchanged control documents let false alarms be counted - 3 Common Paper and 6 AEGIS
    ones - and every other constructed document breaks 2-4 distinct rules."""
    controls = [i for i in _CONSTRUCTED_REVIEW if i.control]
    assert len(controls) == 9
    assert sum(i.playbook.startswith("cp_") for i in controls) == 3
    for item in _CONSTRUCTED_REVIEW:
        planted = [r.rule for r in item.rules if r.planted]
        assert item.item_id == f"rv-{item.doc_id}" and item.source == "constructed"
        assert len({r.rule for r in item.rules}) == len(item.rules)
        if item.control:
            assert planted == [] and all(r.status == "compliant" for r in item.rules)
        else:
            assert 2 <= len(planted) <= 4 and len(set(planted)) == len(planted), item.item_id
        assert all((r.status == "compliant") == (r.planted is None) for r in item.rules)


def test_every_rule_with_a_clause_to_break_is_planted_in_at_least_one_document():
    """FR-5: a rule that is never planted is never measured. Every rule of each Common Paper
    playbook, and every rule the AEGIS catalogue lists for a template, is broken in at least one
    document of its set (the long documents add to the MSA and SaaS counts, not replace them)."""
    planted: dict[str, set[str]] = {}
    for item in _CONSTRUCTED_REVIEW:
        if item.doc_id.startswith("ae-long-"):
            continue
        planted.setdefault(item.playbook, set()).update(r.rule for r in item.rules if r.planted)
    assert planted["cp_msa"] == {r[0] for r in b.CP_MSA_RULES}
    assert planted["cp_dpa"] == {r[0] for r in b.CP_DPA_RULES}
    for kind, book in b.AEGIS_PLAYBOOK.items():
        assert planted[book] == {p.rule for p in b.AEGIS_CATALOGUE[kind]}, book


def test_every_planted_severity_is_the_platform_rules_own():
    """AC-5 / D-9: the severity of a planted change equals the risk_level set on the playbook rule
    it breaks - the library rule directly for AEGIS documents, the rule named by severity_from for
    Common Paper ones - and says so in severity_source."""
    exam = {
        key: json.loads((c.CONTENT_DIR / "playbooks" / f"{key}.json").read_text(encoding="utf-8"))
        for key in ("cp_msa", "cp_dpa")
    }
    for item in _CONSTRUCTED_REVIEW:
        for rule in (r for r in item.rules if r.planted):
            if item.playbook in exam:
                row = next(r for r in exam[item.playbook]["rules"] if r["clause_type"] == rule.rule)
                book, clause = row["severity_from"]["playbook"], row["severity_from"]["clause_type"]
            else:
                book, clause = item.playbook, rule.rule
            expected = _library_rule(book, clause)["risk_level"]
            assert rule.planted.severity == expected, (item.item_id, rule.rule)
            assert rule.planted.severity_source == f"{book} / {clause}"


def test_aegis_review_documents_are_reviewed_against_the_playbook_of_their_type():
    """The library playbook that reviews a drafted contract is picked by its type; the exam must key
    each AEGIS document against that same playbook, by name, and only on rules it really has."""
    expected = {
        "msa": "Master Services Agreement Playbook",
        "consultancy": "Consultancy Agreement Playbook",
        "vendor": "Vendor Agreement Playbook", "saas": "Software / SaaS Agreement Playbook",
        "sow": "Statement of Work Playbook", "dpa": "Data Processing Agreement Playbook",
    }
    for item in _CONSTRUCTED_REVIEW:
        if item.playbook.startswith("cp_"):
            continue
        kind = item.doc_id.split("-")[1]
        book = expected["msa" if item.doc_id in ("ae-long-01", "ae-long-02", "ae-long-03") else
                        "saas" if item.doc_id == "ae-long-04" else kind]
        assert item.playbook == book
        library_rules = {
            r["clause_type"] for p in PLAYBOOK_LIBRARY if p["name"] == book for r in p["rules"]
        }
        assert {r.rule for r in item.rules} <= library_rules


def test_enough_planted_changes_start_past_25k_and_the_four_long_documents_hold_them():
    """AC-7 / FR-7: at least 10 planted changes start past 25,000 characters, so the report can
    score them apart; they are the changes in the long documents' schedules."""
    past = [(i.doc_id, r.rule) for i in _CONSTRUCTED_REVIEW for r in i.rules
            if r.planted and r.planted.char_start > c.CUTOFF]
    assert len(past) >= 10
    assert {d for d, _ in past} == {f"ae-long-0{n}" for n in range(1, 5)}
    for item in (i for i in _CONSTRUCTED_REVIEW if i.doc_id.startswith("ae-long-")):
        starts = [r.planted.char_start for r in item.rules if r.planted]
        assert len(starts) == 3 and min(starts) > c.CUTOFF


def test_scanned_twins_point_at_their_text_original_and_have_no_record_of_their_own():
    """AC-8: a scanned twin shares its original's answer record (so it has no review item of its
    own), is a PDF, and the set has at least one long, two Common Paper and two AEGIS twins."""
    by_id = {d.doc_id: d for d in _ALL_DOCS}
    twins = _docs("scan-")
    assert len(twins) == 6
    originals = [d.twin_of for d in twins]
    assert originals == list(b.SCAN_TWINS)
    for twin in twins:
        original = by_id[twin.twin_of]
        assert twin.doc_id == f"scan-{twin.twin_of}" and original.mime_type == "text/plain"
        assert twin.file.endswith(".pdf") and original.twin_of is None
        assert twin.doc_id not in {i.doc_id for i in _REVIEW}
        assert any(i.doc_id == original.doc_id and not i.control for i in _REVIEW)
    assert sum(o.startswith("cp-") for o in originals) >= 2
    assert sum(re.fullmatch(r"ae-(?!long)\w+-\d\d", o) is not None for o in originals) >= 2
    assert sum(o.startswith("ae-long-") for o in originals) >= 1


def test_committed_extraction_has_a_key_for_every_constructed_text_contract_and_none_for_twins():
    """FR-5 / FR-8: all 58 constructed contracts carry extraction keys (so their metadata call has
    something to be scored against) and a scanned twin has none of its own - the scorer compares it
    with its original's, which therefore must exist."""
    items = [
        i
        for i in _committed(_ITEMS / "extraction.json", c.ExtractionItem)
        if i.category == c.SourceCategory.CONSTRUCTED
    ]
    constructed = [d for d in _ALL_DOCS if d.category == c.SourceCategory.CONSTRUCTED]
    originals = [d.doc_id for d in constructed if d.twin_of is None]
    assert len(items) == len(originals) == 58
    assert [i.doc_id for i in items] == originals and all(i.item_id == f"ex-{i.doc_id}" for i in items)
    assert not [d.doc_id for d in constructed if d.twin_of and d.doc_id in {i.doc_id for i in items}]
    for item in items:
        names = [f.field for f in item.fields]
        assert {"parties", "effective_date"} <= set(names) and len(names) == len(set(names))
        assert item.source == "constructed" and all(f.value is not None and f.quote for f in item.fields)


def test_the_portfolio_is_forty_contracts_with_nine_exact_questions():
    """AC-9 / FR-9: 40 contracts and 9 questions, each with a non-empty list of contracts drawn from
    that portfolio, in portfolio order, and one counterparty used in exactly three contracts."""
    portfolio = [d.doc_id for d in _ALL_DOCS if d.uploader == "portfolio"]
    questions = _committed(_ITEMS / "portfolio.json", c.PortfolioQuestion)
    assert len(portfolio) == 40 and len(questions) == 9
    assert [q.item_id for q in questions] == [f"pq-{n:02d}" for n in range(1, 10)]
    for q in questions:
        assert q.question.endswith("?") and q.fact
        assert q.expected_doc_ids and set(q.expected_doc_ids) <= set(portfolio)
        assert q.expected_doc_ids == [d for d in portfolio if d in q.expected_doc_ids]
        assert len(q.expected_doc_ids) < len(portfolio)
    assert len({q.question for q in questions}) == 9
    kestrel = next(q for q in questions if q.fact == "counterparty")
    assert len(kestrel.expected_doc_ids) == 3 and b.KESTREL in kestrel.question


def test_the_sample_has_a_little_of_every_task_and_names_only_things_that_exist():
    """AC-30 / FR-30: the fixed sample covers review, extraction, Q&A, portfolio and routing, with a
    control, a planted document, a long one and a scanned twin in review, a sub-portfolio whose
    questions it can partly answer, and no id that is not in the exam."""
    sample = json.loads((c.CONTENT_DIR / "sample.json").read_text(encoding="utf-8"))
    assert set(sample) == {"review", "extraction", "qa", "portfolio", "routing", "portfolio_docs"}
    known_review = {i.item_id for i in _REVIEW} | {f"rv-{d.doc_id}" for d in _docs("scan-")}
    assert len(sample["review"]) == 8 and set(sample["review"]) <= known_review
    by_id = {i.item_id: i for i in _REVIEW}
    review = [by_id[i] for i in sample["review"] if i in by_id]
    assert sum(i.category == c.SourceCategory.PUBLIC for i in review) == 4
    assert any(i.control for i in review) and any(i.doc_id == "ae-long-01" for i in review)
    assert "rv-scan-ae-msa-02" in sample["review"] and "rv-ae-msa-02" in sample["review"]
    keys = {i.item_id: i for i in _committed(_ITEMS / "extraction.json", c.ExtractionItem)}
    assert sample["extraction"] and set(sample["extraction"]) <= set(keys)
    # extraction is asked of the CUAD contracts and of the constructed review documents that have a
    # key: the control, the planted Indian one and the long one - not of a ContractNLI document or
    # of a scanned twin (no key of its own)
    constructed = [keys[i] for i in sample["extraction"] if keys[i].doc_id.startswith("ae-")]
    assert [i.doc_id for i in constructed] == ["ae-msa-01", "ae-msa-02", "ae-long-01"]
    assert all(i.category == c.SourceCategory.CONSTRUCTED for i in constructed)
    assert sample["extraction"][:3] == [i for i in sample["extraction"] if "cuad" in i]
    qa = {q.item_id: q for q in _committed(_ITEMS / "qa.json", c.QAItem)}
    assert sample["qa"] and set(sample["qa"]) <= set(qa)
    cuad_docs = {keys[i].doc_id for i in sample["extraction"] if keys[i].doc_id.startswith("cuad-")}
    assert {qa[i].doc_id for i in sample["qa"]} == cuad_docs and len(cuad_docs) == 3
    questions = {q.item_id: q for q in _committed(_ITEMS / "portfolio.json", c.PortfolioQuestion)}
    docs = set(sample["portfolio_docs"])
    portfolio = {d.doc_id for d in _ALL_DOCS if d.uploader == "portfolio"}
    assert len(docs) == 6 and docs <= portfolio and len(sample["portfolio"]) == 2
    for question_id in sample["portfolio"]:
        answer = set(questions[question_id].expected_doc_ids) & docs
        assert answer and answer != docs  # the sub-portfolio can answer it, in part
    assert sample["routing"] == [f"rt-{n:02d}" for n in range(1, 11)]
    assert {i.doc_id for i in review if i.doc_id.startswith("cuad-")} == cuad_docs
    assert {k.doc_id for k in constructed} <= {i.doc_id for i in review}  # asked and reviewed


@needs_raw
def test_the_sample_reaches_past_25k_in_every_task_that_can():
    """AC-30: the sample must include items past 25,000 characters - Q&A answers (at least four, and
    some before 25k so both buckets can be read), extraction keys and CUAD review labels - or sample
    mode would never show the long-document problem it exists to catch."""
    built = b.build_public()
    sample = json.loads((c.CONTENT_DIR / "sample.json").read_text(encoding="utf-8"))
    text = {d.doc_id: built.texts[d.file].decode() for d in built.documents}
    qa = [q for q in built.qa if q.item_id in sample["qa"]]
    starts = [min(text[q.doc_id].find(p) for p in q.passages) for q in qa]
    assert sum(s >= c.CUTOFF for s in starts) >= 4 and sum(s < c.CUTOFF for s in starts) >= 1
    ex = [i for i in built.extraction if i.item_id in sample["extraction"]]
    quotes = [(i.doc_id, f.quote) for i in ex for f in i.fields if f.quote]
    assert sum(text[doc].find(quote) >= c.CUTOFF for doc, quote in quotes) >= 1
    review = [i for i in built.review if i.item_id in sample["review"]]
    past_docs = [
        i
        for i in review
        if any(
            r.evidence_quote and text[i.doc_id].find(r.evidence_quote) >= c.CUTOFF for r in i.rules
        )
    ]
    assert len(past_docs) >= 2


def test_content_fingerprint_follows_a_planted_severity_and_a_portfolio_answer(monkeypatch):
    """FR-27 / FR-28: change a severity, or the right list for a portfolio question, and the exam is
    a different exam: the fingerprint must change, or an old baseline would be compared with new
    keys."""
    content = c.load_content()
    first = c.content_fingerprint(content)
    assert first == c.content_fingerprint(c.load_content())
    planted = next(r for i in content.review for r in i.rules if r.planted)
    severity = planted.planted.severity
    planted.planted.severity = "low" if severity != "low" else "high"
    assert c.content_fingerprint(content) != first
    planted.planted.severity = severity
    content.portfolio[0].expected_doc_ids = content.portfolio[0].expected_doc_ids[:-1]
    assert c.content_fingerprint(content) != first


# --- the build itself --------------------------------------------------------------------------


def _check_plants_are_where_the_record_says(built: b.ConstructedBuild) -> int:
    checked = 0
    for item in built.review:
        for rule in (r for r in item.rules if r.planted):
            p, text = rule.planted, _text(built, item.doc_id)
            assert text[p.char_start - len(p.anchor_text) : p.char_start] == p.anchor_text
            if p.status == "deviation":
                assert text.startswith(p.changed_text, p.char_start)
            else:
                assert p.original_text not in text  # the deleted text is really gone
            checked += 1
    return checked


def test_every_aegis_planted_change_is_in_the_text_where_its_record_says(aegis):
    """AC-5: the record and the document must agree - the new text at the recorded offset with the
    recorded anchor before it, a deleted text no longer anywhere - or the AI would be scored against
    a change that is not in the file it read."""
    assert _check_plants_are_where_the_record_says(aegis) == 6 * 3 * 3 + 4 * 3  # 54 + 12


@needs_cp
def test_every_common_paper_planted_change_is_in_the_text_where_its_record_says(full):
    """AC-5, for the Common Paper documents (cover-page values and body clauses)."""
    assert _check_plants_are_where_the_record_says(full) == 96  # + 30 in the Common Paper documents


def test_the_aegis_documents_have_indian_law_inr_amounts_and_indian_style_entities(aegis):
    """AC-6 / FR-6: the 24 review documents, the long ones and the Indian portfolio contracts state
    Indian governing law with the courts of a named city, amounts in INR, Indian-style company names
    and registered offices - and each has its own answer record."""
    keyed = {i.doc_id for i in aegis.review}
    indian = [d for d in aegis.deals.values() if d.law == "India"]
    assert len([d for d in indian if not d.doc_id.startswith("pf-")]) == 28
    for deal in indian:
        text = _text(aegis, deal.doc_id)
        assert "laws of India" in text
        assert f"The courts at {deal.city} shall have exclusive jurisdiction" in text
        for name in (deal.company, deal.counterparty):
            assert re.search(r"Private Limited|Pvt\. Ltd\.|Limited|LLP|^M/s ", name), name
        assert f"registered office at {deal.company_office}" in text and "India." in text
        if deal.value:
            assert f"INR {deal.value:,.2f}" in text
        if not deal.doc_id.startswith("pf-"):
            assert deal.doc_id in keyed
    assert any(d.counterparty.startswith("M/s ") or d.company.startswith("M/s ") for d in indian)
    assert {d.city for d in indian} == {"Mumbai", "New Delhi", "Bengaluru"}


def test_the_long_documents_are_long_and_their_changes_sit_in_the_schedules(aegis):
    """AC-7: each long document is over 45,000 characters, ends with the SoW and DPA templates as
    schedules, and every one of its planted changes sits inside a schedule, past 25,000."""
    for doc_id in (f"ae-long-0{n}" for n in range(1, 5)):
        text = _text(aegis, doc_id)
        assert len(text) > 45_000
        one = text.index("SCHEDULE 1 - STATEMENT OF WORK")
        two = text.index("SCHEDULE 2 - DATA PROCESSING")
        assert c.CUTOFF < one < two
        item = next(i for i in aegis.review if i.doc_id == doc_id)
        assert all(r.planted.char_start > one for r in item.rules if r.planted)


def test_a_scanned_twin_is_page_images_whose_only_text_is_the_docusign_style_stamp(aegis):
    """AC-8 / FR-8: every page of a twin is an image; the one real text line per page is the stamp,
    so the app reads it like the stamp-only owner scans and the effect of the scan path shows."""
    import pymupdf

    assert [d.doc_id for d in aegis.documents if d.twin_of] == [
        "scan-ae-msa-02", "scan-ae-saas-02", "scan-ae-vendor-03", "scan-ae-long-01",
    ]
    for doc_id in ("scan-ae-msa-02", "scan-ae-long-01"):
        pdf = pymupdf.open(stream=aegis.texts[f"constructed/{doc_id}.pdf"], filetype="pdf")
        stamp = b.scan_stamp(doc_id[5:])
        assert stamp.startswith("DocuSign Envelope ID: ") and len(pdf) >= 8
        for page in pdf:
            assert len(page.get_images()) == 1 and page.get_text().strip() == stamp
    original = _text(aegis, "ae-msa-02")
    assert "Master Services Agreement" in original and "Master Services" not in pdf[0].get_text()


@needs_cp
def test_all_six_scanned_twins_are_images_with_only_the_stamp_as_text(full):
    """AC-8: the Common Paper and long twins too - every page an image, the only text the stamp."""
    import pymupdf

    by_id = {d.doc_id: d for d in full.documents}
    twins = [d for d in full.documents if d.twin_of]
    assert [d.twin_of for d in twins] == list(b.SCAN_TWINS)
    for twin in twins:
        pdf = pymupdf.open(stream=full.texts[f"constructed/{twin.doc_id}.pdf"], filetype="pdf")
        stamp = b.scan_stamp(twin.twin_of)
        assert all(len(p.get_images()) == 1 and p.get_text().strip() == stamp for p in pdf)
        assert twin.dataset == by_id[twin.twin_of].dataset  # a Common Paper twin keeps its licence


def test_a_scan_is_rebuilt_byte_for_byte_from_the_same_text():
    """FR-1: a scan that differed between two builds would change the document's hash, and with it
    the content fingerprint, for no reason the owner could see."""
    text = "Clause one. " * 400 + "\n\n" + "Clause two. " * 300
    stamp = b.scan_stamp("x")
    assert b.render_scan(text, stamp) == b.render_scan(text, stamp)
    assert b.render_scan(text, stamp) != b.render_scan(text + "!", stamp)
    assert b.scan_stamp("x") != b.scan_stamp("y") and b.scan_stamp("x") == stamp


def test_constructed_extraction_keys_state_only_what_the_text_states(aegis):
    """FR-5 / FR-23: each constructed contract's keys are normalised (parties as a list, ISO dates,
    the app's jurisdiction key, an amount) and each quote is in the contract - an auto-renewing
    contract is keyed by its renewal terms rather than an end date that is not its end."""
    deals = aegis.deals
    assert len(aegis.extraction) == 40 and {e.doc_id for e in aegis.extraction} == set(deals)
    for item in aegis.extraction:
        deal, text = deals[item.doc_id], _text(aegis, item.doc_id)
        fields = {f.field: f for f in item.fields}
        assert item.item_id == f"ex-{deal.doc_id}" and item.category == c.SourceCategory.CONSTRUCTED
        assert fields["parties"].value == [deal.company, deal.counterparty]
        assert fields["effective_date"].value == deal.effective.isoformat()
        assert fields["governing_law"].value == b.normalize_jurisdiction(deal.law)
        assert ("value" in fields) == bool(deal.value)
        assert ("expiration_date" in fields) == bool(deal.end and not deal.auto_renew)
        assert ("renewal_term" in fields) == deal.auto_renew == ("notice_period" in fields)
        for f in fields.values():
            assert f.quote in text, (item.doc_id, f.field)
            if f.kind == "date":
                assert re.fullmatch(r"\d{4}-\d\d-\d\d", f.value)


@needs_cp
def test_common_paper_extraction_keys_quote_the_text_and_follow_the_governing_law_plant(full):
    """FR-5 / FR-23: a Common Paper CSA whose governing-law clause was planted away from Delaware
    must be keyed with that law, not the template's, or the right answer would be scored wrong; the
    DPA has no governing-law key. Every quote is in the text it is scored against."""
    keys = {i.doc_id: i for i in full.extraction if i.doc_id.startswith("cp-")}
    assert len(keys) == 18
    planted = {
        i.doc_id for i in full.review for r in i.rules if r.rule == "governing_law" and r.planted
    }
    assert planted
    for doc_id, item in keys.items():
        fields = {f.field: f for f in item.fields}
        assert fields["parties"].value[0] == b.CP_PROVIDER
        assert all(f.quote in _text(full, doc_id) for f in item.fields), doc_id
        if doc_id.startswith("cp-dpa-"):
            assert "governing_law" not in fields
        else:
            expected = "cayman islands" if doc_id in planted else "delaware"
            assert fields["governing_law"].value == expected, doc_id


def test_portfolio_answers_are_computed_from_the_deals_and_proved_against_the_texts(aegis):
    """AC-9: the exact lists are the contracts whose own text states each fact - verified both ways
    on every contract - and a text that disagrees with the deals stops the build."""
    committed = _committed(_ITEMS / "portfolio.json", c.PortfolioQuestion)
    assert aegis.portfolio == committed
    texts = {i: _text(aegis, i) for i in aegis.deals}
    listed = aegis.portfolio[0].expected_doc_ids
    broken = dict(texts)
    broken[listed[0]] = texts[listed[0]].replace("renews automatically", "ends")
    with pytest.raises(ValueError, match="portfolio question 1"):
        b.portfolio_questions(aegis.deals, broken)
    unlisted = next(i for i in aegis.deals if i not in listed and aegis.deals[i].kind != "nda")
    broken = {**texts, unlisted: texts[unlisted] + " It renews automatically."}
    with pytest.raises(ValueError, match="portfolio question 1"):
        b.portfolio_questions(aegis.deals, broken)


_OLD_RENEWAL_PLANT = (
    "Each Subscription Term is extended for a further twelve (12) months unless the Customer "
    "gives written notice of non-renewal at least one hundred and eighty (180) days before the "
    "end of the then-current Subscription Term."
)


def test_no_planted_wording_makes_a_contract_renew_and_the_proof_would_catch_it(aegis):
    """AC-9 regression: the SaaS `renewal` plant once rewrote the opt-in clause as 'extended for a
    further twelve months unless ... 180 days' notice' in ae-saas-03, which the deals list as
    fixed-term. A reader who rightly named it for 'renews automatically' and 'at least 90 days'
    notice' was marked wrong, and the proof missed it because it matched one literal phrase. Now
    no plant can say it, the clause stays opt-in, and the proof reads every wording."""
    plants = [p for book in b.AEGIS_CATALOGUE.values() for p in book]
    plants += [p for _, _, long_plants in b.LONG_DOCS for p in long_plants]
    assert not [p.rule for p in plants if p.replace and b._says_auto_renewal(p.replace)]
    assert not [p.rule for p in plants if p.replace and b._says_notice_of_90_days_or_more(p.replace)]
    fixed = "ae-saas-03"
    text = _text(aegis, fixed)
    assert not aegis.deals[fixed].auto_renew
    assert "A Subscription Term renews only where the Order Form provides for renewal" in text
    assert "is not required to send the Customer a renewal reminder" in text

    assert b._says_auto_renewal(_OLD_RENEWAL_PLANT)
    assert b._says_notice_of_90_days_or_more(_OLD_RENEWAL_PLANT)
    assert not b._says_notice_of_90_days_or_more(
        "gives 60 days' written notice of non-renewal; notice of non-renewal at least sixty (60) days"
    )
    texts = {i: _text(aegis, i) for i in aegis.deals}
    broken = {**texts, fixed: text + "\n\n" + _OLD_RENEWAL_PLANT}
    with pytest.raises(ValueError, match="portfolio question 1"):
        b.portfolio_questions(aegis.deals, broken)
    only_notice = "The Customer gives written notice of non-renewal at least one hundred (100) days"
    with pytest.raises(ValueError, match="portfolio question 6"):
        b.portfolio_questions(aegis.deals, {**texts, fixed: text + "\n\n" + only_notice})
    listed = set(aegis.portfolio[0].expected_doc_ids)
    assert {i for i, t in texts.items() if b._says_auto_renewal(t)} == listed


def test_portfolio_facts_are_spread_so_no_question_is_trivial(aegis):
    """AC-9: a question everyone passes (or nobody can) measures nothing: lists are small, not
    empty, never the whole portfolio, and ambiguous facts are avoided - an auto-renewing contract is
    never counted as 'expiring in 2027'."""
    deals = aegis.deals.values()
    assert sum(d.auto_renew for d in deals) == b.N_AUTO_RENEWING
    assert all(not (d.auto_renew and d.end and d.end.year == 2027) for d in deals)
    assert sum(bool(d.value and d.value > b.ONE_CRORE) for d in deals) == b.N_BIG_VALUE
    assert all(1 <= len(q.expected_doc_ids) <= 10 for q in aegis.portfolio[:8])
    assert sorted(d.counterparty for d in deals if d.counterparty == b.KESTREL) == [b.KESTREL] * 3
    assert len({d.counterparty for d in deals}) == 38


def test_the_last_portfolio_question_has_a_list_longer_than_one_search_returns_and_the_sample_ignores_it(
    aegis,
):
    """The failure Step 3e fixes was a list answered from the 8 best passages: it named 8 of 20 and
    said 'high confidence'. None of the first eight questions has more than 8 matches, so none could
    show it. pq-09 (Indian law, 33 of 40) can -- checked both ways against the texts like the rest
    -- and it comes LAST so the fixed sample, which takes the first two questions a six-contract
    sub-portfolio answers in part, still asks pq-01 and pq-02 and no baseline question moves."""
    last = aegis.portfolio[-1]
    assert (last.item_id, last.fact) == ("pq-09", "governing_law")
    assert len(last.expected_doc_ids) == sum(d.law == "India" for d in aegis.deals.values()) == 33
    assert len(last.expected_doc_ids) > 8 >= max(len(q.expected_doc_ids) for q in aegis.portfolio[:8])
    assert all("laws of India" in _text(aegis, i) for i in last.expected_doc_ids)
    others = [i for i in aegis.deals if i not in last.expected_doc_ids]
    assert not any("laws of India" in _text(aegis, i) for i in others)
    sample = json.loads((c.CONTENT_DIR / "sample.json").read_text(encoding="utf-8"))
    assert sample["portfolio"] == ["pq-01", "pq-02"]


def test_the_constructed_build_is_deterministic(aegis, tmp_path):
    """FR-1: two builds give identical files - texts, scans, answer records, questions - or two
    baseline runs would be asked different questions."""
    again = b.build_constructed(tmp_path)
    assert again.texts == aegis.texts
    assert again.review == aegis.review and again.portfolio == aegis.portfolio
    assert again.documents == aegis.documents and again.report == aegis.report


def test_a_rule_with_no_severity_gets_no_plant_and_the_build_says_so(tmp_path):
    """Spec edge case / FR-5: if the platform rule has no severity, nothing is planted for it and a
    severity is never made up. The rule stays in the key as compliant (the clause is still there),
    the build names it, and every other rule is still planted."""
    library = copy.deepcopy(PLAYBOOK_LIBRARY)
    rule = _library_rule("Master Services Agreement Playbook", "payment_terms")
    next(
        r for p in library if p["name"] == "Master Services Agreement Playbook"
        for r in p["rules"] if r["clause_type"] == rule["clause_type"]
    )["risk_level"] = None
    built = b.build_constructed(tmp_path, library=library)
    skipped = built.report["rules_without_severity"]
    assert "Master Services Agreement Playbook / payment_terms" in skipped
    assert "Master Services Agreement Playbook / payment_terms (in ae-long-01)" in skipped
    long_one = next(i for i in built.review if i.doc_id == "ae-long-01")
    planted = [r.rule for r in long_one.rules if r.planted]
    assert planted == ["limitation_of_liability", "data_protection"]
    assert "NO CHANGE PLANTED" in b.format_constructed_report(built)
    msa = [i for i in built.review if i.playbook == "Master Services Agreement Playbook"]
    assert msa and not any(r.planted for i in msa for r in i.rules if r.rule == "payment_terms")
    assert all(r.status == "compliant" for i in msa for r in i.rules if r.rule == "payment_terms")
    assert all(sum(r.planted is not None for r in i.rules) >= 2 for i in msa if not i.control)
    saas = [i for i in built.review if i.playbook == "Software / SaaS Agreement Playbook"]
    assert any(r.planted for i in saas for r in i.rules if r.rule == "payment_terms")


def test_a_missing_common_paper_download_leaves_those_documents_out_and_says_so(aegis):
    """Spec edge case: a dataset that is not there is reported missing, never substituted - no
    Common Paper document, twin or playbook is built, and everything else still is."""
    assert aegis.report["missing"] == ["commonpaper"]
    assert aegis.playbooks == {}
    assert not any(d.doc_id.startswith(("cp-", "scan-cp-")) for d in aegis.documents)
    assert "MISSING datasets" in b.format_constructed_report(aegis)
    assert len(aegis.review) == 28 and len(aegis.documents) == 28 + 12 + 4


def test_a_common_paper_file_that_no_longer_has_a_planted_text_stops_the_build(tmp_path):
    """FR-1: the plants name the exact words they change. An upstream edit that removes them must
    fail loudly, not plant nothing and leave a key describing changes that are not there."""
    for name in ("CSA.md", "DPA.md"):
        (tmp_path / "commonpaper").mkdir(exist_ok=True)
        text = "# Agreement\n\n1. Nothing useful.\n"
        (tmp_path / "commonpaper" / name).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="occurs 0 times"):
        b.build_constructed(tmp_path)


def test_the_constructed_keys_are_written_after_the_public_ones_in_one_extraction_file(
    aegis, tmp_path
):
    """FR-5 / FR-23: the constructed contracts' keys must be in the committed file, or every
    constructed contract gets a paid metadata call with nothing to score and the scanned twins have
    no original key to be compared with. The public keys keep their place at the front, and the file
    loads back through the frozen model unchanged."""
    first = c.ExtractionItem(
        item_id="ex-cuad-001", doc_id="cuad-001", category=c.SourceCategory.PUBLIC,
        source="CUAD", fields=[],
    )
    public = b.PublicBuild(datasets=[], extraction=[first])
    written = b.write_constructed(
        public, aegis, content_dir=tmp_path / "content", docs_dir=tmp_path / "docs"
    )
    path = tmp_path / "content" / "items" / "extraction.json"
    assert path in written
    items = TypeAdapter(list[c.ExtractionItem]).validate_python(json.loads(path.read_text()))
    assert items == [first, *aegis.extraction]
    assert len({i.item_id for i in items}) == len(items) == 41


def test_a_missing_public_set_leaves_its_tasks_out_of_the_sample_instead_of_stopping_the_build(
    aegis,
):
    """Spec edge case: a dataset that was not downloaded leaves its set out (and named in the
    build's `missing`). The sample must do the same; it used to raise, so one missing public set
    kept the constructed review, portfolio and sample files from being written at all."""
    sample = b.make_sample(b.PublicBuild(datasets=[]), aegis)
    assert sample["extraction"] == ["ex-ae-msa-01", "ex-ae-msa-02", "ex-ae-long-01"]
    assert sample["qa"] == []
    assert sample["review"] == ["rv-ae-msa-01", "rv-ae-msa-02", "rv-scan-ae-msa-02", "rv-ae-long-01"]
    assert sample["portfolio"] and sample["routing"] and sample["portfolio_docs"]


def test_a_run_without_the_public_downloads_still_writes_the_constructed_sets_and_says_so(
    tmp_path, capsys
):
    """FR-4 / spec edge case: `build` with the public datasets missing names them, leaves their
    documents and items out, and still writes everything that needs no download."""
    run = b.run_constructed(
        tmp_path / "no-raw", download=False, content_dir=tmp_path / "content",
        docs_dir=tmp_path / "docs",
    )
    out = capsys.readouterr().out
    assert "MISSING public datasets" in out and "cuad" in out
    sample = json.loads((tmp_path / "content" / "sample.json").read_text(encoding="utf-8"))
    assert sample["qa"] == [] and "rv-ae-msa-02" in sample["review"]
    assert "ex-ae-msa-02" in sample["extraction"]
    documents = json.loads((tmp_path / "content" / "documents.json").read_text(encoding="utf-8"))
    assert {d["doc_id"] for d in documents} == {d.doc_id for d in run.documents}


def test_the_builder_imports_nothing_that_can_reach_a_model():
    """AC-3 / FR-3: no answer key may come from the model under test. The builder's own imports hold
    no AI client or gateway; the app modules it borrows (the template filler, the playbook library)
    are read, not called to ask a model."""
    import ast

    tree = ast.parse((BACKEND_DIR / "eval" / "exam" / "build.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    forbidden = (
        "anthropic", "httpx", "app.ai", "app.integrations", "app.core.database", "sqlalchemy",
    )
    assert not [m for m in imported if m and m.startswith(forbidden)]


@needs_everything
def test_committed_constructed_content_is_exactly_what_the_build_produces(tmp_path):
    """FR-1: the committed files, and the contract files on disk, must be reproducible from the
    downloads and the repository. A hand edit, a template change or a non-deterministic step shows
    up here - not later as a baseline that cannot be reproduced. (A different PyMuPDF writes
    different scan bytes: the manifest records the version, and the comparison is skipped then.)"""
    import pymupdf

    committed = json.loads((c.CONTENT_DIR / "manifest.json").read_text(encoding="utf-8"))
    if committed["pymupdf"] != pymupdf.__version__:
        pytest.skip(f"scanned twins were built with PyMuPDF {committed['pymupdf']}")
    public, built = b.build_public(), b.build_constructed()
    assert built.report["missing"] == [] and built.report["rules_without_severity"] == []
    b.write_constructed(public, built, content_dir=tmp_path / "content", docs_dir=tmp_path / "docs")
    for rel in ("documents.json", "items/review.json", "items/extraction.json",
                "items/portfolio.json", "sample.json", "manifest.json", "playbooks/cp_msa.json",
                "playbooks/cp_dpa.json"):
        assert (tmp_path / "content" / rel).read_bytes() == (c.CONTENT_DIR / rel).read_bytes(), rel
    for doc in built.documents:
        written = (tmp_path / "docs" / doc.file).read_bytes()
        assert hashlib.sha256(written).hexdigest() == doc.file_sha256
    assert built.report["plants"]["total"] == 96 and built.report["plants"]["past_25k"] >= 10
    assert sorted(built.report["plants"]["by_severity"]) == ["critical", "high", "low", "medium"]


@pytest.mark.skipif(
    not _DOCS_ON_DISK.is_dir(), reason="contract files not generated (eval/contracts)"
)
def test_the_contract_files_on_disk_are_the_ones_the_committed_hashes_name():
    """FR-1 / FR-20: documents.json pins each constructed contract by hash; a file on disk that
    drifted from it would be a different exam under the same fingerprint."""
    for doc in (d for d in _ALL_DOCS if d.category == c.SourceCategory.CONSTRUCTED):
        path = c.DOCS_DIR / doc.file
        assert path.is_file(), doc.doc_id
        assert hashlib.sha256(path.read_bytes()).hexdigest() == doc.file_sha256, doc.doc_id


@needs_everything
def test_run_constructed_writes_the_files_and_prints_the_counts(tmp_path, capsys):
    """`build --only constructed` prints the per-set counts, the plants by severity and anything
    missing or unplanted (FR-5, FR-7), and writes the constructed files and the three files that
    merge them with the public rows - leaving the purely public files as they are."""
    built = b.run_constructed(
        c.RAW_DIR, download=False, content_dir=tmp_path / "content", docs_dir=tmp_path / "docs"
    )
    out = capsys.readouterr().out
    assert "constructed sets built" in out and '"past_25k": 12' in out and "by_severity" in out
    assert "MISSING" not in out and "NO CHANGE PLANTED" not in out
    root = tmp_path / "content"
    written = {p.relative_to(root).as_posix() for p in root.rglob("*.json")}
    assert written == {
        "documents.json", "items/review.json", "items/extraction.json", "items/portfolio.json",
        "sample.json", "manifest.json", "playbooks/cp_msa.json", "playbooks/cp_dpa.json",
    }
    assert len(list((tmp_path / "docs" / "constructed").iterdir())) == len(built.texts) == 64


@needs_everything
def test_a_full_build_refreshes_every_content_file_and_prints_one_consistent_report(
    tmp_path, capsys
):
    """FR-4 / FR-5: the full `build` writes the public files too and prints the public and the
    constructed counts as one report. It used to print the public step's "documents.json and
    items/review.json were not written" line, which was false by the end of the build, and
    `--only constructed` could leave the public files out of step with the merged ones."""
    b.run_constructed(
        c.RAW_DIR, download=False, content_dir=tmp_path / "content", docs_dir=tmp_path / "docs",
        with_public=True,
    )
    out = capsys.readouterr().out
    assert "public sets built: contractnli, cuad, qa" in out and "constructed sets built" in out
    assert "not written" not in out and "MISSING" not in out
    root = tmp_path / "content"
    written = {p.relative_to(root).as_posix() for p in root.rglob("*.json")}
    assert written == {
        "datasets.json", "documents.json", "sample.json", "manifest.json",
        "items/review.json", "items/extraction.json", "items/qa.json", "items/portfolio.json",
        "playbooks/cuad_checklist.json", "playbooks/nda_checklist.json",
        "playbooks/cp_msa.json", "playbooks/cp_dpa.json",
    }
    for rel in written:
        assert (root / rel).read_bytes() == (c.CONTENT_DIR / rel).read_bytes(), rel
