"""AUTH-02 (database-free half): every Contract Brain lens that can reach other
contracts must REQUIRE the user's accessible set, so no caller can forget it.
The SQL behaviour itself is covered in test_brain_traversal.TestAccessBoundary."""

import inspect
from types import SimpleNamespace

from app.contract_brain import retrieval


def test_every_lens_requires_the_accessible_set():
    for lens in (
        retrieval.lineage_facts,
        retrieval.temporal_facts,
        retrieval.clause_language_matches,
        retrieval.cohort_facts,
        retrieval.shared_entity_links,
        retrieval._graph_facts,
    ):
        param = inspect.signature(lens).parameters["accessible_ids"]
        assert param.default is inspect.Parameter.empty, lens.__name__


def test_hybrid_sources_requires_the_user():
    assert inspect.signature(retrieval.hybrid_sources).parameters["user"].default is inspect.Parameter.empty


def test_graph_edge_to_an_inaccessible_contract_is_detected():
    nodes = {
        "hub-mine": SimpleNamespace(contract_id="c-mine"),
        "hub-walled": SimpleNamespace(contract_id="c-walled"),
        "party": SimpleNamespace(contract_id=None),
    }
    visible = {"c-mine"}
    assert retrieval._reaches_inaccessible(nodes, ("hub-mine", "hub-walled"), visible)
    assert not retrieval._reaches_inaccessible(nodes, ("hub-mine", "party"), visible)
    assert not retrieval._reaches_inaccessible(nodes, ("hub-mine", "missing-node"), visible)
