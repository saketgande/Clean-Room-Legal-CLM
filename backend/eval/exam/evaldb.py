"""The exam's throwaway database: rebuild it, seed it, and read documents into it.

specs/005-ai-eval-baseline/plan.md, "Component design" -> ``evaldb.py`` (T017; FR-5, FR-24,
FR-37, FR-38). Every run starts from nothing: ``reset`` drops and recreates ``legal_clm_eval``,
``migrate`` builds the schema with the app's own migrations, ``bootstrap`` creates the exam's
organisation and its two users, ``load_playbooks`` / ``load_workflows`` seed what the exam
reviews and routes against, and ``ingest_document`` reads one document in.

``ingest_document`` runs the upload pipeline's own read and persist steps
(``ContractFilesService._resolve_extracted_text`` through ``read_as_app``,
``_persist_intake_records``) and the embedding job's handler function, in this process. It
deliberately stops short of everything ``create_contract_from_upload`` does afterwards -- no
Celery dispatch, no stage-entry trigger (those start jobs and advance workflows), no
notification, no workflow run (FR-38). The contract lands at the Review stage exactly as an
upload does, set directly.

Every function that writes refuses a session that is not on an exam database, so a mistake in
a caller can never put an exam contract into the real development data (FR-37, FR-38).

Import this module only after ``eval.exam.env.isolate()``: it reaches the app's models, which
import the database engine.
"""

import asyncio
import hashlib
import re
import secrets
import shutil
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from alembic import command
from app.ai.embeddings import generate_embeddings_for_snapshot
from app.auth.models import Role, User
from app.auth.schemas import SetupAdminRequest
from app.auth.service import create_first_admin
from app.contract_files.service import ContractFilesService, _ExtractedText
from app.core.config import settings
from app.core.database import utcnow
from app.core.enums import ContractLifecycleStage, PlaybookStatus, UserStatus
from app.core.rbac import LEGAL_REVIEWER_ROLE_NAME
from app.core.security import hash_password
from app.integrations.ocr import page_map_from_elements
from app.menu_security.bootstrap import ensure_org_access
from app.playbooks.library import PLAYBOOK_LIBRARY, seed_playbook_library
from app.playbooks.models import Playbook, PlaybookRule, PlaybookVersion
from app.workflows.models import Workflow
from eval.exam.content import (
    APP_DIR,
    DOCS_DIR,
    PRIVATE_DIR,
    WORK_DIR,
    ExamContent,
    ExamDocument,
    SourceCategory,
    owner_file_path,
)
from eval.exam.env import EVAL_DB_NAME, ExamRefused, LiveDB

if TYPE_CHECKING:
    from eval.exam.owner import FrozenOCR

RUNNER_EMAIL = "exam-runner@example.com"
PORTFOLIO_EMAIL = "exam-portfolio@example.com"

# Where the owner's 14 files are kept by `owner-import` (the layout ExamDocument.file names).
_OWNER_FILES_DIR = PRIVATE_DIR / "owner" / "files"

# A database name goes into DROP/CREATE DATABASE, which cannot take a bound parameter: only
# plain lower-case identifiers that start with the exam prefix are ever put in the statement.
_EXAM_DB_NAME = re.compile(rf"{EVAL_DB_NAME}[a-z0-9_]*")

# An exam playbook's rule names the library rule its severity was copied from at build time.
# That is provenance for the build and its tests, not a column of the rule.
_RULE_PROVENANCE_KEY = "severity_from"


class IngestFailed(Exception):
    """One document could not be read or stored. The runner records it and its items fail; the
    rest of the run goes on (FR-40). ``reason`` is plain text for the run record."""

    def __init__(self, doc_id: str, reason: str):
        super().__init__(f"{doc_id}: {reason}")
        self.doc_id = doc_id
        self.reason = reason


@dataclass(frozen=True)
class ExamActors:
    org_id: str
    runner: User  # an admin: uploads and reviews everything, like the owner of a real portfolio
    portfolio: User  # a member: sees only what it uploaded, so portfolio scope is the app's own


@dataclass(frozen=True)
class IngestedDoc:
    doc_id: str
    contract_id: str
    version_id: str
    snapshot_id: str
    text_len: int
    method: str
    elements: int
    # Beyond the frozen list (additive, defaulted).
    # sha256 of the text the app read; for an owner document this is what keys must match.
    text_sha256: str = ""
    # Owner main documents only: does that text equal the frozen text the owner's answers were
    # confirmed against? False means the keys of this document are out of date (FR-20). None
    # for every other document, and for the "as read today" twin (its text differs on purpose).
    frozen_text_matches: bool | None = None


