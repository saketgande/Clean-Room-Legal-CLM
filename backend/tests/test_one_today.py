"""APP-08: one notion of "today" (UTC) everywhere, and deadline facts only for
contracts in force."""

import pathlib
import re

from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  (register every mapper)
from app.contract_brain import retrieval


def test_no_code_reads_the_servers_local_date():
    root = pathlib.Path(retrieval.__file__).parents[1]
    offenders = [str(p.relative_to(root)) for p in root.rglob("*.py") if re.search(r"date\.today\(\)", p.read_text())]
    assert offenders == []


def test_deadline_facts_only_cover_contracts_in_force():
    statements = []

    class DB:
        def scalars(self, stmt):
            statements.append(str(stmt.compile(dialect=postgresql.dialect())))
            return iter([])

    assert retrieval.temporal_facts(DB(), org_id="org-1", contract_ids=["c-closed"], accessible_ids={"c-closed"}) == []
    assert len(statements) == 1 and "contract.lifecycle_stage = " in statements[0]
