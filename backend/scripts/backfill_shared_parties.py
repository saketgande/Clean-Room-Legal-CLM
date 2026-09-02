#!/usr/bin/env python
"""Build shared counterparty nodes for every existing contract.

Reads Contract.counterparty_name (and ContractParty rows where present),
normalises each to an organisation key, and links every contract to one shared
node per organisation. No AI re-run — this is a graph rewrite from data already
on the contract rows.

    python scripts/backfill_shared_parties.py --dry-run
    python scripts/backfill_shared_parties.py
    python scripts/backfill_shared_parties.py --suggest-merges
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main  # noqa: F401,E402
from sqlalchemy import select  # noqa: E402

from app.contract_brain.entities import normalize_org_name, party_entity_key, suggest_merges  # noqa: E402
from app.contract_brain.models import KnowledgeEdge, KnowledgeNode  # noqa: E402
from app.contracts.models import Contract, ContractParty  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--suggest-merges", action="store_true",
                    help="report near-duplicate names for a human to confirm, then exit")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        contracts = list(
            db.scalars(select(Contract).where(Contract.deleted_at.is_(None)))
        )
        extra_parties: dict[str, list[ContractParty]] = defaultdict(list)
        for party in db.scalars(select(ContractParty)):
            extra_parties[party.contract_id].append(party)

        if args.suggest_merges:
            names = [c.counterparty_name for c in contracts if c.counterparty_name]
            pairs = suggest_merges(names)
            if not pairs:
                print("No near-duplicates above threshold.")
            for a, b, score in pairs[:20]:
                print(f"  {score}%  '{a}'  ~  '{b}'")
            print("\nNothing merged — these are for a human to confirm.")
            return 0

        # org key -> (display name, [contract ids])
        orgs: dict[tuple[str, str], tuple[str, list[str]]] = {}
        skipped: list[str] = []
        for contract in contracts:
            names = [(p.name, p.party_type) for p in extra_parties.get(contract.id, [])]
            if contract.counterparty_name:
                names.append((contract.counterparty_name, "counterparty"))
            if not names:
                continue
            for raw, _ in names:
                key = party_entity_key(raw)
                if key is None:
                    skipped.append(raw)
                    continue
                display, ids = orgs.setdefault((contract.org_id, key), (raw, []))
                if contract.id not in ids:
                    ids.append(contract.id)

        print(f"{len(contracts)} contracts -> {len(orgs)} distinct organisations")
        print(f"skipped {len(skipped)} placeholder names (e.g. {sorted(set(skipped))[:3]})\n")
        for (org_id, key), (display, ids) in sorted(orgs.items(), key=lambda kv: -len(kv[1][1]))[:12]:
            print(f"   {display[:42]:44s} {len(ids):3d} contracts")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        created = linked = 0
        for (org_id, key), (display, contract_ids) in orgs.items():
            shared = next(
                (
                    n
                    for n in db.scalars(
                        select(KnowledgeNode).where(
                            KnowledgeNode.org_id == org_id,
                            KnowledgeNode.node_type == "party",
                            KnowledgeNode.contract_id.is_(None),
                            KnowledgeNode.is_stale.is_(False),
                        )
                    )
                    if str(n.properties.get("entity_key")) == key
                ),
                None,
            )
            if shared is None:
                shared = KnowledgeNode(
                    org_id=org_id,
                    node_type="party",
                    label=display,
                    contract_id=None,
                    properties={
                        "entity_key": key,
                        "shared": True,
                        "normalized": normalize_org_name(display),
                    },
                    is_stale=False,
                )
                db.add(shared)
                db.flush()
                created += 1

            for contract_id in contract_ids:
                # the contract's own hub node, from the existing graph
                hub = db.scalars(
                    select(KnowledgeNode).where(
                        KnowledgeNode.contract_id == contract_id,
                        KnowledgeNode.node_type == "contract",
                        KnowledgeNode.is_stale.is_(False),
                    )
                ).first()
                if hub is None:
                    continue            # contract never ingested into the graph
                already = db.scalars(
                    select(KnowledgeEdge).where(
                        KnowledgeEdge.from_node_id == hub.id,
                        KnowledgeEdge.to_node_id == shared.id,
                        KnowledgeEdge.is_stale.is_(False),
                    )
                ).first()
                if already is not None:
                    continue
                db.add(
                    KnowledgeEdge(
                        org_id=org_id,
                        edge_type="negotiated_with",
                        from_node_id=hub.id,
                        to_node_id=shared.id,
                        contract_id=contract_id,
                        contract_version_id=hub.contract_version_id,
                        properties={"party_type": "counterparty"},
                        is_stale=False,
                    )
                )
                linked += 1

        db.commit()
        print(f"\ncreated {created} shared party nodes · {linked} contract links")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
