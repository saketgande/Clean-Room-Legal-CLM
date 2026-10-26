"""Clause-language similarity: where the same wording reappears.

These build their OWN data in a transaction that is rolled back, so they run (and mean the
same thing) in an empty test database. They used to run against "whatever clauses are in the
database" and skipped when there were none, which is exactly what happens in ``legal_clm_test``.

The world is a throwaway org with three contracts:

* SOURCE: the contract being scoped; an indemnity clause and a governing-law clause.
* OTHER: the same indemnity wording with two words changed (a reuse), a governing-law clause
  that is nothing like SOURCE's, and a ``payment_terms`` clause whose text is IDENTICAL to
  SOURCE's indemnity (a decoy: same words, wrong clause type).
* HIDDEN: another near-identical indemnity, in a contract the viewer may not access.

That guards the two failure modes the GraphRAG literature warns about: matching across clause
types, and matching text that is merely on-topic rather than near-identical.
"""

import uuid
from types import SimpleNamespace

import pytest
from rapidfuzz import fuzz
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.contract_brain.models import ClauseExtraction
from app.contract_brain.retrieval import clause_language_matches
from app.contract_files.models import (
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contracts.models import Contract
from app.core.database import engine, new_uuid
from app.organizations.models import Organization

INDEMNITY = (
    "The Supplier shall indemnify, defend and hold harmless the Customer against all losses, "
    "damages, liabilities, costs and expenses, including reasonable legal fees, arising out of "
    "any third party claim that the Services infringe intellectual property rights, subject to "
    "prompt written notice of the claim and reasonable cooperation."
)
INDEMNITY_REUSED = INDEMNITY.replace("legal fees", "attorneys fees").replace("prompt", "timely")
GOVERNING_LAW = (
    "This Agreement is governed by the laws of England and Wales and the parties submit to the "
    "exclusive jurisdiction of the courts of England."
)
GOVERNING_LAW_OTHER = (
    "Delaware law governs this contract. Any dispute shall be resolved exclusively in the state "
    "courts located in New York County."
)
SOURCE, OTHER, HIDDEN = "ReuseFixture Source Agreement", "ReuseFixture Other Agreement", "ReuseFixture Hidden Agreement"


def _contract(db, org, user, title, clauses):
    """One contract with its full record chain and the given (clause_type, text) clauses."""
    contract = Contract(org_id=org.id, title=title, owner_user_id=user.id, created_by_user_id=user.id)
    db.add(contract)
    db.flush()
    file = ContractFile(org_id=org.id, contract_id=contract.id, file_label=f"{title}.docx")
    stored = StorageObject(org_id=org.id, storage_key=f"test/{uuid.uuid4().hex}", filename=f"{title}.docx",
                           mime_type="application/octet-stream", size_bytes=1, sha256_hash=uuid.uuid4().hex)
    db.add_all([file, stored])
    db.flush()
    version = ContractVersion(org_id=org.id, contract_id=contract.id, contract_file_id=file.id, version_number=1,
                              storage_object_id=stored.id, source="upload", is_authoritative=True)
    db.add(version)
    db.flush()
    snap = ContractTextSnapshot(org_id=org.id, contract_id=contract.id, contract_version_id=version.id,
                                extraction_method="test", text="\n".join(t for _, t in clauses))
    db.add(snap)
    db.flush()
    for clause_type, body in clauses:
        db.add(ClauseExtraction(org_id=org.id, contract_id=contract.id, contract_version_id=version.id,
                                text_snapshot_id=snap.id, clause_type=clause_type, heading=clause_type,
                                text=body, confidence="high"))
    db.flush()
    return contract.id


@pytest.fixture
def world():
    """The fixture world inside a transaction that is rolled back, so nothing is left behind."""
    connection = engine.connect()
    trans = connection.begin()
    db = Session(bind=connection)
    try:
        org = Organization(id=new_uuid(), name="Reuse Co", slug=f"reuse-co-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
        user = User(org_id=org.id, email=f"reuse-{uuid.uuid4().hex[:6]}@example.com", full_name="Reuser",
                    hashed_password="h", status="active")
        db.add(user)
        db.flush()
        ids = SimpleNamespace(
            source=_contract(db, org, user, SOURCE,
                             [("indemnification", INDEMNITY), ("governing_law", GOVERNING_LAW)]),
            other=_contract(db, org, user, OTHER,
                            [("indemnification", INDEMNITY_REUSED), ("governing_law", GOVERNING_LAW_OTHER),
                             ("payment_terms", INDEMNITY)]),
            hidden=_contract(db, org, user, HIDDEN, [("indemnification", INDEMNITY_REUSED)]),
        )
        # The fixture must really be what the tests assume: a near-identical pair, an unrelated pair.
        assert fuzz.token_sort_ratio(INDEMNITY, INDEMNITY_REUSED) >= 90
        assert fuzz.token_sort_ratio(GOVERNING_LAW, GOVERNING_LAW_OTHER) < 60
        yield SimpleNamespace(db=db, org=org.id, ids=ids, visible={ids.source, ids.other})
    finally:
        db.close()
        trans.rollback()
        connection.close()


def _matches(w, **kw):
    kw.setdefault("accessible_ids", w.visible)
    return clause_language_matches(w.db, org_id=w.org, contract_ids=[w.ids.source], **kw)


def test_matches_are_same_type_only(world):
    """A match must be between clauses of the SAME type — an indemnity clause
    never matches a payment-terms clause that happens to carry the same words."""
    found = _matches(world)
    assert found, "the reused indemnity must be found, or this test proves nothing"
    assert {m["clause_type"] for m in found} == {"indemnification"}
    assert "payment_terms" not in {m["clause_type"] for m in found}


def test_every_match_clears_the_threshold(world):
    found = _matches(world, threshold=85)
    assert found
    for m in found:
        assert m["similarity"] >= 85


def test_higher_threshold_never_yields_more(world):
    """Tightening the threshold can only remove matches, never add them."""
    loose = _matches(world, threshold=75)
    tight = _matches(world, threshold=95)
    assert loose, "nothing at the loose threshold would make this vacuous"
    assert len(tight) <= len(loose)


def test_impossible_threshold_returns_nothing(world):
    assert _matches(world), "there must be a match at the default threshold for 101 to rule it out"
    assert _matches(world, threshold=101) == []


def test_empty_scope_returns_nothing(world):
    """No scoped contracts, or no contracts the viewer may see, names nothing."""
    assert clause_language_matches(world.db, org_id=world.org, contract_ids=[], accessible_ids=set()) == []
    assert _matches(world, accessible_ids=set()) == []


def test_a_match_names_a_different_contract(world):
    """Reuse is cross-contract — a clause never matches itself, and it never names a
    contract the viewer cannot see."""
    facts = [m["fact"] for m in _matches(world)]
    assert facts
    for fact in facts:
        assert OTHER in fact
        assert SOURCE not in fact
        assert HIDDEN not in fact
