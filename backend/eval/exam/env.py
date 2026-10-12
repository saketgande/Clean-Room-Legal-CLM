"""The exam's process guard: isolate the process, prove the owner, audit, lock.

Everything the exam does that could touch live data or spend money starts here
(specs/005-ai-eval-baseline/plan.md, "Component design" -> ``env.py``).

``isolate()`` must run BEFORE anything imports ``app.core.database``: that module builds the
default engine from ``settings.database_url`` at import time, so repointing the settings first
is what makes "the app's own code, run against a throwaway database" true instead of a hope.
That is why this module imports only the standard library, pydantic-settings, SQLAlchemy,
``eval.exam.content`` and ``app.core.config`` at module level -- never ``app.core.database``,
``app.models`` or anything that reaches them.

The exam's own values (its Anthropic key, the owner's email, the OCR price) are NOT app
settings: they live in the git- and docker-ignored ``eval/private/exam.env``, read only here,
so no API, worker or beat process can ever see the exam key (plan decision 20).
"""

import importlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import NoReturn

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from eval.exam.content import PRIVATE_DIR, WORK_DIR

EVAL_DB_NAME = "legal_clm_eval"

# Where the exam may run at all. Anything else (staging, production, a typo) is refused: the
# exam rebuilds a database and spends money, and neither belongs on a deployed environment.
_LOCAL_ENVIRONMENTS = frozenset({"local", "development", "dev", "test"})

# Distinct from the audit-chain key (app/core/audit.py, "audit_lo"): the exam lock must never
# block, or be blocked by, an audit write.
_EXAM_LOCK_KEY = 0x6578_616D_5F6C_6F63  # ascii "exam_loc"

# FR-38: the exam may not send mail, e-signature requests or trademark/web searches.
_MOCKS_FORCED_ON = (
    "mock_resend",
    "mock_sendgrid",
    "mock_docusign",
    "mock_signa",
    "mock_tmsearch",
    "mock_serper",
)


class ExamSettings(BaseSettings):
    """Read ONLY by the exam process, from ``eval/private/exam.env`` (or ``EVAL_*`` env)."""

    model_config = SettingsConfigDict(
        env_file=PRIVATE_DIR / "exam.env", env_prefix="EVAL_", extra="ignore"
    )

    claude_api_key: str | None = None  # EVAL_CLAUDE_API_KEY -- dedicated, never the app's key
    owner_email: str | None = None  # EVAL_OWNER_EMAIL -- the one exam owner
    # Reducto's price per credit, so the OCR estimate can show dollars. Optional.
    reducto_usd_per_credit: float | None = Field(None, validation_alias="REDUCTO_USD_PER_CREDIT")


class ExamRefused(Exception):
    """A guard said no. The message is printed, nothing was paid, the exit code is 2."""


@dataclass(frozen=True)
class LiveDB:
    # The URL carries the password; keep it out of reprs, tracebacks and assertion output.
    url: str = field(repr=False)
    sessions: sessionmaker
    org_id: str


@dataclass(frozen=True)
class Owner:
    user_id: str
    email: str


def _load_exam_settings() -> ExamSettings:
    try:
        return ExamSettings()
    except ValidationError as exc:
        # Name the fields, never the values: the file holds a spending key.
        fields = ", ".join(".".join(str(part) for part in e["loc"]) for e in exc.errors())
        raise ExamRefused(f"eval/private/exam.env is not valid (check: {fields})") from exc


