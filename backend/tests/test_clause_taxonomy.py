"""Running the eval harness's golden fixture (backend/eval/seed_golden_fixtures.py)
surfaced a real data-quality gap: the clause_extraction prompt explicitly
suggests "governing law and disputes" as a clause category, but the taxonomy's
alias map only recognized "governing_law" — so a real, correctly-identified
governing-law clause silently failed the eval's coverage check. Pin the fix."""

from app.contract_brain.clause_taxonomy import canonical_clause_type


def test_governing_law_and_disputes_canonicalizes_to_governing_law():
    assert canonical_clause_type("governing_law_and_disputes") == "governing_law"
    assert canonical_clause_type("Governing Law and Disputes") == "governing_law"
