"""The few facts an approver sees on the request page (spec 009).

Read-only: stored rows only, no AI call, no job, no write. A fact with no stored value is None
(the page shows "Not available"); nothing is estimated or defaulted. Each shown value carries
where it came from: verified (a person or a change request set it), requester-stated (the form
said so) or extracted (read from the document or computed by the app).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contract_brain.clause_taxonomy import canonical_clause_type
from app.contract_brain.models import ClauseExtraction
from app.contracts.models import Contract

PROFILES: dict[str, tuple[str, ...]] = {
    "finance": ("value", "term", "payment_terms", "liability", "renewal"),
    "legal": ("playbook_risk", "indemnity", "termination", "governing_law", "non_standard"),
    "general": ("value", "term", "overall_risk", "top_issues"),
}
LABELS = {
    "value": "Value", "term": "Term", "payment_terms": "Payment terms", "liability": "Liability",
    "renewal": "Renewal", "playbook_risk": "Playbook risk", "indemnity": "Indemnity",
    "termination": "Termination", "governing_law": "Governing law",
    "non_standard": "Non-standard clauses", "overall_risk": "Overall risk", "top_issues": "Top issues",
}
QUOTE_KEYS = frozenset({"payment_terms", "liability", "indemnity", "termination"})
# Which stored clause type each clause-wording fact quotes.
_CLAUSE_FOR = {
    "payment_terms": "payment_terms", "liability": "limitation_of_liability",
    "indemnity": "indemnification", "termination": "term_and_termination",
}
# The card clamps long text and expands it on the client ("Show more"), so the server sends the
# whole finding; these caps are only a safety bound against pathological text.
_SNIPPET_MAX = 1500
_ISSUE_MAX = 2000


def tag_for(contract: Contract, column: str) -> str:
    """verified / requester-stated / extracted, from how the contract column was set. Unknown
    (older contracts with no field_sources) errs towards extracted: it never overstates."""
    sources = (contract.metadata_json or {}).get("field_sources") or {}
    src = sources.get(column)
    if src in ("user", "change_request"):
        return "verified"
    if src == "form":
        return "requester-stated"
    return "extracted"


def snippet(text: str) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= _SNIPPET_MAX:
        return flat
    return flat[: _SNIPPET_MAX - 1].rstrip() + "…"


def request_contract(db: Session, request) -> Contract | None:
    """The request's contract, else its workflow run's (as get_approval_chain reads it)."""
    from app.workflows.service import get_run_for_request

    contract_id = request.contract_id
    if not contract_id:
        run = get_run_for_request(db, request_id=request.id, org_id=request.org_id)
        contract_id = run.contract_id if run else None
    contract = db.get(Contract, contract_id) if contract_id else None
    return contract if contract is not None and contract.org_id == request.org_id else None


def subject_contract(db: Session, approval) -> Contract | None:
    """The contract an approval is about: its own, else its intake request's."""
    contract_id = getattr(approval, "contract_id", None)
    if contract_id:
        contract = db.get(Contract, contract_id)
        return contract if contract is not None and contract.org_id == approval.org_id else None
    request_id = getattr(approval, "intake_request_id", None)
    if not request_id:
        return None
    from app.intake.models import IntakeRequest

    request = db.get(IntakeRequest, request_id)
    if request is None or request.org_id != approval.org_id:
        return None
    return request_contract(db, request)


def open_risk_counts(db: Session, contract: Contract | None) -> dict:
    """Critical and high open findings: the same numbers the page header shows (contract_risk)."""
    if contract is None:
        return {"critical": 0, "high": 0}
    from app.contracts.risk import contract_risk

    counts = contract_risk(db, contract)["counts"]
    return {"critical": counts["critical"], "high": counts["high"]}


def current_clauses(db: Session, contract: Contract) -> dict[str, ClauseExtraction]:
    """First clause per canonical type on the CURRENT version, never a stale or older one."""
    version_id = contract.current_authoritative_version_id
    if not version_id:
        return {}
    rows = db.scalars(
        select(ClauseExtraction).where(
            ClauseExtraction.org_id == contract.org_id,
            ClauseExtraction.contract_id == contract.id,
            ClauseExtraction.contract_version_id == version_id,
            ClauseExtraction.is_stale.is_(False),
        )
    ).all()
    # Offsets first (nulls last), then insertion order.
    rows = sorted(rows, key=lambda c: (c.start_char is None, c.start_char or 0, c.created_at))
    out: dict[str, ClauseExtraction] = {}
    for row in rows:
        out.setdefault(canonical_clause_type(row.clause_type), row)
    return out


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _money(amount, currency) -> str | None:
    if amount is None:
        return None
    text = f"{Decimal(str(amount)):,.2f}"
    return f"{currency} {text}" if currency else text


def _term(eff, exp) -> str | None:
    if eff and exp:
        return f"{eff.isoformat()} to {exp.isoformat()}"
    if eff:
        return f"From {eff.isoformat()}"
    if exp:
        return f"Until {exp.isoformat()}"
    return None


def _fact(key: str, value, source, *, quote: bool | None = None) -> dict:
    if _blank(value):
        value, source = None, None
    return {"key": key, "label": LABELS[key], "value": value, "source": source,
            "quote": key in QUOTE_KEYS if quote is None else quote}


def _driver_lines(drivers: list, limit: int | None) -> str:
    lines = []
    for d in drivers[:limit]:
        issue = " ".join((d.get("issue") or "").split())
        if len(issue) > _ISSUE_MAX:
            issue = issue[:_ISSUE_MAX] + "…"
        lines.append(f"{d['severity']}: {issue}")
    return "\n".join(lines)


