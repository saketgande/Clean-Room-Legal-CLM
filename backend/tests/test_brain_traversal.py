"""Graph traversal: shared_entity_links and lineage_facts.

The entity-resolution tests cover the pure normalisation logic; these cover the
SQL that actually walks the graph. They seed a small graph under a throwaway
org id and delete it afterwards, so the traversal is exercised against real
Postgres rather than a mock.

The shape seeded:

    C1 ─negotiated_with→ [Contoso]* ←negotiated_with─ C2
    C1 ─deviates_from_rule→ [rule:x]* ←deviates_from_rule─ C3
    C_sow ─governed_by→ C_msa            (inferred)

    * = shared, org-level node (contract_id = NULL)
"""

import uuid

import pytest

from sqlalchemy import text

from app.contract_brain.models import KnowledgeEdge, KnowledgeNode
from app.contract_brain.retrieval import (
    cohort_facts,
    lineage_facts,
    shared_entity_links,
    temporal_facts,
)
from app.contracts.models import Contract
from app.core.database import SessionLocal


@pytest.fixture
def graph():
    """Seed a small graph, yield (db, org_id, ids), then delete it."""
    db = SessionLocal()
    # Contract rows FK to a real org + owner, so reuse an existing user rather
    # than fabricating auth rows. The graph nodes are isolated by their own ids,
    # not by org, so other contracts in this org can't leak into the traversal.
    owner = db.execute(text('SELECT org_id, id FROM "user" LIMIT 1')).fetchone()
    if owner is None:
        pytest.skip("no user in the test database to own fixture contracts")
    org, owner_id = owner
    made: list = []
    contract_ids_made: list[str] = []

    def node(node_type, label, contract_id, props=None):
        n = KnowledgeNode(
            org_id=org, node_type=node_type, label=label,
            contract_id=contract_id, properties=props or {}, is_stale=False,
        )
        db.add(n)
        db.flush()
        made.append(n)
        return n

    def edge(edge_type, src, dst, contract_id, props=None):
        e = KnowledgeEdge(
            org_id=org, edge_type=edge_type, from_node_id=src.id, to_node_id=dst.id,
            contract_id=contract_id, properties=props or {}, is_stale=False,
        )
        db.add(e)
        db.flush()
        made.append(e)
        return e

    from datetime import date, timedelta
    soon = date.today() + timedelta(days=30)      # the MSA expires within the horizon
    far = date.today() + timedelta(days=800)
    exp = {"msa": soon}                           # only the master expires soon
    ids = {c: str(uuid.uuid4()) for c in ("c1", "c2", "c3", "sow", "msa")}
    for c, cid in ids.items():
        db.add(Contract(id=cid, org_id=org, title=f"TRAVERSAL_TEST_{c}",
                        owner_user_id=owner_id, expiration_date=exp.get(c, far)))
        contract_ids_made.append(cid)
    db.flush()

    # contract hub nodes
    hubs = {c: node("contract", c.upper(), ids[c]) for c in ids}

    # a shared counterparty touched by c1 and c2
    contoso = node("party", "Contoso Consulting LLP", None,
                   {"entity_key": "org:contoso consulting", "shared": True})
    edge("negotiated_with", hubs["c1"], contoso, ids["c1"])
    edge("negotiated_with", hubs["c2"], contoso, ids["c2"])

    # a shared playbook rule broken by c1 and c3, with severities
    rule = node("playbook_rule", "liability_cap", None,
                {"entity_key": "rule:x", "shared": True})
    edge("deviates_from_rule", hubs["c1"], rule, ids["c1"], {"severity": "high"})
    edge("deviates_from_rule", hubs["c3"], rule, ids["c3"], {"severity": "medium"})

    # a shared person who signed c1 and c2 — resolved by email across contracts
    signer = node("person", "Jane Doe", None,
                  {"entity_key": "person:jane@x.com", "shared": True, "role": "signatory"})
    edge("signed_by", hubs["c1"], signer, ids["c1"])
    edge("signed_by", hubs["c2"], signer, ids["c2"])

    # a shared jurisdiction governing c1 and c3
    delaware = node("jurisdiction", "Delaware", None,
                    {"entity_key": "jurisdiction:delaware", "shared": True})
    edge("governed_by_law", hubs["c1"], delaware, ids["c1"])
    edge("governed_by_law", hubs["c3"], delaware, ids["c3"])

    # a lineage edge: the SoW is governed by the MSA (inferred)
    edge("governed_by", hubs["sow"], hubs["msa"], ids["sow"],
         {"inferred": True, "confidence": 0.85})

    db.commit()
    try:
        yield db, org, ids
    finally:
        # edges before nodes before contracts, to respect the FKs
        edge_ids = [m.id for m in made if isinstance(m, KnowledgeEdge)]
        node_ids = [m.id for m in made if isinstance(m, KnowledgeNode)]
        if edge_ids:
            db.query(KnowledgeEdge).filter(KnowledgeEdge.id.in_(edge_ids)).delete(synchronize_session=False)
        if node_ids:
            db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(node_ids)).delete(synchronize_session=False)
        if contract_ids_made:
            db.query(Contract).filter(Contract.id.in_(contract_ids_made)).delete(synchronize_session=False)
        db.commit()
        db.close()