# --- guards -----------------------------------------------------------------------------


def _check_name(live: LiveDB, eval_db_name: str) -> None:
    if not _EXAM_DB_NAME.fullmatch(eval_db_name):
        raise ExamRefused(
            f"'{eval_db_name}' is not an exam database name: it must start with "
            f"'{EVAL_DB_NAME}' and use only lower-case letters, digits and underscores"
        )
    if eval_db_name == make_url(live.url).database:
        raise ExamRefused("the exam database must not be the live database")


def _require_exam_db(db: Session) -> None:
    """Refuse a session that is not on an exam database (FR-37, FR-38)."""
    name = db.get_bind().engine.url.database or ""
    if not name.startswith(EVAL_DB_NAME):
        raise ExamRefused(
            f"refusing to write to database '{name}': the exam only writes to '{EVAL_DB_NAME}*'"
        )


def _require_exam_storage(storage) -> None:
    """Exam contract bytes may only be stored under ``eval/.work`` (git- and docker-ignored)."""
    root = getattr(storage, "root", None)
    if root is None or not Path(root).resolve().is_relative_to(WORK_DIR.resolve()):
        raise ExamRefused(
            "exam contracts may only be stored under backend/eval/.work; run the exam through "
            "`python -m eval.exam` so the storage location is set"
        )


# --- the throwaway database -------------------------------------------------------------


