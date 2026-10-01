"""RAG-04: the knowledge graph asserts approvals and signatures only when they
happened. RAG-05: lineage survives re-ingesting a parent, and a directly uploaded
SoW gets an inferred (labelled) parent."""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

import app.models  # noqa: F401  (register every mapper)
from app.approvals.models import ApprovalDecision, ApprovalRequest
from app.contract_brain import ingestion
from app.contract_brain.models import KnowledgeEdge, KnowledgeNode
from app.contracts.models import Contract
from app.core.enums import ApprovalStatus, SignatureStatus
from app.signatures.models import SignatureRecipient, SignatureRequest


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class RouteDB:
    """Answers each query by the entity it selects; answers per entity are used in order."""

    def __init__(self, answers, users=None):
        self.answers = {entity: list(batches) for entity, batches in answers.items()}
        self.users = users or {}
        self.added = []

    def scalars(self, stmt):
        queue = self.answers.get(stmt.column_descriptions[0]["entity"]) or []
        return _Rows(queue.pop(0) if queue else [])

    def scalar(self, _stmt):
        return None  # the locked re-check for a shared entity finds nothing new

    def execute(self, _stmt):
        return _Rows([])  # the shared-entity advisory lock

    def get(self, _model, key):
        return self.users.get(key)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = str(uuid.uuid4())

    def edges(self, edge_type):
        return [o for o in self.added if isinstance(o, KnowledgeEdge) and o.edge_type == edge_type]


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(ingestion, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(ingestion, "write_timeline_event", lambda db, **kw: None)


def _contract(**fields):
    base = dict(id="c-msa", title="Acme MSA", contract_type="Services Agreement", lifecycle_stage="approval",
                risk_level=None, counterparty_name=None, jurisdiction=None, metadata_json={}, effective_date=None)
    base.update(fields)
    return SimpleNamespace(**base)


def _ingest(db, contract):
    ingestion.ingest_contract_brain(db, org_id="org-1", created_by_user_id="u-sys", contract=contract,
                                    version=SimpleNamespace(id="v-1", text_snapshot_id="s-1"), snapshot=None)


def test_only_real_decisions_become_approved_by_and_signed_by_needs_a_completed_signature():
    pending = SimpleNamespace(id="apr-1", approver_user_id="u-jane", status=ApprovalStatus.PENDING, step_order=1)
    decided = SimpleNamespace(id="apr-2", approver_user_id=None, status=ApprovalStatus.APPROVED, step_order=2)
    decision = SimpleNamespace(approval_request_id="apr-2", approver_user_id="u-raj", decision="approve", decided_at=None)
    users = {"u-jane": SimpleNamespace(email="jane@example.com", full_name="Jane"),
             "u-raj": SimpleNamespace(email="raj@example.com", full_name="Raj")}
    sig = SimpleNamespace(id="sig-1", status=SignatureStatus.SENT)
    recipient = SimpleNamespace(signature_request_id="sig-1", email="cfo@acme.com", name="Acme CFO",
                                role="signer", status="sent")
    db = RouteDB({ApprovalRequest: [[pending, decided]], ApprovalDecision: [[decision]],
                  SignatureRequest: [[sig]], SignatureRecipient: [[recipient]]}, users)
    _ingest(db, _contract())
    assert [e.properties["approval_request_id"] for e in db.edges("approved_by")] == ["apr-2"]  # Raj decided
    assert [e.properties["approval_request_id"] for e in db.edges("approval_requested_from")] == ["apr-1"]
    assert db.edges("signed_by") == []
    assert len(db.edges("signature_requested_from")) == 1


def test_reingesting_a_parent_keeps_its_children_linked():
    old_hub = KnowledgeNode(id="hub-old", org_id="org-1", node_type="contract", label="Acme MSA",
                            contract_id="c-msa", properties={}, is_stale=False)
    child_edge = KnowledgeEdge(id="edge-sow", org_id="org-1", edge_type="governed_by", from_node_id="hub-sow",
                               to_node_id="hub-old", contract_id="c-sow", properties={}, is_stale=False)
    db = RouteDB({KnowledgeEdge: [[], [child_edge]], KnowledgeNode: [[old_hub], []]})
    _ingest(db, _contract())
    new_hub = next(o for o in db.added if isinstance(o, KnowledgeNode) and o.node_type == "contract")
    assert old_hub.is_stale is True
    assert child_edge.to_node_id == new_hub.id


def test_a_directly_uploaded_sow_gets_an_inferred_parent():
    sow = _contract(id="c-sow", title="Acme SOW Phase 2", contract_type="Statement of Work",
                    counterparty_name="Acme Corp", effective_date=date(2026, 3, 1))
    msa = SimpleNamespace(id="c-msa", title="Acme MSA", contract_type="Master Services Agreement",
                          counterparty_name="Acme Corp", effective_date=date(2025, 1, 1))
    msa_hub = KnowledgeNode(id="hub-msa", org_id="org-1", node_type="contract", label="Acme MSA",
                            contract_id="c-msa", properties={}, is_stale=False)
    db = RouteDB({KnowledgeNode: [[], [], [msa_hub]], Contract: [[msa]]})
    _ingest(db, sow)
    [lineage] = db.edges("governed_by")
    assert lineage.to_node_id == "hub-msa"
    assert lineage.properties["inferred"] is True
