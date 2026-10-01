"""RAG-02: every retrieval lens reads a contract's CURRENT text: the authoritative
version's own snapshot and clauses, never a rejected proposal or superseded version."""

import inspect

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  (register every mapper)
from app.contract_brain import retrieval
from app.contract_brain.models import ClauseExtraction
from app.contracts.models import Contract


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_clause_lenses_only_read_the_current_authoritative_version():
    stmt = (
        select(ClauseExtraction.id)
        .join(Contract, Contract.id == ClauseExtraction.contract_id)
        .where(retrieval._current_clauses())
    )
    sql = _sql(stmt)
    assert "clause_extraction.contract_version_id = contract.current_authoritative_version_id" in sql
    assert "clause_extraction.is_stale IS false" in sql
    for lens in (retrieval.hybrid_sources,):
        assert "_current_clauses()" in inspect.getsource(lens)


def test_text_lens_only_reads_the_authoritative_versions_own_snapshot():
    source = inspect.getsource(retrieval.hybrid_sources)
    assert "ContractVersion.id == Contract.current_authoritative_version_id" in source
    assert "ContractTextSnapshot.id == ContractVersion.text_snapshot_id" in source
