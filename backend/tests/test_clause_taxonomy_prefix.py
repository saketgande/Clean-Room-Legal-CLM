"""Guards the compound-label fallback in canonical_clause_type.

Failure mode: the extractor emits a canonical type with a qualifier glued on
("ip_ownership_and_no_license"). Without the prefix fallback each variant became
its own bucket, so filtering by a canonical type silently missed those clauses.
"""

from app.contract_brain.clause_taxonomy import _ALIASES, canonical_clause_type


def test_compound_labels_collapse_to_their_canonical_type():
    assert canonical_clause_type("ip_ownership_and_no_license") == "ip_ownership"
    assert canonical_clause_type("data_protection_sub_processors") == "data_protection"
    assert canonical_clause_type("Dispute Resolution - Arbitration") == "dispute_resolution"


def test_exact_aliases_still_win_over_the_prefix_fallback():
    # The fallback must only run after an exact miss, never shadow a real alias.
    for raw, canon in _ALIASES.items():
        assert canonical_clause_type(raw) == canon


def test_unknown_labels_still_collapse_to_a_slug_not_a_wrong_type():
    assert canonical_clause_type("Publications") == "publications"
    assert canonical_clause_type(None) == "unknown"