def isolate(*, paid: bool, eval_db_name: str = EVAL_DB_NAME) -> LiveDB:
    """Repoint this process at the throwaway database; return the live DB handle.

    Refuses (``ExamRefused``) unless it is a local/dev/test environment, the app's engine has
    not been built yet, and -- for paid commands -- the exam has its own key and no mock,
    Voyage or Cohere is in play (client text may go only to Anthropic and Reducto, FR-35).
    """
    # 1. Where and when. Environment first: it is the cheaper, clearer message.
    if settings.environment.strip().lower() not in _LOCAL_ENVIRONMENTS:
        raise ExamRefused(
            f"the exam only runs in a local/development environment, not '{settings.environment}'"
        )
    if "app.core.database" in sys.modules:
        raise ExamRefused(
            "the app's database engine already exists in this process, so it may point at the "
            "live database; start the exam in a fresh process (python -m eval.exam ...)"
        )
    if not eval_db_name.startswith(EVAL_DB_NAME):
        raise ExamRefused(f"the exam database name must start with '{EVAL_DB_NAME}'")
    live_url = make_url(settings.database_url)
    if live_url.get_backend_name() != "postgresql":
        raise ExamRefused("the exam needs a PostgreSQL database server")
    if live_url.database == eval_db_name:
        raise ExamRefused("the exam database must not be the live database")

    # 2. Capture what the app would have used, then read the exam's own config.
    # The Anthropic SDK falls back to ANTHROPIC_API_KEY when it is handed no key, so that
    # variable counts as a "real" key too -- and is removed below so it can never be the
    # fallback for a command that was given none.
    real_keys = {
        key for key in (settings.claude_api_key, os.environ.get("ANTHROPIC_API_KEY")) if key
    }
    exam = _load_exam_settings()
    exam_key = (exam.claude_api_key or "").strip() or None

    # 3. Paid commands: a dedicated key, no mocks, no third party.
    if paid:
        if exam_key is None:
            raise ExamRefused(
                "paid commands need their own Anthropic key: set EVAL_CLAUDE_API_KEY in "
                "backend/eval/private/exam.env (a key made for the exam only)"
            )
        if exam_key in real_keys:
            raise ExamRefused(
                "EVAL_CLAUDE_API_KEY is the same as the app's Claude key; the exam must use "
                "its own key so its spend can be told apart and capped separately"
            )
        if settings.mock_claude:
            raise ExamRefused("MOCK_CLAUDE is on: an exam against a fake model measures nothing")
        if settings.allow_mock_embeddings:
            raise ExamRefused(
                "ALLOW_MOCK_EMBEDDINGS is on: random vectors would make every retrieval score noise"
            )
        if settings.embedding_provider != "local" or settings.rerank_provider not in {
            "local",
            "none",
        }:
            raise ExamRefused(
                "contract text may go only to Anthropic and Reducto: set EMBEDDING_PROVIDER=local "
                "and RERANK_PROVIDER=local (or none) so Voyage and Cohere are not used"
            )

    # 4. Repoint the app's settings singleton in place. Every `from app.core.config import
    # settings` shares this one object, so nothing imported later can see the old values.
    settings.database_url = live_url.set(database=eval_db_name).render_as_string(
        hide_password=False
    )
    # None for a free command: it cannot make a paid call by accident.
    settings.claude_api_key = exam_key if paid else None
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    settings.storage_root = WORK_DIR / "storage"
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    settings.storage_backend = "local"
    # The dev container exports DISABLE_RBAC=true; with it on every user is an admin and the
    # portfolio user would see every exam contract.
    settings.disable_rbac = False
    # The exam's ceiling is its spend control; the per-org daily cap would 429 a run midway.
    settings.claude_daily_token_cap_per_org = 0
    settings.sentry_dsn = None
    settings.voyage_api_key = None
    settings.cohere_api_key = None
    for name in _MOCKS_FORCED_ON:
        setattr(settings, name, True)

    # 5. Only now may the app's database module be imported. Prove it landed on the exam DB.
    importlib.import_module("app.models")  # register every table before anything queries
    database = importlib.import_module("app.core.database")
    if database.engine.url.database != eval_db_name:
        raise ExamRefused("the app's engine did not move to the exam database; refusing to go on")
    claude = importlib.import_module("app.integrations.claude")
    if len(claude._anthropic_clients):
        raise ExamRefused("an Anthropic client was built before the exam key was set; refusing")

    # 6. Same backstop tests/conftest.py uses: the exam runs its jobs itself and sends none on
    # purpose, but the app queues one now and then (a portfolio question's table reads, which the
    # runner then runs in-process). A queued job must not be able to reach the live worker's broker.
    celery_app = importlib.import_module("app.jobs.celery_app").celery_app
    celery_app.conf.update(broker_url="memory://", result_backend="cache+memory://")

    # 7. The live database, for audit rows and ledger copies only. Small pool: the exam holds
    # one connection for its lock and never has two live sessions open at once.
    from app.organizations.models import Organization

    engine = create_engine(
        live_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        connect_args={
            "options": "-c idle_in_transaction_session_timeout="
            f"{settings.db_idle_transaction_timeout_ms}"
        },
    )
    sessions = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)
    try:
        with sessions() as db:
            # Single-tenant: there is one organization (as register_user finds it).
            org_id = db.scalar(select(Organization.id).order_by(Organization.created_at).limit(1))
    except SQLAlchemyError as exc:
        raise ExamRefused(
            f"cannot read the live database ({type(exc).__name__}); is the stack running?"
        ) from exc
    if org_id is None:
        raise ExamRefused("the live database has no organization yet; finish the app's setup first")
    return LiveDB(
        url=live_url.render_as_string(hide_password=False), sessions=sessions, org_id=org_id
    )


