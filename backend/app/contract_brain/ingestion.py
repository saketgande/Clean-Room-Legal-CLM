from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalRequest
from app.contract_brain.models import ClauseExtraction, KnowledgeEdge, KnowledgeNode
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract, ContractParty
from app.core.audit import write_audit_log, write_timeline_event
from app.obligations.models import Obligation
from app.playbooks.models import PlaybookDeviation
from app.signatures.models import SignatureRequest


def ingest_contract_brain(
    db: Session,
    *,
    org_id: str,
    created_by_user_id: str | None,
    contract: Contract,
    version: ContractVersion,
    snapshot: ContractTextSnapshot | None,
    request_id: str | None = None,
) -> dict:
    """Build the contract's knowledge-graph slice from already-extracted data,
    tied to the current authoritative version. Prior graph entries are marked
    stale instead of deleted so historical versions remain inspectable."""
    snapshot_id = snapshot.id if snapshot is not None else version.text_snapshot_id

    for edge in db.scalars(
        select(KnowledgeEdge).where(
            KnowledgeEdge.org_id == org_id,
            KnowledgeEdge.contract_id == contract.id,
            KnowledgeEdge.is_stale.is_(False),
        )
    ):
        edge.is_stale = True
        edge.updated_by_user_id = created_by_user_id
    for node in db.scalars(
        select(KnowledgeNode).where(
            KnowledgeNode.org_id == org_id,
            KnowledgeNode.contract_id == contract.id,
            KnowledgeNode.is_stale.is_(False),
        )
    ):
        node.is_stale = True
        node.updated_by_user_id = created_by_user_id
    db.flush()

    def add_node(node_type: str, label: str, properties: dict) -> KnowledgeNode:
        node = KnowledgeNode(
            org_id=org_id,
            node_type=node_type,
            label=label[:500],
            contract_id=contract.id,
            contract_version_id=version.id,
            text_snapshot_id=snapshot_id,
            properties=properties,
            is_stale=False,
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(node)
        db.flush()
        return node

    def add_edge(edge_type: str, src: KnowledgeNode, dst: KnowledgeNode, properties: dict | None = None) -> None:
        db.add(
            KnowledgeEdge(
                org_id=org_id,
                edge_type=edge_type,
                from_node_id=src.id,
                to_node_id=dst.id,
                contract_id=contract.id,
                contract_version_id=version.id,
                text_snapshot_id=snapshot_id,
                properties=properties or {},
                is_stale=False,
                created_by_user_id=created_by_user_id,
                updated_by_user_id=created_by_user_id,
            )
        )

    counts = {"contract": 1, "party": 0, "clause": 0, "obligation": 0, "approval": 0, "signature": 0, "playbook_rule": 0}

    contract_node = add_node(
        "contract",
        contract.title,
        {
            "contract_type": contract.contract_type,
            "lifecycle_stage": contract.lifecycle_stage,
            "risk_level": contract.risk_level,
            "counterparty_name": contract.counterparty_name,
        },
    )

    if contract.jurisdiction:
        jur = add_node("jurisdiction", contract.jurisdiction, {})
        add_edge("related_to", contract_node, jur)

    for party in db.scalars(
        select(ContractParty).where(
            ContractParty.org_id == org_id, ContractParty.contract_id == contract.id
        )
    ):
        pnode = add_node("party", party.name, {"party_type": party.party_type})
        add_edge("negotiated_with", contract_node, pnode)
        counts["party"] += 1

    for clause in db.scalars(
        select(ClauseExtraction).where(
            ClauseExtraction.org_id == org_id,
            ClauseExtraction.contract_id == contract.id,
            ClauseExtraction.contract_version_id == version.id,
            ClauseExtraction.is_stale.is_(False),
        )
    ):
        cnode = add_node(
            "clause",
            clause.heading or clause.clause_type,
            {"clause_type": clause.clause_type, "clause_extraction_id": clause.id},
        )
        add_edge("contains_clause", contract_node, cnode, {"clause_type": clause.clause_type})
        counts["clause"] += 1

    for ob in db.scalars(
        select(Obligation).where(
            Obligation.org_id == org_id,
            Obligation.contract_id == contract.id,
            Obligation.deleted_at.is_(None),
        )
    ):
        onode = add_node(
            "obligation",
            (ob.obligation_type or ob.description)[:200],
            {"obligation_id": ob.id, "status": ob.status, "due_date": str(ob.due_date)},
        )
        add_edge("has_obligation", contract_node, onode)
        counts["obligation"] += 1

    for appr in db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.org_id == org_id, ApprovalRequest.contract_id == contract.id
        )
    ):
        anode = add_node(
            "approval",
            f"approval:{appr.status}",
            {"approval_request_id": appr.id, "status": appr.status},
        )
        add_edge("approved_by", contract_node, anode, {"status": appr.status})
        counts["approval"] += 1

    for sig in db.scalars(
        select(SignatureRequest).where(
            SignatureRequest.org_id == org_id, SignatureRequest.contract_id == contract.id
        )
    ):
        snode = add_node(
            "signature",
            f"signature:{sig.status}",
            {"signature_request_id": sig.id, "status": sig.status},
        )
        add_edge("signed_by", contract_node, snode, {"status": sig.status})
        counts["signature"] += 1

    for dev in db.scalars(
        select(PlaybookDeviation).where(
            PlaybookDeviation.org_id == org_id, PlaybookDeviation.contract_id == contract.id
        )
    ):
        rnode = add_node(
            "playbook_rule",
            dev.clause_type or "playbook_rule",
            {"playbook_deviation_id": dev.id, "severity": dev.severity},
        )
        add_edge("deviates_from_rule", contract_node, rnode, {"severity": dev.severity})
        counts["playbook_rule"] += 1

    write_audit_log(
        db,
        action="contract.brain_ingested",
        resource_type="contract",
        resource_id=contract.id,
        org_id=org_id,
        actor_user_id=created_by_user_id,
        request_id=request_id,
        after={"contract_version_id": version.id, "node_counts": counts},
    )
    write_timeline_event(
        db,
        org_id=org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.brain_ingested",
        title="Contract Brain graph ingested",
        actor_user_id=created_by_user_id,
        request_id=request_id,
        details={"node_counts": counts},
    )
    return counts
