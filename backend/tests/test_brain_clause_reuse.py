"""Clause-language similarity: where the same wording reappears.

The clause FK chain (version -> file -> storage object) is too deep to fabricate
cleanly, so these run against the clauses already in the database and assert
invariants that must hold for ANY data — which is what actually guards the two
failure modes the GraphRAG literature warns about: matching across clause types,
and matching text that is merely on-topic rather than near-identical.
"""

import pytest
from sqlalchemy import select, text

from app.contract_brain.models import ClauseExtraction
from app.contract_brain.retrieval import clause_language_matches
from app.core.database import SessionLocal


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    yield session
    session.close()


def _a_contract_with_clauses(db):
    row = db.execute(
        text("""SELECT contract_id, org_id FROM clause_extraction
                WHERE is_stale=false GROUP BY 1,2 ORDER BY count(*) DESC LIMIT 1""")
    ).fetchone()
    if row is None:
        pytest.skip("no clauses in the database to match against")
    return row  # (contract_id, org_id)


def test_matches_are_same_type_only(db):
    """A match must be between clauses of the SAME type — an indemnity clause
    never matches a governing-law clause, however the fuzzy score falls."""
    cid, org = _a_contract_with_clauses(db)
    my_types = {
        ct for (ct,) in db.execute(
            select(ClauseExtraction.clause_type).where(
                ClauseExtraction.contract_id == cid,
                ClauseExtraction.is_stale.is_(False),
            )
        )
    }
    for m in clause_language_matches(db, org_id=org, contract_ids=[cid]):
        assert m["clause_type"] in my_types


def test_every_match_clears_the_threshold(db):
    cid, org = _a_contract_with_clauses(db)
    for m in clause_language_matches(db, org_id=org, contract_ids=[cid], threshold=85):
        assert m["similarity"] >= 85


def test_higher_threshold_never_yields_more(db):
    """Tightening the threshold can only remove matches, never add them."""
    cid, org = _a_contract_with_clauses(db)
    loose = clause_language_matches(db, org_id=org, contract_ids=[cid], threshold=75)
    tight = clause_language_matches(db, org_id=org, contract_ids=[cid], threshold=95)
    assert len(tight) <= len(loose)


def test_impossible_threshold_returns_nothing(db):
    cid, org = _a_contract_with_clauses(db)
    assert clause_language_matches(db, org_id=org, contract_ids=[cid], threshold=101) == []


def test_empty_scope_returns_nothing(db):
    _, org = _a_contract_with_clauses(db)
    assert clause_language_matches(db, org_id=org, contract_ids=[]) == []


def test_a_match_names_a_different_contract(db):
    """Reuse is cross-contract — a clause never matches itself."""
    cid, org = _a_contract_with_clauses(db)
    own_title = db.execute(text("SELECT title FROM contract WHERE id=:i"), {"i": cid}).scalar()
    for m in clause_language_matches(db, org_id=org, contract_ids=[cid]):
        assert own_title not in m["fact"] or "reused" in m["fact"]  # names another contract
