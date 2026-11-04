"""The lifecycle rows that are not workflow steps.

The lifecycle view lists a workflow's steps under each stage, but some stages are
mostly made of things that happen elsewhere: Intake is the request being filed,
read and routed; Signature is who signs; Active is obligations and the renewal
window; Closed is the contract ending. This reads those from their own tables so
every stage shows the whole story, not only the workflow's part of it.

Each row: {name, kind, detail, who, status (done|waiting|planned|skipped|failed), when, action}. `action` is
None, or {kind, label, contract_id}: something the viewer may be offered to press on that row (the page decides
whether to show it from the viewer's permission; the server enforces it).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.enums import ObligationStatus, RenewalDecision, SignatureStatus


def _iso(v) -> str | None:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return None


def _row(name, kind, detail, who, status, when=None, action=None) -> dict:
    return {"name": name, "kind": kind, "detail": detail or "", "who": who or "—", "status": status,
            "when": _iso(when), "action": action}


def _user_label(db: Session, uid: str | None) -> str | None:
    from app.auth.models import User

    u = db.get(User, uid) if uid else None
    return (u.full_name or u.email) if u else None


def _intake(db: Session, request, run) -> list[dict]:
    at = request.ai_triage or {}
    fs = at.get("flow_suggestion") or {}
    filed = "Uploaded straight into the CLM" if request.source == "upload" else f"{request.type_label} · via {request.source or 'form'}"
    read = " · ".join(str(x) for x in (at.get("category"), at.get("complexity"),
                                        at.get("risk_flag") and f"{at['risk_flag']} risk") if x)
    triage_status = "waiting" if at.get("status") == "pending" else "done" if at else "skipped"
    owner = _user_label(db, request.assigned_to_user_id)
    rows = [
        _row("Request filed", "Form", filed, request.requester_name or _user_label(db, request.requester_user_id),
             "done", request.submitted_at),
        _row("AI triage", "AI", f"Read as: {read}" if read else ("Reading the request…" if triage_status == "waiting" else "Not run"),
             "Aegis AI", triage_status, request.submitted_at),
        _row("Owner assigned", "Automatic", "Owns the request from here" if owner else "Nobody owns it yet", owner,
             "done" if owner else "waiting", (request.triaged_at or request.submitted_at) if owner else None),
    ]
    if run is not None:
        why = fs.get("reasoning") if fs.get("flow_id") == run.flow_id else None
        rows.append(_row("Workflow chosen", "Automatic", why or f"{run.flow_name} · {len(run.steps or [])} steps",
                         "Aegis", "done", run.created_at))
    else:
        rows.append(_row("Workflow chosen", "Automatic",
                         f"Suggested: {fs['flow_name']} — not started yet" if fs.get("flow_name") else "No workflow yet",
                         "Aegis", "waiting", None))
    return rows


def _signature(db: Session, contract, request) -> list[dict]:
    from app.parties.models import Counterparty, LegalEntity
    from app.signatures.models import SignatureRecipient, SignatureRequest

    sig = db.scalars(
        select(SignatureRequest).where(SignatureRequest.contract_id == contract.id,
                                       SignatureRequest.status != SignatureStatus.VOIDED)
        .order_by(SignatureRequest.created_at.desc())
    ).first()
    if sig is not None:
        rows = [_row("Sent for signature", "DocuSign", f"Envelope {sig.status}", _user_label(db, sig.sent_by_user_id),
                     "done" if sig.sent_at else "waiting", sig.sent_at)]
        for r in db.scalars(select(SignatureRecipient).where(SignatureRecipient.signature_request_id == sig.id)
                            .order_by(SignatureRecipient.routing_order)).all():
            st = (r.status or "").lower()
            rows.append(_row(f"{r.name} signs", "Signer", f"{r.email}{f' · {r.role}' if r.role else ''}", r.name,
                             "done" if st in ("completed", "signed") else "skipped" if st in ("declined", "voided")
                             else "waiting" if sig.sent_at else "planned", sig.completed_at if st == "completed" else None))
        return rows
    cp = db.get(Counterparty, request.counterparty_id) if request is not None and request.counterparty_id else None
    le = db.get(LegalEntity, request.legal_entity_id) if request is not None and request.legal_entity_id else None
    return [
        _row("Counterparty signs", "Signer", (cp.contact_email if cp and cp.contact_email else "From the counterparty record"),
             (cp.name if cp else contract.counterparty_name) or "The other side", "planned"),
        _row("We sign", "Signer", le.name if le else "Authorised signatory on the entity record",
             (le.authorised_signatory if le else None) or "Our authorised signatory", "planned"),
    ]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _extraction_rows(db: Session, contract, live: bool) -> list[dict]:
    """The obligation row of the Active stage, from the one extraction state (app/obligations/extraction_audit.py):
    tracked, none found (a valid answer), extracting, failed or never read (red, with a Retry when a retry can
    help), or from an earlier version. Before the contract is Active nothing is expected yet."""
    from app.obligations.extraction_audit import extraction_state
    from app.obligations.models import Obligation

    open_states = (ObligationStatus.OPEN, ObligationStatus.DUE_SOON, ObligationStatus.OVERDUE)
    ai = "Aegis AI"
    if not live:
        total = db.scalar(select(func.count(Obligation.id)).where(
            Obligation.contract_id == contract.id, Obligation.deleted_at.is_(None))) or 0
        if not total:
            return [_row("Extract obligations", "AI", "Deliverables, payment dates and notice periods from the signed copy",
                         ai, "planned")]
    st = extraction_state(db, contract) if live else None
    n = st.live_obligations if st else total
    if st is None or st.state == "done":
        nxt = db.scalars(
            select(Obligation).where(Obligation.contract_id == contract.id, Obligation.deleted_at.is_(None),
                                     Obligation.status.in_(open_states), Obligation.due_date.is_not(None))
            .order_by(Obligation.due_date)
        ).first()
        detail = _plural(n, "obligation") + (f" · next due {nxt.due_date:%d %b %Y}: {nxt.description[:80]}" if nxt else "")
        return [_row("Obligations tracked", "AI", detail, _user_label(db, contract.owner_user_id), "done")]
    retry = {"kind": "extract_obligations", "label": "Retry", "contract_id": contract.id}
    if st.state == "none_found":
        detail = "The AI read the current copy and found no obligations." + (f" It said: {st.note}" if st.note else "")
        if st.poor_text:
            detail += " The text was read poorly, so check the document."
        return [_row("No obligations found", "AI", detail, ai, "done")]
    if st.state == "running":
        return [_row("Extracting obligations", "AI", "Reading the current copy now.", ai, "waiting")]
    if st.state == "failed":
        tried = f" Tried {_plural(st.attempts, 'time')}" + (f", last on {st.last_failed_at:%d %b %Y}." if st.last_failed_at else ".") if st.attempts else ""
        if not st.retryable:
            # The failure says another try fails the same way and pays again: no button, a person has to look.
            return [_row("Obligation extraction failed", "AI",
                         f"{st.reason}{tried} Trying again would fail the same way, so ask an administrator to look.",
                         ai, "failed", st.last_failed_at)]
        return [_row("Obligation extraction failed", "AI", f"{st.reason}{tried}", ai, "failed", st.last_failed_at, retry)]
    if st.state == "never":
        return [_row("Obligations not read", "AI", st.reason, ai, "failed", None, retry)]
    if st.state == "no_text":
        return [_row("Obligations not read", "AI", st.reason, ai, "failed")]
    # stale: obligations exist, from an earlier version of the document
    return [_row("Obligations come from an earlier version", "AI",
                 f"{_plural(n, 'obligation')} from an earlier copy; the current copy has not been read.", ai, "waiting",
                 None, {**retry, "label": "Re-read"})]


def _active(db: Session, contract) -> list[dict]:
    from app.renewals.models import RenewalEvent

    live = (contract.lifecycle_stage or "") in ("active", "closed")
    rows = _extraction_rows(db, contract, live)
    ren = db.scalars(select(RenewalEvent).where(RenewalEvent.contract_id == contract.id)
                     .order_by(RenewalEvent.created_at.desc())).first()
    expiry = (ren.expiration_date if ren else None) or contract.expiration_date
    if ren is not None and ren.decision != RenewalDecision.UNDECIDED:
        rows.append(_row("Renewal decided", "Renewal", f"Decision: {ren.decision}" + (f" — {ren.decision_note}" if ren.decision_note else ""),
                         _user_label(db, ren.owner_user_id), "done", ren.updated_at))
    elif expiry or ren is not None:
        opens = ren.renewal_window_starts_at if ren else None
        detail = " · ".join(x for x in (f"notice by {ren.notice_date:%d %b %Y}" if ren and ren.notice_date else None,
                                        f"expires {expiry:%d %b %Y}" if expiry else None) if x)
        window_open = opens is not None and opens <= utcnow().date()
        rows.append(_row("Renewal window", "Renewal", f"Decide renew, renegotiate or terminate — {detail}" if detail else "Decide renew, renegotiate or terminate",
                         _user_label(db, (ren.owner_user_id if ren else None) or contract.owner_user_id),
                         "waiting" if window_open else "planned", opens))
    return rows


def _closed(contract) -> list[dict]:
    done = (contract.lifecycle_stage or "") == "closed"
    detail = (f"On expiry {contract.expiration_date:%d %b %Y}, or earlier on termination"
              if contract.expiration_date else "On expiry, termination, or a declined renewal")
    return [_row("Contract ends", "Automatic", detail, "Aegis", "done" if done else "planned", contract.expiration_date),
            _row("Retention", "Records", "Kept read-only and searchable for the retention period", "Legal Ops",
                 "waiting" if done else "planned")]


def lifecycle_rows(db: Session, *, request=None, contract=None, run=None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if request is not None:
        out["intake"] = _intake(db, request, run)
    if contract is not None:
        out["signature"] = _signature(db, contract, request)
        out["active"] = _active(db, contract)
        out["closed"] = _closed(contract)
    return out