def _renewal(db: Session, contract: Contract):
    from app.renewals.models import RenewalEvent

    version_id = contract.current_authoritative_version_id
    if not version_id:
        return None
    event = db.scalars(
        select(RenewalEvent).where(
            RenewalEvent.org_id == contract.org_id,
            RenewalEvent.contract_id == contract.id,
            RenewalEvent.contract_version_id == version_id,
        ).order_by(RenewalEvent.created_at.desc())
    ).first()
    auto = (event.metadata_json or {}).get("auto_renewal") if event else None
    if not isinstance(auto, bool):
        return None
    if not auto:
        return "Does not renew automatically"
    text = "Renews automatically"
    return f"{text}; notice by {event.notice_date.isoformat()}" if event.notice_date else text


def _contract_fact(db: Session, contract: Contract, key: str, clauses: dict, risk: dict) -> dict:
    if key == "value":
        return _fact(key, _money(contract.value_amount, contract.currency),
                     tag_for(contract, "value_amount"))
    if key == "term":
        used = [c for c, d in (("effective_date", contract.effective_date),
                               ("expiration_date", contract.expiration_date)) if d]
        tags = [tag_for(contract, c) for c in used]
        src = next((t for t in tags if t != "verified"), "verified")
        return _fact(key, _term(contract.effective_date, contract.expiration_date), src)
    if key in _CLAUSE_FOR:
        c = clauses.get(_CLAUSE_FOR[key])
        return _fact(key, snippet(c.text) if c else None, "extracted")
    if key == "governing_law":
        if not _blank(contract.jurisdiction):
            return _fact(key, contract.jurisdiction, tag_for(contract, "jurisdiction"), quote=False)
        c = clauses.get("governing_law")
        return _fact(key, snippet(c.text) if c else None, "extracted", quote=True)
    if key == "renewal":
        return _fact(key, _renewal(db, contract), "extracted")
    # Risk-derived facts: unknown band means no review has qualified, so nothing to show.
    if risk["band"] == "unknown":
        return _fact(key, None, None)
    drivers = risk["drivers"]
    if key == "playbook_risk":
        value = f"{risk['counts']['critical']} critical · {risk['counts']['high']} high"
    elif key == "overall_risk":
        value = risk["band"].capitalize()
    elif key == "top_issues":
        value = _driver_lines(drivers, 3) or "No open issues"
    else:  # non_standard
        value = _driver_lines(drivers, None) or "None found"
    return _fact(key, value, "extracted")


def _request_fact(request, key: str) -> dict:
    from app.intake.approval_bridge import IntakeApprovalSubject
    from app.intake.drafting import request_facts

    fv = request.field_values or {}
    stated = "requester-stated"

    def s(name):
        v = fv.get(name)
        return v.strip() if isinstance(v, str) and v.strip() else None

    if key == "value":
        subject = IntakeApprovalSubject(request, type_key=None)
        return _fact(key, _money(subject.value_amount, subject.currency), stated)
    if key == "term":
        facts = request_facts(fv)
        value = _term(facts.get("effective_date"), facts.get("expiration_date"))
        return _fact(key, value or s("term") or s("nda_term"), stated)
    if key == "payment_terms":
        return _fact(key, s("payment_terms"), stated)
    if key == "governing_law":
        return _fact(key, s("governing_law") or s("jurisdiction"), stated)
    if key == "renewal":
        term = s("term")
        if term == "Renews automatically":
            text = term
            if s("renewal_term"):
                text += f", each renewal {s('renewal_term')}"
            if s("notice_days"):
                text += f", {s('notice_days')} notice"
            return _fact(key, text, stated)
        if term == "Fixed end date":
            return _fact(key, "Does not renew automatically", stated)
        if term == "No end date (evergreen)":
            return _fact(key, term, stated)
        return _fact(key, None, None)
    if key == "non_standard":
        return _fact(key, snippet(s("nonstandard")) if s("nonstandard") else None, stated)
    return _fact(key, None, None)


def build_summary(db: Session, *, actor: User, request, approval_request_id: str | None) -> dict:
    """The card's data. A named approval must belong to this request's chain (else 404); with
    none named, the first pending rung is used. A walled contract is a 404, as on its own page."""
    from app.contracts.service import get_contract_for_user
    from app.intake.service import get_approval_chain
    from app.workflows.service import approval_view_for, get_run_for_request

    rungs = get_approval_chain(db, actor=actor, request_id=request.id)
    if approval_request_id is not None:
        if approval_request_id not in {r.get("approval_request_id") for r in rungs}:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found for this request")
        chosen = approval_request_id
    else:
        pending = [r for r in rungs if r.get("status") == "pending" and r.get("approval_request_id")]
        chosen = min(pending, key=lambda r: r["step_order"])["approval_request_id"] if pending else None

    contract = None
    linked = request_contract(db, request)
    if linked is not None:
        contract = get_contract_for_user(db, contract_id=linked.id, user=actor)

    run = get_run_for_request(db, request_id=request.id, org_id=actor.org_id)
    profile = approval_view_for(db, run=run, approval_request_id=chosen)

    if contract is not None:
        from app.contracts.risk import contract_risk

        risk = contract_risk(db, contract)
        clauses = current_clauses(db, contract)
        fields = [_contract_fact(db, contract, k, clauses, risk) for k in PROFILES[profile]]
        counts = {"critical": risk["counts"]["critical"], "high": risk["counts"]["high"]}
    else:
        fields = [_request_fact(request, k) for k in PROFILES[profile]]
        counts = {"critical": 0, "high": 0}
    return {
        "request_id": request.id,
        "contract_id": contract.id if contract else None,
        "approval_request_id": chosen,
        "profile": profile,
        "fields": fields,
        "open_risk": counts,
    }