class TestSharedEntityLinks:
    def test_finds_other_contracts_sharing_a_counterparty(self, graph):
        db, org, ids = graph
        facts = shared_entity_links(db, org_id=org, contract_ids=[ids["c1"]])
        party = [f for f in facts if f["entity_type"] == "party"]
        assert len(party) == 1
        assert party[0]["shared_with_count"] == 1          # only c2 also touches Contoso
        assert ids["c2"] in party[0]["contract_ids"]
        assert "Contoso" in party[0]["fact"]

    def test_finds_other_contracts_sharing_a_rule_with_severity(self, graph):
        db, org, ids = graph
        facts = shared_entity_links(db, org_id=org, contract_ids=[ids["c1"]])
        rule = [f for f in facts if f["entity_type"] == "playbook_rule"]
        assert len(rule) == 1
        assert rule[0]["shared_with_count"] == 1           # c3 also breaks it
        assert "severity" in rule[0]["fact"]
        assert "medium" in rule[0]["fact"]                 # c3's severity surfaced

    def test_finds_other_contracts_a_person_touched(self, graph):
        """The people dimension: 'which other contracts did this signer sign'."""
        db, org, ids = graph
        facts = shared_entity_links(db, org_id=org, contract_ids=[ids["c1"]])
        person = [f for f in facts if f["entity_type"] == "person"]
        assert len(person) == 1
        assert person[0]["shared_with_count"] == 1          # c2 shares the signer
        assert ids["c2"] in person[0]["contract_ids"]
        assert "Jane Doe" in person[0]["fact"]

    def test_finds_other_contracts_under_the_same_law(self, graph):
        """The jurisdiction dimension: 'what else is under Delaware law'."""
        db, org, ids = graph
        facts = shared_entity_links(db, org_id=org, contract_ids=[ids["c1"]])
        juris = [f for f in facts if f["entity_type"] == "jurisdiction"]
        assert len(juris) == 1
        assert ids["c3"] in juris[0]["contract_ids"]
        assert "Delaware" in juris[0]["fact"]

    def test_excludes_the_asking_contract_itself(self, graph):
        db, org, ids = graph
        facts = shared_entity_links(db, org_id=org, contract_ids=[ids["c1"]])
        for f in facts:
            assert ids["c1"] not in f["contract_ids"]

    def test_only_actually_shared_entities_are_returned(self, graph):
        """c2 shares the party and the signer with c1, but NOT the rule
        (c2 never deviated from it). So the rule must not appear."""
        db, org, ids = graph
        facts = shared_entity_links(db, org_id=org, contract_ids=[ids["c2"]])
        types = {f["entity_type"] for f in facts}
        assert types == {"party", "person"}
        assert "playbook_rule" not in types

    def test_ranked_by_breadth(self, graph):
        """More widely shared entities come first."""
        db, org, ids = graph
        facts = shared_entity_links(db, org_id=org, contract_ids=[ids["c1"]])
        counts = [f["shared_with_count"] for f in facts]
        assert counts == sorted(counts, reverse=True)

    def test_empty_scope_returns_nothing(self, graph):
        db, org, _ = graph
        assert shared_entity_links(db, org_id=org, contract_ids=[]) == []


