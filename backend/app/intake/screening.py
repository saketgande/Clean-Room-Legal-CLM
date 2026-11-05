"""Counterparty relationship enrichment for intake: what we already have on file
with the parties a request names (contracts and requests the viewer may see).

Sanctions and conflict screening used to live here too. They were removed
(2026-09-28): matching was name-only against one US list, and a hit blocked
nothing outside one workflow step, so a green "Clear" looked like compliance
that was not happening. Sanctions checks belong with a dedicated screening
provider. The ``screening`` column and ``intake_screening`` job keep their
names so queued jobs and stored rows stay valid.
"""

from __future__ import annotations

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contracts.contract_types import canonical_contract_type, contract_status
from app.contracts.models import Contract
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.intake.models import IntakeRequest

# ---- canonical name matching -----------------------------------------------
# Entity suffixes stripped so "Umbrella Corp" ≡ "Umbrella Corporation" ≡ "Umbrella".
_SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|llp|lp|plc|"
    r"gmbh|pbc|sa|nv|bv|ag|pvt|private|group|holdings?|partners?)\b")


def normalize_party_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    n = _SUFFIXES.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def _name_match(a: str, b: str) -> bool:
    na, nb = normalize_party_name(a), normalize_party_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    shared = ta & tb
    # one name's tokens fully contain the other's, or ≥2 shared tokens — and at
    # least one shared token is distinctive (len ≥ 4) to avoid generic matches.
    return (ta <= tb or tb <= ta or len(shared) >= 2) and any(len(t) >= 4 for t in shared)


def request_party_names(r: IntakeRequest) -> list[str]:
    """All party names on a request — the structured `parties`, plus the legacy
    field_values.counterparty."""
    names: list[str] = []
    for p in (r.parties or []):
        if isinstance(p, dict) and p.get("name"):
            names.append(str(p["name"]))
    fv = r.field_values if isinstance(r.field_values, dict) else {}
    if fv.get("counterparty"):
        names.append(str(fv["counterparty"]))
    return names


# ---- parties -------------------------------------------------------------

def gather_parties(r: IntakeRequest) -> list[dict]:
    """The request's parties: the structured `parties` list, else the legacy
    field_values.counterparty as a single counterparty party. Deduped by name."""
    parties: list[dict] = []
    seen: set[str] = set()
    for p in (r.parties or []):
        if isinstance(p, dict) and (p.get("name") or "").strip():
            key = normalize_party_name(p["name"])
            if key and key not in seen:
                seen.add(key)
                parties.append({"name": str(p["name"]).strip(),
                                "role": (p.get("role") or "counterparty"),
                                "is_person": bool(p.get("is_person"))})
    # Fall back to the legacy field_values.counterparty ONLY when structured
    # parties were never set (None). If a reviewer explicitly emptied the list
    # (r.parties == []), respect that — otherwise a removed party keeps getting
    # re-screened from field_values and the two panels contradict each other.
    if not parties and r.parties is None:
        fv = r.field_values if isinstance(r.field_values, dict) else {}
        cp = (fv.get("counterparty") or "").strip() if fv else ""
        if cp:
            parties.append({"name": cp, "role": "counterparty", "is_person": False})
    return parties


def _primary(parties: list[dict]) -> dict:
    return next((p for p in parties if p["role"] == "counterparty"), parties[0])


def primary_counterparty(r: IntakeRequest) -> str | None:
    """The party the relationship note is about: the first counterparty, else the first party."""
    parties = gather_parties(r)
    return _primary(parties)["name"] if parties else None


_CAP = 100  # list length on the request page; the summary still counts the whole visible set


