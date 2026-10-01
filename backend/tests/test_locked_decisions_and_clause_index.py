"""DB-03: decisions on one approval rung, and replacements of a contract's clause
index, are serialised with row locks instead of check-then-act."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  (register every mapper)
from app.ai.controller import ai_controller
from app.ai.schemas import ClauseExtractionOutput
from app.approvals import service
from app.core.enums import ApprovalStatus


def test_a_decision_locks_the_rung_before_reading_its_status():
    locks = []

    class DB:
        def refresh(self, obj, **kwargs):
            locks.append(kwargs)
            obj.status = ApprovalStatus.APPROVED  # the other approver's decision committed first

    approval = SimpleNamespace(status=ApprovalStatus.PENDING)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(service._apply_decision(DB(), approval=approval, subject=None, decision="approve", comment=None,
                                            actor_user_id="u-2", actor_label="Bea"))
    assert exc.value.status_code == 409
    assert locks == [{"with_for_update": True}]


def test_only_the_current_version_may_replace_the_clause_index():
    class DB:
        sql = None

        def scalar(self, stmt):
            DB.sql = str(stmt.compile(dialect=postgresql.dialect()))
            return "v-2"  # a newer version became current while v-1 was being extracted

        def scalars(self, _stmt):
            raise AssertionError("an older version's extraction must not touch the index")

    context = SimpleNamespace(version=SimpleNamespace(id="v-1"), snapshot=SimpleNamespace(id="s-1", text="Term: one year."),
                              contract=SimpleNamespace(id="c-1", org_id="org-1"))
    output = ClauseExtractionOutput(clauses=[{"clause_type": "term", "text": "Term: one year."}])
    ai_controller._persist_clauses(DB(), output=output, context=context, created_by_user_id="u-1")
    assert "FOR UPDATE" in DB.sql
