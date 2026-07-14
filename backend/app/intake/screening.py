"""Third-party risk screening for intake: sanctions/OFAC, conflict-of-interest,
and counterparty relationship enrichment.

Safety posture mirrors the reference implementation: an empty or stale (>30d)
sanctions list returns "unavailable" — never "clear". Every screening run is an
audit event (the check itself is defensibility evidence).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import timedelta

import httpx
from sqlalchemy import Text, cast, func, or_
from sqlalchemy.orm import Session

from app.contracts.models import Contract, ContractParty
from app.core.audit import write_audit_log
from app.core.database import utcnow
from app.intake.models import IntakeRequest, SanctionsListEntry

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
STALE_AFTER = timedelta(days=30)
# Comprehensively embargoed jurisdictions — a mention is a hit regardless of list state.
EMBARGOED = ("iran", "north korea", "dprk", "cuba", "syria", "crimea")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", name.lower()).strip()


def _tokens(name: str) -> set[str]:
    return {t for t in _norm(name).split() if len(t) > 2}


# ---- sanctions -------------------------------------------------------------

def screen_sanctions(db: Session, org_id: str, name: str) -> dict:
    now = utcnow()
    checked = {"checked_at": now.isoformat(), "name": name}

    # Embargoed-jurisdiction mention is a hit independent of list freshness.
    low = _norm(name)
    embargo = [j for j in EMBARGOED if j in low]
    if embargo:
        return {**checked, "status": "hit", "matches": [
            {"kind": "embargo", "name": j.title(), "programs": "comprehensive embargo"} for j in embargo
        ]}

    newest = db.query(func.max(SanctionsListEntry.refreshed_at)).filter(
        SanctionsListEntry.org_id == org_id).scalar()
    if newest is None or (now - newest) > STALE_AFTER:
        return {**checked, "status": "unavailable", "matches": [],
                "note": "Sanctions list empty or stale (>30d) — treat as unscreened, not clear."}

    q_tokens = _tokens(name)
    if not q_tokens:
        return {**checked, "status": "unavailable", "matches": [], "note": "No screenable name."}

    # Narrow with ILIKE on the longest token, then token-overlap score in app code.
    anchor = max(q_tokens, key=len)
    candidates = (db.query(SanctionsListEntry)
                  .filter(SanctionsListEntry.org_id == org_id,
                          SanctionsListEntry.name_normalized.ilike(f"%{anchor}%"))
                  .limit(200).all())
    matches = []
    for c in candidates:
        overlap = q_tokens & _tokens(c.name)
        if len(overlap) >= max(1, min(len(q_tokens), 2)):
            matches.append({"kind": "list", "name": c.name, "source": c.source,
                            "programs": c.programs, "ref": c.source_ref})
    return {**checked, "status": "hit" if matches else "clear", "matches": matches[:10]}


def refresh_ofac(db: Session, org_id: str) -> dict:
    """Pull the live Treasury SDN CSV and upsert entries. Returns counts."""
    resp = httpx.get(OFAC_SDN_URL, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    now = utcnow()
    added = updated = 0
    reader = csv.reader(io.StringIO(resp.text))
    existing = {e.source_ref: e for e in db.query(SanctionsListEntry).filter(
        SanctionsListEntry.org_id == org_id, SanctionsListEntry.source == "OFAC_SDN").all()}
    for row in reader:
        if len(row) < 2 or not row[0].strip().isdigit():
            continue
        ref, name = row[0].strip(), row[1].strip()
        if not name or name == "-0-":
            continue
        programs = row[3].strip() if len(row) > 3 and row[3].strip() != "-0-" else None
        e = existing.get(ref)
        if e:
            e.name, e.name_normalized, e.programs, e.refreshed_at = name, _norm(name), programs, now
            updated += 1
        else:
            db.add(SanctionsListEntry(org_id=org_id, source="OFAC_SDN", source_ref=ref,
                                      name=name, name_normalized=_norm(name),
                                      programs=programs, refreshed_at=now))
            added += 1
    db.commit()
    return {"source": "OFAC_SDN", "added": added, "updated": updated, "refreshed_at": now.isoformat()}


# ---- canonical name matching -----------------------------------------------
# Entity suffixes stripped so "Umbrella Corp" ≡ "Umbrella Corporation" ≡ "Umbrella".
_SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|llp|lp|plc|"
    r"gmbh|pbc|sa|nv|bv|ag|pvt|private|group|holdings?|partners?)\b")
# Roles that make THIS request adverse to a matched existing relationship.
ADVERSE_ROLES = {"adverse", "opposing", "defendant", "plaintiff", "claimant", "respondent"}


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


# ---- conflict of interest + relationship -----------------------------------

def conflict_check(db: Session, org_id: str, parties: list[dict],
                   exclude_request_id: str | None = None) -> list[dict]:
    """Screen every party on THIS request against existing relationships —
    contract counterparties, contract parties, and other intake requests — with
    canonical name matching. A party we're ADVERSE to that we already do business
    with is a HIGH-severity conflict; other name matches are flagged for review.
    Each hit carries its `via` linkage (how it matched).

    ponytail: scans org rows in Python (fine single-tenant). Add a normalized-name
    column + index if the portfolio grows past a few thousand.
    """
    contracts = db.query(Contract).filter(Contract.org_id == org_id).all()
    cparties = db.query(ContractParty).filter(ContractParty.org_id == org_id).all()
    reqs = db.query(IntakeRequest).filter(IntakeRequest.org_id == org_id).all()
    by_contract = {c.id: c for c in contracts}

    hits: list[dict] = []
    for p in parties:
        pname = (p.get("name") or "").strip()
        if not pname:
            continue
        prole = (p.get("role") or "counterparty").lower()
        adverse = prole in ADVERSE_ROLES

        for c in contracts:
            if c.counterparty_name and _name_match(pname, c.counterparty_name):
                hits.append({"kind": "contract", "id": c.id, "title": c.title,
                             "party": pname, "role_here": prole, "matched": c.counterparty_name,
                             "via": f"counterparty on “{c.title}”",
                             "severity": "high" if adverse else "review"})
        for cp in cparties:
            if _name_match(pname, cp.name):
                title = by_contract[cp.contract_id].title if cp.contract_id in by_contract else "a contract"
                hits.append({"kind": "contract_party", "id": cp.contract_id, "title": title,
                             "party": pname, "role_here": prole, "matched": cp.name,
                             "via": f"{cp.party_type or 'party'} “{cp.name}” on “{title}”",
                             "severity": "high" if adverse else "review"})
        for rq in reqs:
            if rq.id == exclude_request_id:
                continue
            if any(_name_match(pname, n) for n in request_party_names(rq)):
                hits.append({"kind": "request", "id": rq.id, "ref": rq.ref, "title": rq.type_label,
                             "party": pname, "role_here": prole, "matched": pname,
                             "via": f"party on {rq.ref}", "severity": "review"})

    # dedupe by (kind, id, party); keep the highest severity
    order = {"high": 2, "review": 1}
    best: dict[tuple, dict] = {}
    for h in hits:
        k = (h["kind"], h.get("id"), h["party"])
        if k not in best or order[h["severity"]] > order[best[k]["severity"]]:
            best[k] = h
    out = sorted(best.values(), key=lambda h: -order[h["severity"]])
    return out[:20]


def counterparty_relationship(db: Session, org_id: str, name: str) -> dict:
    """A relationship dossier for the agent draft: prior contracts (count, stage
    mix, total value), prior NDAs, and prior intake requests — canonically matched."""
    contracts = [c for c in db.query(Contract).filter(Contract.org_id == org_id).all()
                 if c.counterparty_name and _name_match(name, c.counterparty_name)]
    ndas = [c for c in contracts if "nda" in (c.title or "").lower()
            or "non-disclosure" in (c.title or "").lower()]
    total_value = sum((c.value_amount or 0) for c in contracts)
    stages: dict[str, int] = {}
    for c in contracts:
        s = c.lifecycle_stage or "unknown"
        stages[s] = stages.get(s, 0) + 1
    reqs = [rq for rq in db.query(IntakeRequest).filter(IntakeRequest.org_id == org_id).all()
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


def compute_screening(db: Session, r: IntakeRequest) -> dict:
    """Pure screening bundle — queries only, no writes. Safe to persist alone.
    Screens EVERY party: sanctions on each (worst reported), conflict-of-interest
    across all, and a relationship dossier on the primary counterparty."""
    parties = gather_parties(r)
    if not parties:
        return {"status": "skipped", "note": "No parties captured on this request.",
                "checked_at": utcnow().isoformat()}
    primary = next((p for p in parties if p["role"] == "counterparty"), parties[0])

    # Sanctions on each party — report the most severe.
    rank = {"hit": 3, "unavailable": 2, "clear": 1}
    per = [(p, screen_sanctions(db, r.org_id, p["name"])) for p in parties]
    worst_party, sanctions = max(per, key=lambda x: rank.get(x[1].get("status"), 0))
    sanctions = {**sanctions, "party": worst_party["name"]}

    return {
        "counterparty": primary["name"],
        "parties": parties,
        "sanctions": sanctions,
        "conflicts": conflict_check(db, r.org_id, parties, exclude_request_id=r.id),
        "relationship": counterparty_relationship(db, r.org_id, primary["name"]),
        "checked_at": utcnow().isoformat(),
        "status": "done",
    }


def run_screening(db: Session, r: IntakeRequest, *, actor_user_id: str | None = None) -> dict:
    """Screen the request, then record a best-effort audit event. The screen and
    the audit are committed in SEPARATE transactions: the screen persists first
    and definitively, so audit-chain advisory-lock contention (write_audit_log
    flushes) can never roll back the screening result. Idempotent commits — the
    callers may commit again harmlessly."""
    result = compute_screening(db, r)
    r.screening = result
    db.commit()          # persist the screen on its own — nothing can lose it now
    db.refresh(r)
    if result.get("status") == "done":
        try:
            write_audit_log(db, action="intake.screening.run", resource_type="intake_request",
                            resource_id=r.id, org_id=r.org_id, actor_user_id=actor_user_id,
                            after={"status": result.get("status"),
                                   "sanctions": (result.get("sanctions") or {}).get("status"),
                                   "conflicts": len(result.get("conflicts") or [])})
            db.commit()
        except Exception:
            db.rollback()  # screen already persisted; only the audit row is lost
    return result


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
