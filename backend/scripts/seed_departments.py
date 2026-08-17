"""Seed the dev users and contracts into Dr. Reddy's departments.

Without this, every user has department=NULL and every contract has
business_unit=NULL — which grants nothing, so the departmental rules are
invisible even though they are live. This makes them visible on a dev box.

    docker exec -w /app aegis-backend-1 python scripts/seed_departments.py
    docker exec -w /app aegis-backend-1 python scripts/seed_departments.py --dry-run

Idempotent: re-running assigns the same people to the same places. Never
touches a user or contract that already has a value, so a real assignment made
through the admin screen is not overwritten.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.auth.models import User
from app.contracts.models import Contract
from app.core.database import SessionLocal
from app.core.departments import BUSINESS_UNITS, DEPARTMENTS

# Dev accounts -> a department that shows off a different rule.
#   legal*    -> Legal & IP, joins at review, can edit
#   approver* -> Quality / Regulatory, join at approval, comment only
#   user*     -> business units, own their deals end to end
BY_EMAIL_PREFIX: dict[str, str] = {
    "legal1": "Legal & IP",
    "legal2": "Regulatory Affairs",
    "approver1": "Quality & Compliance",
    "approver2": "Finance & Tax",
    "user1": "Generics - North America",
    "user2": "PSAI / CDMO",
}


def main(dry_run: bool = False) -> int:
    db = SessionLocal()
    try:
        assert set(BY_EMAIL_PREFIX.values()) <= set(DEPARTMENTS), "unknown department in map"

        users = db.scalars(select(User)).all()
        changed = 0
        for user in users:
            if user.department:
                print(f"  skip  {user.email:<28} already in {user.department}")
                continue
            prefix = (user.email or "").split("@")[0].lower()
            dept = BY_EMAIL_PREFIX.get(prefix)
            if not dept:
                print(f"  skip  {user.email:<28} no mapping (stays without a department)")
                continue
            print(f"  set   {user.email:<28} -> {dept}")
            if not dry_run:
                user.department = dept
            changed += 1

        # Spread unassigned contracts across the units so every business-unit
        # user has something to see. Deterministic: ordered by id, round-robin.
        contracts = db.scalars(
            select(Contract).where(
                Contract.business_unit.is_(None), Contract.deleted_at.is_(None)
            ).order_by(Contract.id)
        ).all()
        units = ["Generics - North America", "PSAI / CDMO"]
        for i, contract in enumerate(contracts):
            unit = units[i % len(units)]
            if not dry_run:
                contract.business_unit = unit
        print(f"\n  {len(contracts)} contract(s) spread across {', '.join(units)}")

        if dry_run:
            db.rollback()
            print("\nDry run — nothing written.")
        else:
            db.commit()
            print(f"\nDone: {changed} user(s) assigned, {len(contracts)} contract(s) tagged.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    assert all(u in BUSINESS_UNITS for u in ("Generics - North America", "PSAI / CDMO"))
    raise SystemExit(main(dry_run="--dry-run" in sys.argv))