class ScreeningService:
    """Counterparty relationship enrichment (the part of screening that's left).

    Part of the DI migration (see backend/DI_MIGRATION.md). Constructed with
    a ``db`` session; every function that took ``db`` first is now a method
    reading ``self.db``. Pure name-matching helpers and ``gather_parties``
    (no ``db``) stay module-level above.
    """

    def __init__(self, db: Session):
        self.db = db

    # ---- relationship ---------------------------------------------------------

    def visible_counterparty(self, user: User, r: IntakeRequest) -> dict:
        """What THIS viewer may see of the request's primary counterparty, computed at read time
        (never stored): contracts through the contract access rules, earlier requests through the
        intake queue rule minus any tied to a contract the viewer is walled off from. Counts and
        the note come only from that visible set, so a hidden file cannot be inferred."""
        db = self.db
        name = primary_counterparty(r)
        empty = {"name": name, "summary": {"contracts": 0, "active": 0, "requests": 0},
                 "note": None, "contracts": [], "requests": []}
        if not name:
            return empty
        # Imported here: intake.service imports this module at call time, not import time.
        from app.contracts.access import accessible_contract_filter, sealed_off_filter
        from app.core.rbac import has_permission
        from app.intake.service import _accessible

        contracts: list[Contract] = []
        tokens = normalize_party_name(name).split()
        if tokens and has_permission(user.permission_values, "contract:read"):
            q = select(Contract).where(
                Contract.org_id == user.org_id, accessible_contract_filter(user),
                Contract.deleted_at.is_(None), Contract.counterparty_name.is_not(None),
                # Tokens are [a-z0-9] only, so no LIKE escaping; a shared token is always a substring.
                or_(*[Contract.counterparty_name.ilike(f"%{t}%") for t in tokens]))
            if r.contract_id:
                q = q.where(Contract.id != r.contract_id)
            contracts = [c for c in db.scalars(q) if _name_match(name, c.counterparty_name)]
        today = utcnow().date()  # one notion of today (UTC), see tests/test_one_today.py

        def ended(c: Contract) -> bool:
            return contract_status(c, today) is None

        contracts.sort(key=lambda c: c.created_at, reverse=True)
        contracts.sort(key=ended)  # stable: live first, newest first within each

        walled_ok = select(Contract.id).where(Contract.org_id == user.org_id, sealed_off_filter(user))
        # ponytail: Python name match over the viewer's visible requests (68 rows today); add a
        # normalized party-name column if a counterparty page gets slow.
        reqs = [rq for rq in db.scalars(select(IntakeRequest).where(
                    _accessible(user), IntakeRequest.id != r.id,
                    or_(IntakeRequest.contract_id.is_(None), IntakeRequest.contract_id.in_(walled_ok))))
                if any(_name_match(name, n) for n in request_party_names(rq))]
        reqs.sort(key=lambda rq: rq.submitted_at or rq.created_at, reverse=True)

        ndas = [c for c in contracts if canonical_contract_type(c.contract_type) == "NDA"
                and contract_status(c, today) == "in_force"]
        if not contracts and not reqs:
            note = "New counterparty. No other contracts or requests on file."
        else:
            parts = []
            if contracts:
                parts.append(f"{len(contracts)} prior contract(s)")
            if ndas:
                parts.append(f"{len(ndas)} NDA in force")
            if reqs:
                parts.append(f"{len(reqs)} prior request(s)")
            note = "Known counterparty: " + ", ".join(parts) + "."
        return {
            "name": name,
            "summary": {"contracts": len(contracts),
                        "active": sum(1 for c in contracts
                                      if c.lifecycle_stage == "active" and not ended(c)),
                        "requests": len(reqs)},
            "note": note,
            "contracts": [{"id": c.id, "title": c.title, "contract_type": c.contract_type,
                           "stage": c.lifecycle_stage,
                           "value": float(c.value_amount) if c.value_amount is not None else None,
                           "currency": c.currency, "created_at": c.created_at,
                           "ended": ended(c)} for c in contracts[:_CAP]],
            "requests": [{"id": rq.id, "ref": rq.ref, "type_label": rq.type_label,
                          "status": rq.status, "date": rq.submitted_at or rq.created_at}
                         for rq in reqs[:_CAP]],
        }

    # ---- orchestrator ------------------------------------------------------------

    def compute_screening(self, r: IntakeRequest) -> dict:
        """Queries only, no writes. The relationship note is for the primary
        counterparty (the first party with that role, else the first party)."""
        parties = gather_parties(r)
        if not parties:
            return {"status": "skipped", "note": "No parties captured on this request.",
                    "checked_at": utcnow().isoformat()}
        return {
            "counterparty": _primary(parties)["name"],
            "parties": parties,
            "checked_at": utcnow().isoformat(),
            "status": "done",
        }

    def run_screening(self, r: IntakeRequest, *, actor_user_id: str | None = None) -> dict:
        """Compute and store the relationship note, then a best-effort audit event.
        Stored and audited in SEPARATE transactions so audit-chain lock contention
        can never roll back the result."""
        db = self.db
        result = self.compute_screening(r)
        r.screening = result
        db.commit()
        db.refresh(r)
        if result.get("status") == "done":
            try:
                write_audit_log(db, action="intake.relationship.checked", resource_type="intake_request",
                                resource_id=r.id, org_id=r.org_id, actor_user_id=actor_user_id,
                                after={"counterparty": result.get("counterparty")})
                db.commit()
            except Exception:
                db.rollback()  # result already persisted; only the audit row is lost
        return result


# --- DI-MIGRATION: temporary wrappers ---------------------------------------
# Imported directly by app.intake.service, app.jobs.tasks and tests. Tracked
# in backend/DI_MIGRATION.md.

def compute_screening(db: Session, r: IntakeRequest) -> dict:
    return ScreeningService(db).compute_screening(r)


def run_screening(db: Session, r: IntakeRequest, *, actor_user_id: str | None = None) -> dict:
    return ScreeningService(db).run_screening(r, actor_user_id=actor_user_id)


if __name__ == "__main__":  # pragma: no cover - gather_parties self-check
    from types import SimpleNamespace
    # explicitly emptied parties must NOT fall back to field_values (QA bug)
    r_emptied = SimpleNamespace(parties=[], field_values={"counterparty": "Globex"})
    assert gather_parties(r_emptied) == [], gather_parties(r_emptied)
    # never-set parties (legacy) still derives from field_values
    r_legacy = SimpleNamespace(parties=None, field_values={"counterparty": "Globex"})
    assert [p["name"] for p in gather_parties(r_legacy)] == ["Globex"]
    # structured parties win
    r_struct = SimpleNamespace(parties=[{"name": "Acme", "role": "counterparty"}], field_values={})
    assert [p["name"] for p in gather_parties(r_struct)] == ["Acme"]
    print("gather_parties self-check passed")