def reset(live: LiveDB, eval_db_name: str) -> None:
    """Drop and recreate the exam database (and forget the previous run's stored files).

    Refuses (``ExamRefused``) any name that does not start with ``legal_clm_eval`` and the live
    database itself. Runs on a connection to the live server, outside any transaction (Postgres
    cannot create or drop a database inside one). ``FORCE`` ends any session still open on the
    old database, such as this process's own pooled connections from the previous run.
    """
    _check_name(live, eval_db_name)
    database = sys.modules.get("app.core.database")
    if database is not None and database.engine.url.database == eval_db_name:
        database.engine.dispose()
    server = create_engine(live.url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        with server.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{eval_db_name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{eval_db_name}"'))
    except SQLAlchemyError as exc:
        # The server's own one-line reason ("permission denied to create database", ...).
        reason = (str(getattr(exc, "orig", None) or exc).splitlines() or [type(exc).__name__])[0]
        raise ExamRefused(f"could not rebuild the exam database: {reason}") from exc
    finally:
        server.dispose()
    # The previous run's contract bytes belong to a database that no longer exists. Only the
    # exam's own folder is ever cleared: a storage root anywhere else is left alone.
    root = Path(settings.storage_root).resolve()
    if root == (WORK_DIR / "storage").resolve():
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)


def migrate() -> None:
    """``alembic upgrade head`` on the exam database: the app's own migrations, nothing else.

    ``alembic/env.py`` reads ``settings.database_url``, which ``isolate()`` has already pointed
    at the exam database; refused unless it is one. Built without ``alembic.ini`` on purpose:
    ``env.py`` would otherwise call ``logging.config.fileConfig``, which switches off every
    logger the app has already created and would hide the app's own warnings from the run.
    """
    name = make_url(settings.database_url).database or ""
    if not name.startswith(EVAL_DB_NAME):
        raise ExamRefused(f"refusing to migrate '{name}': it is not an exam database")
    config = Config()
    config.set_main_option("script_location", str(APP_DIR.parent / "alembic"))
    command.upgrade(config, "head")


def bootstrap(db: Session) -> ExamActors:
    """The exam's organisation and its two users, made the way the app makes its first admin.

    ``create_first_admin`` (organisation, roles, default teams, the admin's role grant and every
    role's screen access) gives the runner, an admin. The portfolio user is a legal reviewer, not an
    admin: admins see every contract, so the Contract Brain's portfolio scope returns exactly what
    it uploaded only for a non-admin, and the app's real access code does that scoping (FR-9). A
    plain member would not do: a "which contracts" question can need the AI to read each contract,
    and that takes the AI tools permission a member lacks (contract_brain/portfolio.py).
    Neither user can be logged in as: their passwords are random and thrown away; the exam calls
    the app's functions with the user object.
    """
    _require_exam_db(db)
    runner = create_first_admin(
        db,
        SetupAdminRequest(
            setup_token=settings.setup_token,
            organization_name="AI exam",
            organization_slug="ai-exam",
            email=RUNNER_EMAIL,
            full_name="Exam runner",
            password=secrets.token_urlsafe(24),
        ),
    )
    reviewer = db.scalar(
        select(Role).where(Role.org_id == runner.org_id, Role.name == LEGAL_REVIEWER_ROLE_NAME)
    )
    portfolio = User(
        org_id=runner.org_id,
        email=PORTFOLIO_EMAIL,
        full_name="Exam portfolio user",
        hashed_password=hash_password(secrets.token_urlsafe(24)),
        status=UserStatus.ACTIVE,
        active_role_id=reviewer.id,
        created_by_user_id=runner.id,
        updated_by_user_id=runner.id,
    )
    db.add(portfolio)
    db.flush()
    # `User.roles` is read-only; the role grant is what gives the user its role. This is the
    # app's own repair helper: it adds the missing grant and leaves what exists alone.
    ensure_org_access(db, org_id=runner.org_id, actor_user_id=runner.id)
    db.commit()
    for user in (runner, portfolio):
        db.refresh(user)
        # Reading the permissions loads them while the session is open (the access code reads
        # them later, possibly in another session); none at all means the role grant is missing.
        if not user.permission_values:
            raise ExamRefused(f"the exam user {user.email} was created without a role")
    # Detached with everything loaded: the actors are handed to every unit of the run, each in
    # its own session, and a rollback in this one would otherwise expire them for good.
    db.expunge_all()
    return ExamActors(org_id=runner.org_id, runner=runner, portfolio=portfolio)


def load_playbooks(db: Session, content: ExamContent, actors: ExamActors) -> dict[str, str]:
    """The platform's playbook library plus the exam's own playbooks, all published.

    Returns ``{key: playbook_id}``: an exam playbook by its key (``cp_msa``, ``nda_checklist``,
    ...) and a library playbook by its name (``"Master Services Agreement Playbook"``), the two
    ways a review item names its playbook. The library is seeded by the app's own function.

    The review prompt lists rules in ``created_at`` order, so an exam playbook's rules are given
    explicit, increasing timestamps: the same rule order, hence the same prompt, every run.

    ponytail: library rules keep the order ``seed_playbook_library`` inserts them in; that rests
    on consecutive Python timestamps differing, which holds for the microseconds an insert
    takes. Re-stamp them from ``PLAYBOOK_LIBRARY`` order if two ever tie.
    """
    _require_exam_db(db)
    seed_playbook_library(db, org_id=actors.org_id, actor_id=actors.runner.id)
    db.flush()
    ids: dict[str, str] = {}
    library_names = [spec["name"] for spec in PLAYBOOK_LIBRARY]
    for playbook in db.scalars(
        select(Playbook).where(
            Playbook.org_id == actors.org_id,
            Playbook.name.in_(library_names),
            Playbook.deleted_at.is_(None),
        )
    ):
        ids[playbook.name] = playbook.id

    start = utcnow()
    for key in sorted(content.playbooks):
        spec = content.playbooks[key]
        playbook = Playbook(
            org_id=actors.org_id,
            name=spec["name"],
            description=spec.get("description"),
            status=PlaybookStatus.PUBLISHED,
            created_by_user_id=actors.runner.id,
            updated_by_user_id=actors.runner.id,
        )
        db.add(playbook)
        db.flush()
        version = PlaybookVersion(
            org_id=actors.org_id,
            playbook_id=playbook.id,
            version_number=1,
            status=PlaybookStatus.PUBLISHED,
            summary="Exam playbook",
            source_metadata={"exam": True, "key": key, "source": spec.get("source")},
            created_by_user_id=actors.runner.id,
            updated_by_user_id=actors.runner.id,
        )
        db.add(version)
        db.flush()
        playbook.current_version_id = version.id
        for index, rule in enumerate(spec["rules"]):
            db.add(
                PlaybookRule(
                    org_id=actors.org_id,
                    playbook_version_id=version.id,
                    created_by_user_id=actors.runner.id,
                    updated_by_user_id=actors.runner.id,
                    created_at=start + timedelta(milliseconds=index),
                    **{k: v for k, v in rule.items() if k != _RULE_PROVENANCE_KEY},
                )
            )
        ids[key] = playbook.id
    db.commit()
    return ids


def load_workflows(db: Session, snapshot: dict, actors: ExamActors) -> None:
    """Copy the routing snapshot's workflows into the exam database, ids included.

    The owner's routing picks name these ids, and the intake triage chooses among exactly the
    enabled workflows it finds here. They are only ever read: a workflow is never started.
    The loaded rows must reproduce the snapshot's fingerprint, or the exam would route against a
    list the owner never saw.
    """
    from eval.exam.routing import workflow_fingerprint  # imports runner, which imports this

    _require_exam_db(db)
    start = utcnow()
    ordered = sorted(snapshot["workflows"], key=lambda w: (w["eval_order"], w["name"], w["id"]))
    for index, w in enumerate(ordered):
        db.add(
            Workflow(
                id=w["id"],
                org_id=actors.org_id,
                name=w["name"],
                description=w["description"],
                enabled=w["enabled"],
                is_builtin=w["is_builtin"],
                eval_order=w["eval_order"],
                version=w["version"],
                criteria=w["criteria"],
                steps=w["steps"],
                created_by_user_id=actors.runner.id,
                updated_by_user_id=actors.runner.id,
                created_at=start + timedelta(milliseconds=index),
            )
        )
    db.flush()
    loaded = [
        {
            "id": w.id,
            "name": w.name,
            "enabled": bool(w.enabled),
            "version": w.version,
            "criteria": w.criteria or {},
            "steps": w.steps or [],
        }
        for w in db.scalars(select(Workflow).where(Workflow.org_id == actors.org_id))
    ]
    if workflow_fingerprint(loaded) != snapshot["fingerprint"]:
        db.rollback()
        raise ExamRefused(
            "the workflows loaded into the exam database do not match the routing snapshot; "
            "take the snapshot again (draft-routing)"
        )
    db.commit()


# --- reading a document in --------------------------------------------------------------


def _read_document(doc: ExamDocument) -> bytes:
    """The document's bytes, proved to be the file the exam recorded.

    The record's sha256 is part of the content fingerprint, so bytes that differ are not the
    exam's content: a regenerated or edited file fails loudly instead of being scored under a
    fingerprint it does not belong to. An owner file is opened only through ``owner_file_path``,
    which refuses any name outside the 14 before touching the disk (FR-15).
    """
    if doc.category is SourceCategory.OWNER:
        try:
            path = owner_file_path(_OWNER_FILES_DIR, doc.file)
        except FileNotFoundError:
            raise IngestFailed(
                doc.doc_id, "the contract file is not in eval/private/owner; run `owner-import`"
            ) from None
    else:
        path = (DOCS_DIR / doc.file).resolve()
        if not path.is_relative_to(DOCS_DIR.resolve()):
            raise IngestFailed(doc.doc_id, "the document is outside the exam's contracts folder")
        if not path.is_file():
            raise IngestFailed(doc.doc_id, "the document file is missing; run `build`")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != doc.file_sha256:
        raise IngestFailed(
            doc.doc_id, "the file is not the one the exam recorded (its sha256 differs)"
        )
    return data


async def _forced_ocr_text(
    ocr: "FrozenOCR", *, filename: str, mime_type: str, content: bytes
) -> _ExtractedText:
    """A stamp-only scan's text, from the frozen OCR cache, built the way the app builds the
    text of an OCR'd upload (``_resolve_extracted_text``'s OCR branch), page map included."""
    result = await ocr.extract_text(filename=filename, mime_type=mime_type, content=content)
    return _ExtractedText(
        method=f"{result.provider}_ocr",
        text=result.text,
        quality_score=result.quality_score,
        page_map=page_map_from_elements(result.text, result.elements),
        ocr_provider=result.provider,
        elements=result.elements,
    )


def _persist_and_embed(
    db: Session,
    service: ContractFilesService,
    *,
    uploader: User,
    stored,
    doc: ExamDocument,
    extracted: _ExtractedText,
) -> tuple[str, str, str, int]:
    """Rows, snapshot, document elements, then the embedding job's own function.

    One transaction: a document that cannot be embedded leaves no contract behind, so a failed
    document can never turn up in a portfolio answer. The flush matters as much as the commit
    it replaces: the session does not autoflush, and without it the embedding step would not
    see the document elements just written and would cut fixed windows instead of the
    clause-aligned chunks the app retrieves.
    """
    _, contract, _, version, snapshot = service._persist_intake_records(
        user=uploader,
        stored=stored,
        mime_type=doc.mime_type,
        title=doc.title,
        counterparty_name=None,
        contract_type=None,
        extracted=extracted,
    )
    # The outcome of `_advance_upload_to_review` without `transition_contract_stage`, whose
    # stage-entry triggers dispatch jobs and advance workflows (FR-38).
    contract.lifecycle_stage = ContractLifecycleStage.REVIEW
    db.flush()
    generate_embeddings_for_snapshot(db, snapshot=snapshot, created_by_user_id=uploader.id)
    ids = (contract.id, version.id, snapshot.id, snapshot.element_count or 0)
    db.commit()
    return ids


async def ingest_document(
    db: Session, doc: ExamDocument, *, actors: ExamActors, ocr: "FrozenOCR"
) -> IngestedDoc:
    """Read one exam document into the exam database exactly as an upload is read and stored.

    Store the bytes, read the text with the app's own reader (``read_as_app``, OCR only ever from
    the frozen cache), write the contract / file / version / snapshot / element rows, set the
    contract to Review, embed it. A stamp-only owner scan (``text_mode="forced_ocr"``) is read
    from the frozen OCR text instead. Any failure removes the stored bytes and the rows and
    raises ``IngestFailed``; the caller's ``ocr`` must be a ``FrozenOCR`` without a ``fill``, so
    reading can never pay for OCR.

    The brain ingestion step (the knowledge graph) is not here: it needs the metadata and clause
    answers, so the runner calls ``ingest_contract_brain`` between its two phases.
    """
    # Both import runner, which imports this module.
    from eval.exam.owner import read_as_app, read_manifest

    _require_exam_db(db)
    content = _read_document(doc)
    uploader = actors.portfolio if doc.uploader == "portfolio" else actors.runner
    service = ContractFilesService(db, reducto=ocr)
    _require_exam_storage(service.storage)
    stored = await asyncio.to_thread(
        service.storage.save_bytes,
        org_id=actors.org_id,
        filename=Path(doc.file).name,
        mime_type=doc.mime_type,
        content=content,
    )
    try:
        if doc.text_mode == "forced_ocr":
            extracted = await _forced_ocr_text(
                ocr, filename=stored.filename, mime_type=doc.mime_type, content=content
            )
        else:
            extracted = await read_as_app(
                content, mime_type=doc.mime_type, filename=stored.filename, ocr=ocr
            )
        if extracted.method.endswith("_ocr_failed"):
            # The app's own path swallows an OCR failure and keeps the thin native text. The
            # exam must not score that as a read: here it means the OCR is not frozen.
            raise IngestFailed(
                doc.doc_id,
                "the app wanted OCR for this file and none is frozen; run `ocr` first "
                f"({extracted.ocr_error})",
            )
        if not extracted.text.strip():
            raise IngestFailed(doc.doc_id, "no text could be read from the file")
        contract_id, version_id, snapshot_id, elements = await asyncio.to_thread(
            _persist_and_embed,
            db,
            service,
            uploader=uploader,
            stored=stored,
            doc=doc,
            extracted=extracted,
        )
    except Exception as exc:
        # Once bytes are on disk any failure must delete them (the upload pipeline's rule).
        db.rollback()
        service.storage.delete_bytes_permanently(stored.storage_key)
        if isinstance(exc, IngestFailed):
            raise
        raise IngestFailed(doc.doc_id, f"{type(exc).__name__}: {exc}") from exc

    text_sha256 = hashlib.sha256(extracted.text.encode("utf-8")).hexdigest()
    frozen_match = None
    if doc.category is SourceCategory.OWNER and doc.as_read_today_of is None:
        entry = next((e for e in read_manifest() if e.doc_id == doc.doc_id), None)
        frozen_match = entry is not None and entry.frozen_text_sha256 == text_sha256
    return IngestedDoc(
        doc_id=doc.doc_id,
        contract_id=contract_id,
        version_id=version_id,
        snapshot_id=snapshot_id,
        text_len=len(extracted.text),
        method=extracted.method,
        elements=elements,
        text_sha256=text_sha256,
        frozen_text_matches=frozen_match,
    )