def audit(
    live: LiveDB,
    *,
    action: str,
    actor: Owner | None,
    resource_type: str,
    resource_id: str | None,
    after: dict | None = None,
    metadata: dict | None = None,
) -> None:
    """Write one ``eval.*`` audit row on its own short live session and commit it at once.

    Own session, committed before returning: the audit write takes an app-wide advisory lock
    held until commit, so it must never sit open across an AI/OCR call or beside another live
    session of this process (CLAUDE.md, "audit-lock freeze 2026-09-28").
    """
    # FR-39 and the isolation test both rely on this: the exam writes only eval.* audit rows.
    if not action.startswith("eval."):
        raise ValueError(f"exam audit actions start with 'eval.', got '{action}'")
    from app.core.audit import write_audit_log

    with live.sessions() as db:
        write_audit_log(
            db,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            org_id=live.org_id,
            actor_user_id=actor.user_id if actor else None,
            after=after,
            metadata=metadata,
        )
        db.commit()


def authenticate_owner(live: LiveDB, email: str | None, *, command: str) -> Owner:
    """The configured exam owner, as an active AEGIS user (FR-31).

    ``email`` defaults to the owner in ``exam.env``; another address is refused. Roles and
    permissions are deliberately not consulted. Every refusal is audited as
    ``eval.access_refused``.

    ponytail: no password (the owner dropped it on 2026-10-07): whoever can run the CLI on the
    host is trusted, and the typed spending ceiling is the go-ahead for money. Ask for the AEGIS
    password again here if the exam ever runs on a shared host.
    """
    claimed = (email or _load_exam_settings().owner_email or "").strip()

    def refuse(reason: str, message: str, actor: Owner | None = None) -> NoReturn:
        audit(
            live,
            action="eval.access_refused",
            actor=actor,
            resource_type="eval_run",
            resource_id=None,
            after={"command": command, "claimed_email": claimed, "reason": reason},
        )
        raise ExamRefused(message)

    owner_email = (_load_exam_settings().owner_email or "").strip()
    if not owner_email:
        refuse(
            "owner_not_configured",
            "the exam owner is not set: put EVAL_OWNER_EMAIL=<your AEGIS email> in "
            "backend/eval/private/exam.env",
        )
    if claimed.lower() != owner_email.lower():
        refuse("not_the_owner", "only the exam owner can run this command")

    from app.auth.models import User
    from app.core.enums import UserStatus

    # Read, then close the session BEFORE any audit write (one live session at a time).
    with live.sessions() as db:
        row = db.execute(
            select(User.id, User.email, User.status).where(
                func.lower(User.email) == claimed.lower(), User.org_id == live.org_id
            )
        ).first()
    if row is None:
        refuse("no_such_user", "no active AEGIS user with that email in this organization")
    owner = Owner(user_id=row.id, email=row.email)
    if row.status != UserStatus.ACTIVE:
        refuse("not_active", "no active AEGIS user with that email in this organization", owner)
    return owner


@contextmanager
def exam_lock(live: LiveDB) -> Iterator[None]:
    """Hold one exam command at a time, across processes (``pg_try_advisory_lock``).

    The lock is session-level on a dedicated autocommit live connection kept for the whole
    command; closing that connection releases it, so a crash cannot leave it stuck.

    ponytail: if that connection drops mid-command the lock is silently released. Acceptable
    for a single-owner CLI; re-take it on reconnect if two owners ever share a database.
    """
    engine = create_engine(live.url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            if not conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": _EXAM_LOCK_KEY}):
                raise ExamRefused("another exam command is running; wait for it to finish")
            yield
    finally:
        engine.dispose()
