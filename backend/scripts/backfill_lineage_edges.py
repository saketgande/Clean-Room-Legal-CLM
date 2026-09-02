#!/usr/bin/env python
"""Infer CLM lineage edges (SoW/DPA -> master) between existing contracts.

No explicit parent references exist on contract rows, so this infers them from
contract type + shared counterparty + dates. Every edge written carries
inferred=true and a confidence; retrieval surfaces them as "likely governed by",
for a human to confirm — never as asserted fact.

    python scripts/backfill_lineage_edges.py --dry-run
    python scripts/backfill_lineage_edges.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main  # noqa: F401,E402
from sqlalchemy import select  # noqa: E402

from app.contract_brain.entities import party_entity_key  # noqa: E402
from app.contract_brain.lineage import infer_parent, looks_like_amendment  # noqa: E402
from app.contract_brain.models import KnowledgeEdge, KnowledgeNode  # noqa: E402
from app.contracts.models import Contract  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        contracts = [
            {
                "id": c.id,
                "org_id": c.org_id,
                "title": c.title,
                "contract_type": c.contract_type,
                "counterparty_key": party_entity_key(c.counterparty_name),
                "effective_date": c.effective_date,
            }
            for c in db.scalars(select(Contract).where(Contract.deleted_at.is_(None)))
        ]
        by_org: dict[str, list[dict]] = {}
        for c in contracts:
            by_org.setdefault(c["org_id"], []).append(c)

        proposals = []
        for org_id, group in by_org.items():
            for child in group:
                hit = infer_parent(child, group)
                if hit is None:
                    continue
                parent, conf = hit
                proposals.append((child, parent, conf, "governed_by"))

        if not proposals:
            print("No lineage inferred. Likely no SoW/DPA sharing a counterparty with a master.")
            return 0

        print(f"{len(proposals)} lineage edge(s) inferred:\n")
        for child, parent, conf, kind in sorted(proposals, key=lambda p: -p[2]):
            print(f"  {conf:.2f}  {child['title'][:34]:36s} --{kind}-->  {parent['title'][:34]}")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        # hub node per contract, from the existing graph
        hubs = {
            n.contract_id: n
            for n in db.scalars(
                select(KnowledgeNode).where(
                    KnowledgeNode.node_type == "contract",
                    KnowledgeNode.is_stale.is_(False),
                )
            )
        }
        written = 0
        for child, parent, conf, kind in proposals:
            child_hub, parent_hub = hubs.get(child["id"]), hubs.get(parent["id"])
            if child_hub is None or parent_hub is None:
                continue                          # a contract not yet in the graph
            exists = db.scalars(
                select(KnowledgeEdge).where(
                    KnowledgeEdge.from_node_id == child_hub.id,
                    KnowledgeEdge.to_node_id == parent_hub.id,
                    KnowledgeEdge.edge_type == kind,
                    KnowledgeEdge.is_stale.is_(False),
                )
            ).first()
            if exists is not None:
                continue
            db.add(
                KnowledgeEdge(
                    org_id=child["org_id"],
                    edge_type=kind,
                    from_node_id=child_hub.id,
                    to_node_id=parent_hub.id,
                    contract_id=child["id"],
                    contract_version_id=child_hub.contract_version_id,
                    properties={
                        "inferred": True,
                        "confidence": conf,
                        "basis": "shared counterparty + type hierarchy",
                        "parent_contract_id": parent["id"],
                    },
                    is_stale=False,
                )
            )
            written += 1

        db.commit()
        print(f"\nwrote {written} inferred lineage edge(s) (inferred=true, for confirmation)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
