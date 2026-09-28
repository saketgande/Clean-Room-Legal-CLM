"""DB-02: the database keeps two rules the application used to keep by convention —
one authoritative version per contract, and job keys unique per organization."""

import inspect
import pathlib
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  (register every mapper)
from app.contract_files import routes as contract_routes
from app.contract_files import service as contract_service
from app.contract_files.models import ContractVersion
from app.jobs.models import JobRun
from app.signatures import service as signature_service


def test_the_migration_adds_both_rules():
    migration = pathlib.Path("alembic/versions/0043_db_invariants.py").read_text()
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_version_authoritative" in migration
    assert "WHERE is_authoritative AND deleted_at IS NULL" in migration
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_run_org_idempotency_key" in migration
    assert "DROP INDEX IF EXISTS ix_job_run_idempotency_key" in migration


def test_the_models_carry_the_same_rules():
    assert "uq_contract_version_authoritative" in {getattr(arg, "name", None) for arg in ContractVersion.__table_args__}
    assert "uq_job_run_org_idempotency_key" in {c.name for c in JobRun.__table__.constraints if c.name}
    assert JobRun.__table__.c.idempotency_key.unique is not True  # per-org now, not global


def test_promoting_a_version_demotes_the_others_first():
    calls = []

    class DB:
        def execute(self, stmt):
            calls.append(("execute", str(stmt.compile(dialect=postgresql.dialect()))))

        def flush(self):
            calls.append(("flush", None))

    contract = SimpleNamespace(id="c-1", current_authoritative_version_id="v-1", updated_by_user_id=None)
    version = SimpleNamespace(id="v-2", is_authoritative=False, updated_by_user_id=None)
    contract_service.promote_version(DB(), contract=contract, version=version, actor_user_id="u-1")

    assert calls[0][0] == "execute" and "UPDATE contract_version SET is_authoritative" in calls[0][1]
    assert calls[1] == ("flush", None)  # the demotion lands before the promotion
    assert version.is_authoritative is True
    assert contract.current_authoritative_version_id == "v-2"


def test_every_promotion_goes_through_it():
    counts = {contract_service: 1, contract_routes: 3, signature_service: 1}
    for module, expected in counts.items():
        source = inspect.getsource(module)
        assert source.count("promote_version(db, contract=") == expected, module.__name__
        assert "is_authoritative = row.id ==" not in source and "is_authoritative = version.id ==" not in source
