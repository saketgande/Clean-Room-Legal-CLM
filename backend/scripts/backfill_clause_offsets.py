#!/usr/bin/env python
"""Re-anchor ClauseExtraction char offsets to the text they actually describe.

Rows written before the locate_phrase fix carry the offsets the model reported,
and a model cannot count characters: across this corpus 57% pointed at the wrong
span (median 68 chars off, drifting to 188 past 6k) and 42% were NULL. The clause
*text* was always stored correctly, so the true span is recoverable by searching
the snapshot — no AI call, no cost.

Idempotent: re-running recomputes from stored text and changes nothing further.

    python scripts/backfill_clause_offsets.py --dry-run
    python scripts/backfill_clause_offsets.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

import app.main  # noqa: F401
from app.contract_brain.models import ClauseExtraction
from app.contract_files.blocks import locate_phrase
from app.contract_files.models import ContractTextSnapshot
from app.core.database import SessionLocal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    db = SessionLocal()
    snapshots: dict[str, str] = {}
    fixed = already = unlocated = 0
    try:
        rows = db.scalars(select(ClauseExtraction).order_by(ClauseExtraction.created_at)).all()
        if args.limit:
            rows = rows[: args.limit]

        for clause in rows:
            text = snapshots.get(clause.text_snapshot_id)
            if text is None:
                snap = db.get(ContractTextSnapshot, clause.text_snapshot_id)
                text = snapshots[clause.text_snapshot_id] = (snap.text if snap else "") or ""

            span = locate_phrase(text, clause.text)
            new_start, new_end = span if span else (None, None)
            if (clause.start_char, clause.end_char) == (new_start, new_end):
                already += 1
                continue
            if span is None:
                # Clear a wrong offset rather than leave it: an unresolvable
                # citation must read as unlocated, never point at another clause.
                unlocated += 1
            else:
                fixed += 1
            if not args.dry_run:
                clause.start_char, clause.end_char = new_start, new_end

        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    finally:
        db.close()

    verb = "would re-anchor" if args.dry_run else "re-anchored"
    print(f"clauses examined : {fixed + already + unlocated}")
    print(f"{verb:17}: {fixed}")
    print(f"already correct  : {already}")
    print(f"cleared to NULL  : {unlocated}  (text not found in snapshot)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
