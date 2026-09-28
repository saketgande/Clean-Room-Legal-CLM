"""AUTH-04: a saved Contract Brain answer is re-checked for each viewer: others see
only the sources they can open, and no answer text drawn from contracts they can't."""

from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.contract_brain import routes
from app.contract_brain.models import BrainQuery


def _saved(**fields):
    base = dict(
        id="q-1", org_id="org-1", query_scope="portfolio", question="Who caps liability?",
        contract_id=None, matter_id=None,
        answer="Acme caps liability at fees paid; Globex caps it at $5M.",
        citations=[{"quote": "fees paid"}],
        retrieval_metadata={
            "contract_ids": ["c-open", "c-walled"],
            "sources": {
                "semantic": [{"contract_id": "c-open", "text": "fees paid"}, {"contract_id": "c-walled", "text": "$5M"}],
                "clauses": [], "text": [],
                "graph": [{"fact": "Acme shares Delaware law with Globex", "contract_ids": ["c-walled"]},
                          {"fact": "SOW is governed by MSA", "direction": "parent"}],
            },
        },
        created_by_user_id="u-asker",
    )
    base.update(fields)
    return BrainQuery(**base)


def test_the_asker_sees_their_answer_unchanged():
    out = routes._viewer_copy(_saved(), viewer_id="u-asker", accessible_ids={"c-open"})
    assert out["answer"].startswith("Acme")
    assert len(out["retrieval_metadata"]["sources"]["semantic"]) == 2


def test_another_viewer_gets_no_answer_or_sources_from_contracts_they_cannot_open():
    saved = _saved()
    out = routes._viewer_copy(saved, viewer_id="u-other", accessible_ids={"c-open"})
    meta = out["retrieval_metadata"]
    assert [s["contract_id"] for s in meta["sources"]["semantic"]] == ["c-open"]
    assert meta["sources"]["graph"] == []
    assert meta["contract_ids"] == ["c-open"]
    assert "Globex" not in out["answer"]
    assert out["citations"] == []
    assert saved.answer.startswith("Acme")  # the stored row is untouched


def test_a_viewer_who_can_open_every_source_sees_the_answer():
    out = routes._viewer_copy(_saved(), viewer_id="u-other", accessible_ids={"c-open", "c-walled"})
    assert out["answer"].startswith("Acme")


def test_visibility_is_decided_in_the_query_not_after_the_limit():
    """AUTH-04 + API-03: the rule (own answers; records you can open, walls included;
    portfolio-wide only for admins) is one SQL condition, so the limit counts only
    answers this user may see."""
    from sqlalchemy.dialects import postgresql


    viewer = SimpleNamespace(id="u-1", org_id="org-1", permission_values=set(), clearance_level=None, roles=[])
    condition = routes._visible_brain_queries(None, viewer)
    sql = str(condition.compile(dialect=postgresql.dialect()))

    assert "brain_query.created_by_user_id" in sql
    assert "brain_query.contract_id IN" in sql and "brain_query.matter_id IN" in sql
    assert "ethical_wall" in sql  # walls are part of the condition, for admins too