class TestCohort:
    def test_two_hop_finds_multiply_connected_contracts(self, graph):
        """c1 shares the party (c2), the rule (c3), the signer (c2) and the
        jurisdiction (c3). c2 shares party + signer = 2 entities, one of them
        high-signal, so it qualifies as a cohort member."""
        db, org, ids = graph
        facts = cohort_facts(db, org_id=org, contract_ids=[ids["c1"]])
        members = {f["contract_id"]: f for f in facts}
        assert ids["c2"] in members
        assert members[ids["c2"]]["shared_count"] >= 2

    def test_rule_only_overlap_is_not_a_cohort(self, graph):
        """A contract sharing ONLY a common playbook rule — no party, person or
        jurisdiction — is not surfaced as closely related. c3 shares the rule
        and the jurisdiction with c1, so it qualifies via jurisdiction; a
        rule-only contract would not."""
        db, org, ids = graph
        facts = cohort_facts(db, org_id=org, contract_ids=[ids["c1"]])
        for f in facts:
            assert f["high_signal_count"] >= 1          # every member has a real signal

    def test_empty_scope_returns_nothing(self, graph):
        db, org, _ = graph
        assert cohort_facts(db, org_id=org, contract_ids=[]) == []


class TestTemporal:
    def test_expiring_contract_is_flagged(self, graph):
        db, org, ids = graph
        facts = temporal_facts(db, org_id=org, contract_ids=[ids["msa"]])
        expiring = [f for f in facts if f["kind"] == "expiring"]
        assert len(expiring) == 1
        assert "expires" in expiring[0]["fact"]

    def test_renewal_cascade_names_dependents(self, graph):
        """The graph-native temporal fact: the MSA expires soon and the SoW
        that is governed_by it is named as affected."""
        db, org, ids = graph
        facts = temporal_facts(db, org_id=org, contract_ids=[ids["msa"]])
        cascade = [f for f in facts if f["kind"] == "cascade"]
        assert len(cascade) == 1
        assert "RENEWAL CASCADE" in cascade[0]["fact"]
        assert "TRAVERSAL_TEST_sow" in cascade[0]["fact"]

    def test_a_contract_far_from_expiry_has_no_temporal_facts(self, graph):
        db, org, ids = graph
        # c1 expires far out and has no dependents
        assert temporal_facts(db, org_id=org, contract_ids=[ids["c1"]]) == []

    def test_empty_scope_returns_nothing(self, graph):
        db, org, _ = graph
        assert temporal_facts(db, org_id=org, contract_ids=[]) == []


class TestLineageFacts:
    def test_upward_from_the_child(self, graph):
        db, org, ids = graph
        facts = lineage_facts(db, org_id=org, contract_ids=[ids["sow"]])
        parent = [f for f in facts if f["direction"] == "parent"]
        assert len(parent) == 1
        assert "governed by" in parent[0]["fact"]
        assert "inferred" in parent[0]["fact"]             # hedge is present

    def test_downward_from_the_parent(self, graph):
        db, org, ids = graph
        facts = lineage_facts(db, org_id=org, contract_ids=[ids["msa"]])
        child = [f for f in facts if f["direction"] == "child"]
        assert len(child) == 1
        assert "affected if it is amended or terminated" in child[0]["fact"]

    def test_a_contract_with_no_lineage_gets_nothing(self, graph):
        db, org, ids = graph
        assert lineage_facts(db, org_id=org, contract_ids=[ids["c1"]]) == []

    def test_empty_scope_returns_nothing(self, graph):
        db, org, _ = graph
        assert lineage_facts(db, org_id=org, contract_ids=[]) == []
