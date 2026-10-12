"""Counterparty relationship enrichment for intake: what we already have on file
with the parties a request names (prior contracts, NDAs, requests).

Sanctions and conflict screening used to live here too. They were removed
(2026-09-28): matching was name-only against one US list, and a hit blocked
nothing outside one workflow step, so a green "Clear" looked like compliance
that was not happening. Sanctions checks belong with a dedicated screening
provider. The ``screening`` column and ``intake_screening`` job keep their
names so queued jobs and stored rows stay valid.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

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

    def counterparty_relationship(self, org_id: str, name: str) -> dict:
        """A relationship dossier: prior contracts (count, stage
        mix, total value), prior NDAs, and prior intake requests — canonically matched."""
        contracts = [c for c in self.db.query(Contract).filter(Contract.org_id == org_id).all()
                     if c.counterparty_name and _name_match(name, c.counterparty_name)]
        ndas = [c for c in contracts if "nda" in (c.title or "").lower()
                or "non-disclosure" in (c.title or "").lower()]
        total_value = sum((c.value_amount or 0) for c in contracts)
        stages: dict[str, int] = {}
        for c in contracts:
            s = c.lifecycle_stage or "unknown"
            stages[s] = stages.get(s, 0) + 1
        reqs = [rq for rq in self.db.query(IntakeRequest).filter(IntakeRequest.org_id == org_id).all()
                if any(_name_match(name, n) for n in request_party_names(rq))]

        if not contracts and not reqs:
            note = "New counterparty — no prior contracts or requests on file."
        else:
            parts = []
            if contracts:
                parts.append(f"{len(contracts)} prior contract(s)"
                             + (f" (~${round(total_value / 1000)}K)" if total_value else ""))
            if ndas:
                parts.append(f"{len(ndas)} NDA on file")
            if reqs:
                parts.append(f"{len(reqs)} prior request(s)")
            note = "Known counterparty: " + ", ".join(parts) + "."
        return {"prior_contracts": len(contracts), "contract_stages": stages,
                "total_value": round(total_value) if total_value else None,
                "prior_ndas": len(ndas), "prior_nda_id": ndas[0].id if ndas else None,
                "prior_requests": len(reqs), "note": note}

    # ---- orchestrator ------------------------------------------------------------

    def compute_screening(self, r: IntakeRequest) -> dict:
        """Queries only, no writes. The relationship note is for the primary
        counterparty (the first party with that role, else the first party)."""
        parties = gather_parties(r)
        if not parties:
            return {"status": "skipped", "note": "No parties captured on this request.",
                    "checked_at": utcnow().isoformat()}
        primary = next((p for p in parties if p["role"] == "counterparty"), parties[0])
        return {
            "counterparty": primary["name"],
            "parties": parties,
            "relationship": self.counterparty_relationship(r.org_id, primary["name"]),
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
                                after={"counterparty": result.get("counterparty"),
                                       "prior_contracts": result["relationship"]["prior_contracts"]})
                db.commit()
            except Exception:
                db.rollback()  # result already persisted; only the audit row is lost
        return result


# --- DI-MIGRATION: temporary wrappers ---------------------------------------
# Imported directly by app.intake.service, app.jobs.tasks and tests. Tracked
# in backend/DI_MIGRATION.md.

def counterparty_relationship(db: Session, org_id: str, name: str) -> dict:
    return ScreeningService(db).counterparty_relationship(org_id, name)


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

