#!/usr/bin/env python
"""Merge per-contract playbook_rule nodes into shared org-level entities.

Before: every contract that deviated from a rule got its own copy of that
rule node, so the graph was N disconnected stars and "who else deviates from
this rule" could not be asked.

After:  one node per rule per org, with every contract's deviation edge
re-pointed at it. No AI re-run — this is a pure graph rewrite from data already
extracted.

    python scripts/backfill_shared_playbook_rules.py --dry-run
    python scripts/backfill_shared_playbook_rules.py
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main  # noqa: F401,E402  — registers every model so mappers resolve
from sqlalchemy import select  # noqa: E402

from app.contract_brain.models import KnowledgeEdge, KnowledgeNode  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.playbooks.models import PlaybookDeviation  # noqa: E402


def entity_key_for(node: KnowledgeNode, deviations: dict[str, PlaybookDeviation]) -> str:
    """The same key ingestion now uses, derived from the old node's properties."""
    dev = deviations.get(str(node.properties.get("playbook_deviation_id")))
    if dev is not None and dev.playbook_rule_id:
        return f"rule:{dev.playbook_rule_id}"
    clause_type = (dev.clause_type if dev else None) or node.label or "unknown"
    return f"clause_type:{clause_type}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        old_nodes = list(
            db.scalars(
                select(KnowledgeNode).where(
                    KnowledgeNode.node_type == "playbook_rule",
                    KnowledgeNode.contract_id.isnot(None),   # the per-contract copies
                    KnowledgeNode.is_stale.is_(False),
                )
            )
        )
        if not old_nodes:
            print("Nothing to merge — no per-contract playbook_rule nodes.")
            return 0

        deviations = {
            str(d.id): d
            for d in db.scalars(select(PlaybookDeviation))
        }

        # group the duplicates: (org, entity_key) -> [nodes]
        groups: dict[tuple[str, str], list[KnowledgeNode]] = defaultdict(list)
        for node in old_nodes:
            groups[(node.org_id, entity_key_for(node, deviations))].append(node)

        print(f"{len(old_nodes)} per-contract rule nodes -> {len(groups)} distinct rules")
        for (org_id, key), nodes in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:10]:
            contracts = len({n.contract_id for n in nodes})
            print(f"   {key[:46]:48s} {len(nodes):3d} copies across {contracts} contracts")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        created = repointed = staled = 0
        for (org_id, key), nodes in groups.items():
            template = nodes[0]
            shared = db.scalars(
                select(KnowledgeNode).where(
                    KnowledgeNode.org_id == org_id,
                    KnowledgeNode.node_type == "playbook_rule",
                    KnowledgeNode.contract_id.is_(None),
                    KnowledgeNode.is_stale.is_(False),
                )
            ).all()
            shared_node = next(
                (n for n in shared if str(n.properties.get("entity_key")) == key), None
            )
            if shared_node is None:
                dev = deviations.get(str(template.properties.get("playbook_deviation_id")))
                shared_node = KnowledgeNode(
                    org_id=org_id,
                    node_type="playbook_rule",
                    label=template.label,
                    contract_id=None,
                    properties={
                        "entity_key": key,
                        "shared": True,
                        "playbook_rule_id": dev.playbook_rule_id if dev else None,
                        "clause_type": dev.clause_type if dev else template.label,
                    },
                    is_stale=False,
                    created_by_user_id=template.created_by_user_id,
                    updated_by_user_id=template.updated_by_user_id,
                )
                db.add(shared_node)
                db.flush()
                created += 1

            for node in nodes:
                for edge in db.scalars(
                    select(KnowledgeEdge).where(KnowledgeEdge.to_node_id == node.id)
                ):
                    edge.to_node_id = shared_node.id
                    repointed += 1
                node.is_stale = True
                staled += 1

        db.commit()
        print(f"\ncreated {created} shared nodes · repointed {repointed} edges · staled {staled} duplicates")

        # prove the traversal now exists
        cross = db.execute(
            select(KnowledgeEdge.to_node_id)
            .join(KnowledgeNode, KnowledgeNode.id == KnowledgeEdge.to_node_id)
            .where(KnowledgeNode.contract_id.is_(None), KnowledgeEdge.is_stale.is_(False))
        ).all()
        print(f"edges now pointing at shared entities: {len(cross)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
