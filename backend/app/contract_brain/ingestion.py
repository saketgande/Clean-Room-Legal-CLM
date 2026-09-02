from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalRequest
from app.auth.models import User
from app.contract_brain.entities import (
    clause_for_quote,
    jurisdiction_entity_key,
    party_entity_key,
    person_entity_key,
)
from app.contract_brain.models import ClauseExtraction, KnowledgeEdge, KnowledgeNode
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract, ContractParty
from app.core.audit import write_audit_log, write_timeline_event
from app.obligations.models import Obligation
from app.playbooks.models import PlaybookDeviation
from app.signatures.models import SignatureRecipient, SignatureRequest


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


    # ---- shared, org-level entities -------------------------------------
    # Contract-scoped nodes are recreated per contract (and staled per
    # contract). Shared entities must NOT be: one node per playbook rule for
    # the whole org, so edges from many contracts converge on it and
    # cross-contract traversal becomes possible at all. They carry
    # contract_id = NULL, which is also what keeps the staling loop above from
    # touching them.
    shared_cache: dict[tuple[str, str], KnowledgeNode] = {
        (node.node_type, str(node.properties.get("entity_key"))): node
        for node in db.scalars(
            select(KnowledgeNode).where(
                KnowledgeNode.org_id == org_id,
                KnowledgeNode.contract_id.is_(None),
                KnowledgeNode.is_stale.is_(False),
            )
        )
    }

    def shared_node(node_type: str, label: str, entity_key: str, properties: dict) -> KnowledgeNode:
        """One node per real-world entity, reused across contracts."""
        cached = shared_cache.get((node_type, entity_key))
        if cached is not None:
            return cached
        node = KnowledgeNode(
            org_id=org_id,
            node_type=node_type,
            label=label[:500],
            contract_id=None,          # shared: belongs to the org, not a contract
            contract_version_id=None,
            text_snapshot_id=None,
            properties={**properties, "entity_key": entity_key, "shared": True},
            is_stale=False,
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(node)
        db.flush()
        shared_cache[(node_type, entity_key)] = node
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

    # Jurisdiction as a shared entity: "Delaware", "State of Delaware" and
    # "Delaware, USA" resolve to one node, so "every contract under Delaware
    # law" is a single traversal. The governing-law relationship is named as
    # such rather than the generic related_to.
    jkey = jurisdiction_entity_key(contract.jurisdiction)
    if jkey:
        jnode = shared_node("jurisdiction", contract.jurisdiction, jkey,
                            {"normalized": jkey.removeprefix("jurisdiction:")})
        add_edge("governed_by_law", contract_node, jnode)
        counts["jurisdiction"] = counts.get("jurisdiction", 0) + 1

    # Parties are shared entities: "Nexus Legal Technologies, Inc." is the same
    # organisation in every contract it appears in, which is what makes
    # counterparty-level portfolio questions answerable. Names come from
    # ContractParty rows where they exist and from Contract.counterparty_name
    # otherwise — in this codebase the latter is the populated one.
    party_names: list[tuple[str, str | None]] = [
        (party.name, party.party_type)
        for party in db.scalars(
            select(ContractParty).where(
                ContractParty.org_id == org_id, ContractParty.contract_id == contract.id
            )
        )
    ]
    if contract.counterparty_name:
        party_names.append((contract.counterparty_name, "counterparty"))

    seen_party_keys: set[str] = set()
    for name, party_type in party_names:
        key = party_entity_key(name)
        if key is None or key in seen_party_keys:
            continue                    # placeholder name, or already linked
        seen_party_keys.add(key)
        pnode = shared_node("party", name, key, {"normalized": key.removeprefix("org:")})
        add_edge("negotiated_with", contract_node, pnode, {"party_type": party_type})
        counts["party"] += 1

    clause_nodes: list = []   # (node, text, start_char, end_char) for provenance
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
        # Keep the clause node beside its text so an obligation's source quote
        # can be traced back to the clause that created it (provenance below).
        clause_nodes.append((cnode, (clause.text or ""), clause.start_char, clause.end_char))
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

        # Provenance: which clause created this obligation. The obligation's
        # source quote is a verbatim span from the contract, so the clause whose
        # text contains it is the source — evidence, not a type guess. Char
        # offsets settle ties when a short quote sits inside several clauses.
        quote = (ob.source_citation or {}).get("quote") if isinstance(ob.source_citation, dict) else None
        hit = clause_for_quote(quote, [(cn, ctext) for cn, ctext, _, _ in clause_nodes])
        if hit is not None:
            add_edge("creates_obligation", hit, onode,
                     {"obligation_type": ob.obligation_type})
            counts["provenance"] = counts.get("provenance", 0) + 1

    # Approvers — shared PERSON nodes, so "every contract Jane approved" is one
    # traversal. The approval status stays on the edge; the person is the node.
    for appr in db.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.org_id == org_id, ApprovalRequest.contract_id == contract.id
        )
    ):
        approver = db.get(User, appr.approver_user_id) if appr.approver_user_id else None
        key = person_entity_key(
            email=approver.email if approver else None,
            name=approver.full_name if approver else None,
        )
        if key is None:
            continue                       # an approval with no identified approver yet
        pnode = shared_node("person", approver.full_name, key,
                            {"email": approver.email, "role": "approver"})
        add_edge("approved_by", contract_node, pnode,
                 {"status": appr.status, "approval_request_id": appr.id})
        counts["person"] = counts.get("person", 0) + 1

    # Signatories — shared PERSON nodes from the recipients on each signature
    # request (counterparty and our own signers alike).
    for sig in db.scalars(
        select(SignatureRequest).where(
            SignatureRequest.org_id == org_id, SignatureRequest.contract_id == contract.id
        )
    ):
        for recip in db.scalars(
            select(SignatureRecipient).where(
                SignatureRecipient.signature_request_id == sig.id
            )
        ):
            key = person_entity_key(email=recip.email, name=recip.name)
            if key is None:
                continue
            pnode = shared_node("person", recip.name, key,
                                {"email": recip.email, "role": recip.role or "signatory"})
            add_edge("signed_by", contract_node, pnode,
                     {"status": sig.status, "role": recip.role})
            counts["person"] = counts.get("person", 0) + 1

    for dev in db.scalars(
        select(PlaybookDeviation).where(
            PlaybookDeviation.org_id == org_id, PlaybookDeviation.contract_id == contract.id
        )
    ):
        # Entity resolution: the rule is the same rule whichever contract
        # deviated from it. Key on the rule id where we have one, else on the
        # clause type so older deviations still converge.
        entity_key = f"rule:{dev.playbook_rule_id}" if dev.playbook_rule_id else f"clause_type:{dev.clause_type or 'unknown'}"
        rnode = shared_node(
            "playbook_rule",
            dev.clause_type or "playbook_rule",
            entity_key,
            {"playbook_rule_id": dev.playbook_rule_id, "clause_type": dev.clause_type},
        )
        add_edge(
            "deviates_from_rule",
            contract_node,
            rnode,
            {"severity": dev.severity, "playbook_deviation_id": dev.id, "issue": (dev.issue or "")[:300]},
        )
        counts["playbook_rule"] += 1

    # Explicit lineage: the parent the requester chose on the agreement form.
    # This is a STATED relationship (confidence 1.0, inferred=False), which the
    # answer prompt asserts plainly — unlike the same-counterparty guess the
    # backfill infers. The parent must be a contract already in the graph.
    parent_id = (contract.metadata_json or {}).get("parent_contract_id")
    if parent_id:
        parent_hub = db.scalars(
            select(KnowledgeNode).where(
                KnowledgeNode.org_id == org_id,
                KnowledgeNode.node_type == "contract",
                KnowledgeNode.contract_id == parent_id,
                KnowledgeNode.is_stale.is_(False),
            )
        ).first()
        if parent_hub is not None:
            add_edge(
                "governed_by",
                contract_node,
                parent_hub,
                {"inferred": False, "confidence": 1.0, "basis": "chosen on the intake form",
                 "parent_contract_id": parent_id},
            )
            counts["lineage"] = counts.get("lineage", 0) + 1

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
