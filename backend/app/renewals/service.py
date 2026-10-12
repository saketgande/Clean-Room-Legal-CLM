import html
import logging
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.schemas import RenewalExtractionOutput
from app.auth.models import User
from app.contracts.lifecycle import transition_contract_stage
from app.contracts.models import Contract
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.core.deadlines import Period, contract_anchors, date_in_quote, explain, shift
from app.core.enums import ContractLifecycleStage, RenewalDecision
from app.integrations.resend import EmailSender, resend_client
from app.renewals.models import RenewalEvent

logger = logging.getLogger(__name__)


def serialize_renewal(row: RenewalEvent, *, contract_title: str | None = None) -> dict:
    """Renewal JSON enriched with the contract title — so the portfolio list
    never has to show a bare UUID or re-fetch a separately-paginated contract
    list to resolve it (that list caps at 100 by default, so a renewal whose
    contract falls outside that page silently lost its title before)."""
    dates = (row.metadata_json or {}).get("dates") or {}
    return {
        "id": row.id,
        "org_id": row.org_id,
        "contract_id": row.contract_id,
        "contract_title": contract_title,
        "contract_version_id": row.contract_version_id,
        "expiration_date": row.expiration_date.isoformat() if row.expiration_date else None,
        "notice_date": row.notice_date.isoformat() if row.notice_date else None,
        "renewal_window_starts_at": (
            row.renewal_window_starts_at.isoformat() if row.renewal_window_starts_at else None
        ),
        "owner_user_id": row.owner_user_id,
        "decision": row.decision,
        "decision_note": row.decision_note,
        # How each date was counted, and whether a person has confirmed it — the
        # page shows these instead of a bare date (metadata_json.dates is the full block).
        "next_expiration_date": dates.get("next_expiration_date"),
        "formulas": dates.get("formulas") or {},
        "date_reason": dates.get("reason"),
        "date_conflict": dates.get("conflict"),
        "dates_status": dates_status(row),
        "metadata_json": row.metadata_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


_ANCHOR_LABELS = {"effective_date": "effective date", "signature_date": "signed", "expiration_date": "expiry"}

# The alert goes out this long before the last day to give notice, so the owner
# has time to decide. Before Step 3d the "window" WAS the deadline: no lead time.
WINDOW_LEAD = Period(30, "days")
UNCONFIRMED_LABEL = "calculated - not yet confirmed"
NEEDS_PERSON = "needs a person"
_DATE_KEYS = ("expiration_date", "notice_date", "window_opens", "next_expiration_date")


def _formula(prefix: str, due: date, **kw) -> str:
    # explain() words a deadline ("due ..."); an expiry or notice date reads better
    # with its own verb in front of the same formula.
    return prefix + explain(due, **kw)[len("due"):]


def _empty_block(facts: RenewalExtractionOutput) -> dict:
    # Citations are kept: a recount checks the term length against the cited wording.
    return {"facts": facts.model_dump(mode="json"),
            **dict.fromkeys(_DATE_KEYS), "formulas": {}, "reason": None, "conflict": None,
            "confirmed_by": None, "confirmed_at": None, "needs_reconfirm": False}


def _from_expiry(facts: RenewalExtractionOutput, expiry: date, block: dict) -> dict:
    """Everything counted from an expiry: the notice deadline (EARLIEST of the
    non-renewal terms, only when it auto-renews), the window that opens before it,
    and the next term's end (shown only; nothing rolls the contract forward)."""
    block["expiration_date"] = expiry.isoformat()
    if facts.auto_renews is None:
        block["reason"] = f"the contract does not say whether it renews automatically - {NEEDS_PERSON}"
        return block
    if not facts.auto_renews:
        return block
    if facts.renewal_term:
        period = Period(facts.renewal_term.number, facts.renewal_term.unit)
        nxt = shift(expiry, period)
        block["next_expiration_date"] = nxt.isoformat()
        block["formulas"]["next_expiration_date"] = _formula(
            "next term ends", nxt, anchor_label="expiry", anchor=expiry, period=period, direction="after")
    candidates = []
    for term in facts.notice_terms:
        # A period the text doesn't state (number None) would come from law: no date.
        if term.kind != "non_renewal" or term.counted != "before_end_of_term" or term.number is None or not term.unit:
            continue
        if not _states_number(term.number, term.quote):
            continue  # number not in its own wording: falls to "needs a person" below
        period = Period(term.number, term.unit)
        due = shift(expiry, period, "before", clear_days=term.clear_days)
        candidates.append((due, _formula("notice by", due, anchor_label="expiry", anchor=expiry,
                                         period=period, direction="before", clear_days=term.clear_days)))
    if not candidates:
        block["reason"] = f"renews automatically but no non-renewal notice period is stated - {NEEDS_PERSON}"
        return block
    due, block["formulas"]["notice_date"] = min(candidates)
    block["notice_date"] = due.isoformat()
    opens = shift(due, WINDOW_LEAD, "before")
    block["window_opens"] = opens.isoformat()
    block["formulas"]["window_opens"] = _formula(
        "window opens", opens, anchor_label="notice by", anchor=due, period=WINDOW_LEAD, direction="before")
    return block


def _states_number(number: int, quote: str | None) -> bool:
    # The same check obligations and notices use: a period whose number isn't in the
    # cited wording would come from law (or the model), not from this contract.
    from app.obligations.service import _states_number as states

    return bool(quote) and states(number, quote)


def _quoted(facts: RenewalExtractionOutput) -> str:
    """All the wording the reading cites, for checking a term length against."""
    parts = [c.quote for c in facts.citations if getattr(c, "quote", None)]
    parts += [facts.stated_end_date_quote or ""] + [t.quote or "" for t in facts.notice_terms]
    return " ".join(p for p in parts if p)


# Expiry sources that are a decision, not a reading: a person's date or a
# finished change request's. Code counts from them and never replaces them.
_DECIDED = {"user": "expiry set by a person", "change_request": "expiry set by a change request"}


def renewal_dates(facts: RenewalExtractionOutput, anchors: dict) -> dict:
    """The stored dates block for a renewal: the typed facts, the expiry and the
    notice deadline counted by code with their formulas, or why there is none.

    Expiry: a person's (or a finished change request's) date wins; otherwise the
    text's written end date (only when its quote carries it), else its initial
    term from the anchor it names. When the request form disagrees with
    the text (including a form end date on an evergreen contract) neither is used:
    both are kept and a person decides. The notice deadline is the EARLIEST of the
    non-renewal terms counted back from that expiry, and only when it auto-renews."""
    block = _empty_block(facts)
    held = anchors["expiration_date"]
    if held.source in _DECIDED and held.value is not None:
        expiry, block["formulas"]["expiration_date"] = held.value, _DECIDED[held.source]
        return _from_expiry(facts, expiry, block)
    if held.conflict:
        block["conflict"] = held.conflict
        block["reason"] = f"expiry differs between the form and the contract text - {NEEDS_PERSON}"
        return block
    expiry = None
    if facts.stated_end_date and date_in_quote(facts.stated_end_date, facts.stated_end_date_quote):
        expiry = facts.stated_end_date
        block["formulas"]["expiration_date"] = f"expires {expiry:%a} {expiry.day} {expiry:%b %Y} = stated in the contract"
    elif facts.term_kind != "evergreen" and facts.initial_term:
        anchor = anchors.get(facts.initial_term.counted_from)
        if anchor is not None and anchor.conflict:
            block["conflict"] = anchor.conflict
            block["reason"] = f"{_ANCHOR_LABELS[anchor.name]} differs between the form and the contract text - {NEEDS_PERSON}"
            return block
        if anchor is None or anchor.value is None:
            label = _ANCHOR_LABELS.get(facts.initial_term.counted_from, "start of the term")
            block["reason"] = f"term counted from the {label}, which is not set - {NEEDS_PERSON}"
            return block
        if not _states_number(facts.initial_term.number, _quoted(facts)):
            block["reason"] = f"term length not found in the quoted clause - {NEEDS_PERSON}"
            return block
        period = Period(facts.initial_term.number, facts.initial_term.unit)
        expiry = shift(anchor.value, period)
        block["formulas"]["expiration_date"] = _formula(
            "expires", expiry, anchor_label=_ANCHOR_LABELS[anchor.name], anchor=anchor.value,
            period=period, direction="after")
    if held.source == "form" and held.value is not None and (
        facts.term_kind == "evergreen" or (expiry is not None and expiry != held.value)
    ):
        block["formulas"] = {}
        block["conflict"] = {"form": held.value.isoformat(),
                             "text": expiry.isoformat() if expiry else "continues until terminated"}
        block["reason"] = f"expiry differs between the form and the contract text - {NEEDS_PERSON}"
        return block
    if expiry is None and held.source == "form" and held.value is not None and facts.term_kind != "evergreen":
        # The text states no term and nothing disputes the form's end date: use it, as the
        # obligations counted from the expiry already do, so the two screens agree.
        expiry = held.value
        block["formulas"]["expiration_date"] = (
            f"expires {expiry:%a} {expiry.day} {expiry:%b %Y} = from the request form")
    if expiry is None:
        block["reason"] = ("no end date - continues until terminated" if facts.term_kind == "evergreen"
                           else f"term not stated in the contract - {NEEDS_PERSON}")
        return block
    return _from_expiry(facts, expiry, block)


def dates_status(row: RenewalEvent) -> str | None:
    """One word for the page and the emails: confirmed / needs_reconfirm /
    needs_person / calculated, or None for an event extracted before Step 3d."""
    dates = (row.metadata_json or {}).get("dates")
    if not dates:
        return None
    if dates.get("confirmed_by"):
        return "needs_reconfirm" if dates.get("needs_reconfirm") else "confirmed"
    if dates.get("conflict") or (dates.get("reason") or "").endswith(NEEDS_PERSON):
        return "needs_person"
    return "calculated"


def unconfirmed_note(row: RenewalEvent) -> str:
    """Suffix for a reminder about dates no person has confirmed (owner decision:
    the reminder still goes out, clearly labelled)."""
    return "" if dates_status(row) == "confirmed" else f" ({UNCONFIRMED_LABEL})"


def _iso_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _keep_confirmed(block: dict, old: dict) -> bool:
    """A date a person confirmed is never overwritten. The confirmed values stay
    on the block; when the freshly computed dates differ from what code computed
    at confirmation, the row is flagged needs_reconfirm and the new values are
    kept beside it under `recomputed` so the person sees both."""
    if not old.get("confirmed_by"):
        return False
    fresh = {k: block[k] for k in (*_DATE_KEYS, "formulas")}
    # Compare against what code computed when the person confirmed, not what they
    # typed: a person's own override must not look like an anchor change forever.
    before = old.get("computed") or {k: old.get(k) for k in _DATE_KEYS}
    block.update({k: old.get(k) for k in (*_DATE_KEYS, "formulas", "confirmed_by", "confirmed_at")})
    block["computed"] = before
    changed = any(fresh[k] != before.get(k) for k in ("expiration_date", "notice_date"))
    block["needs_reconfirm"] = changed
    block["recomputed"] = fresh if changed else None
    return True


def _set_columns(event: RenewalEvent, values: dict) -> None:
    event.expiration_date = _iso_date(values.get("expiration_date"))
    event.notice_date = _iso_date(values.get("notice_date"))
    event.renewal_window_starts_at = _iso_date(values.get("window_opens"))


def _fill_contract_expiry(db: Session, contract: Contract, block: dict) -> None:
    """Give the contract one expiry: close_expired_contracts reads the contract's,
    the window sweep the event's. Code writes it (source "computed") only over an
    empty one, its own earlier one, or the metadata model's own arithmetic ("ai").
    A person's, the form's or a change request's date is never overwritten. When
    the dates can no longer be counted (a disputed or missing anchor) code's own
    copy is cleared, so nothing keeps counting from a date nobody stands behind."""
    meta = contract.metadata_json or {}
    sources = meta.get("field_sources") or {}
    source = sources.get("expiration_date")
    new = _iso_date(block.get("expiration_date"))
    if new is None:
        if source != "computed" or contract.expiration_date is None:
            return
        sources = {k: v for k, v in sources.items() if k != "expiration_date"}
    else:
        replaceable = contract.expiration_date is None or source in ("ai", "computed")
        if not replaceable or (contract.expiration_date == new and source == "computed"):
            return
        sources = {**sources, "expiration_date": "computed"}
    contract.expiration_date = new
    contract.metadata_json = {**meta, "field_sources": sources}
    # An obligation counted from the expiry was waiting for (or counting from) it.
    from app.obligations.service import recompute_due_dates

    recompute_due_dates(db, contract)


def _write_dates(db: Session, event: RenewalEvent, contract: Contract, facts: RenewalExtractionOutput) -> dict:
    """Compute the block, keep confirmed dates, fill the event's date columns and
    the contract's empty expiry. Returns the block; the caller stores it."""
    old = (event.metadata_json or {}).get("dates") or {}
    block = renewal_dates(facts, contract_anchors(db, contract))
    # The contract's copy follows the event's columns, which a confirmed row keeps.
    if not _keep_confirmed(block, old):
        _set_columns(event, block)
        _fill_contract_expiry(db, contract, block)
    return block


def _find_event(db: Session, contract: Contract) -> RenewalEvent | None:
    return db.scalar(
        select(RenewalEvent).where(
            RenewalEvent.org_id == contract.org_id,
            RenewalEvent.contract_id == contract.id,
        )
    )


def apply_extracted_renewal(
    db: Session,
    *,
    contract: Contract,
    version,
    output,
    skill_run,
    created_by_user_id: str | None,
    request_id: str | None,
) -> RenewalEvent:
    """Write a renewal_extraction result: the AI's typed wording, with the expiry,
    notice deadline and window computed here. A date a person confirmed is never
    overwritten; when the new facts would give a different date it is flagged
    needs_reconfirm instead. Never commits."""
    renewal = (
        output if isinstance(output, RenewalExtractionOutput)
        else RenewalExtractionOutput.model_validate(output)
    )
    event = _find_event(db, contract)
    if event is None:
        event = RenewalEvent(
            org_id=contract.org_id,
            contract_id=contract.id,
            owner_user_id=contract.owner_user_id,
            created_by_user_id=created_by_user_id,
            metadata_json={},
        )
        db.add(event)
    block = _write_dates(db, event, contract, renewal)
    # Merge, don't replace: notified_at (the window sweep's "already emailed")
    # and anything else other code keeps on the event must survive a re-extraction.
    event.metadata_json = {
        **(event.metadata_json or {}),
        "auto_renewal": renewal.auto_renews,
        "termination_rights_summary": renewal.termination_rights_summary,
        "confidence": renewal.confidence,
        "needs_review": renewal.needs_review,
        "skill_run_id": skill_run.id,
        "citations": [c.model_dump(mode="json") for c in renewal.citations],
        "dates": block,
    }
    event.contract_version_id = version.id if version else event.contract_version_id
    event.updated_by_user_id = created_by_user_id
    db.flush()
    write_audit_log(
        db,
        action="contract.renewal_extracted",
        resource_type="contract",
        resource_id=contract.id,
        org_id=contract.org_id,
        actor_user_id=created_by_user_id,
        request_id=request_id,
        after={
            "expiration_date": str(event.expiration_date),
            "notice_date": str(event.notice_date),
            "skill_run_id": skill_run.id,
        },
    )
    write_timeline_event(
        db,
        org_id=contract.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.renewal_extracted",
        title="Renewal terms extracted",
        actor_user_id=created_by_user_id,
        request_id=request_id,
        skill_run_id=skill_run.id,
        details={
            "expiration_date": str(event.expiration_date),
            "notice_date": str(event.notice_date),
            "needs_review": renewal.needs_review,
        },
    )
    return event


def recompute_renewal_dates(db: Session, contract: Contract) -> None:
    """Re-derive the renewal's code-computed dates after one of the contract's
    anchor dates changed (core/deadlines.recompute_contract_deadlines), from the
    facts stored at extraction. Confirmed dates stay and get needs_reconfirm.
    Never commits."""
    event = _find_event(db, contract)
    facts = ((event.metadata_json or {}).get("dates") or {}).get("facts") if event is not None else None
    if not facts:
        return  # nothing extracted yet, or an event from before code counted dates
    meta = event.metadata_json or {}
    before = {k: meta["dates"].get(k) for k in (*_DATE_KEYS, "needs_reconfirm")}
    block = _write_dates(db, event, contract, RenewalExtractionOutput.model_validate(facts))
    event.metadata_json = {**meta, "dates": block}
    after = {k: block.get(k) for k in before}
    if after == before:
        return
    write_audit_log(
        db,
        action="renewal.dates_recomputed",
        resource_type="renewal_event",
        resource_id=event.id,
        org_id=contract.org_id,
        actor_user_id=contract.updated_by_user_id,
        before=before,
        after=after,
    )
    write_timeline_event(
        db,
        org_id=contract.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="renewal.dates_recomputed",
        title="Renewal dates recalculated" + (" - needs re-confirming" if block["needs_reconfirm"] else ""),
        actor_user_id=contract.updated_by_user_id,
        details={"renewal_event_id": event.id, **after},
    )


class RenewalsService:
    def __init__(self, db: Session, *, resend: EmailSender | None = None):
        self.db = db
        self.resend = resend or resend_client

    def get_recommendation(self, *, row: RenewalEvent, org_id: str) -> dict:
        """Advisory AI suggestion (renew / renegotiate / terminate) + rationale,
        grounded in the contract's facts on file. Never records a decision."""
        db = self.db
        contract = db.get(Contract, row.contract_id)
        if contract is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found for this renewal")
        from app.renewals.recommendation import recommend_renewal

        return recommend_renewal(db, org_id=org_id, renewal=row, contract=contract)

    def decide_renewal(
        self,
        *,
        row: RenewalEvent,
        decision: str,
        note: str | None,
        current_user: User,
        request_id: str | None,
    ) -> RenewalEvent:
        db = self.db
        decision_map = {
            "renew": RenewalDecision.RENEW,
            "terminate": RenewalDecision.TERMINATE,
            "renegotiate": RenewalDecision.RENEGOTIATE,
        }
        row.decision = decision_map[decision]
        row.decision_note = note
        row.updated_by_user_id = current_user.id
        # Deciding clears the renewal-due flag; terminating also closes the contract.
        contract = db.get(Contract, row.contract_id)
        if contract is not None and contract.org_id == current_user.org_id:
            contract.renewal_due = False
            contract.updated_by_user_id = current_user.id
            if (
                decision == "terminate"
                and contract.lifecycle_stage == ContractLifecycleStage.ACTIVE
            ):
                transition_contract_stage(
                    db,
                    contract=contract,
                    to_stage=ContractLifecycleStage.CLOSED,
                    actor_user_id=current_user.id,
                    reason="Renewal decision: terminate",
                    request_id=request_id,
                )
        write_audit_log(
            db,
            action="renewal.decided",
            resource_type="renewal_event",
            resource_id=row.id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            request_id=request_id,
            after={"decision": row.decision, "contract_id": row.contract_id},
        )
        write_timeline_event(
            db,
            org_id=current_user.org_id,
            resource_type="contract",
            resource_id=row.contract_id,
            event_type="renewal.decided",
            title=f"Renewal decision: {decision}",
            actor_user_id=current_user.id,
            request_id=request_id,
            details={"renewal_event_id": row.id, "decision": decision},
        )
        db.commit()
        db.refresh(row)
        return row

    def confirm_dates(
        self,
        *,
        row: RenewalEvent,
        expiration_date: date | None,
        notice_date: date | None,
        current_user: User,
        request_id: str | None,
        no_end_date: bool = False,
    ) -> RenewalEvent:
        """A person confirms the renewal dates shown (the recalculated ones when the
        row needs re-confirming), optionally typing their own expiry or notice date.
        A typed expiry recounts the notice deadline from the stored facts; a typed
        expiry also becomes the contract's expiry as a person-set value."""
        db = self.db
        contract = db.get(Contract, row.contract_id)
        if contract is None or contract.org_id != current_user.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found for this renewal")
        meta = row.metadata_json or {}
        old = meta.get("dates") or {}
        facts = RenewalExtractionOutput.model_validate(old.get("facts") or {})
        shown = old.get("recomputed") or {k: old.get(k) for k in (*_DATE_KEYS, "formulas")}
        # What code computed for these dates, kept so a later anchor change is
        # compared against it rather than against the person's own typing.
        if old.get("confirmed_by") and not old.get("recomputed"):
            computed = old.get("computed") or {k: old.get(k) for k in _DATE_KEYS}
        else:
            computed = {k: shown.get(k) for k in _DATE_KEYS}
        final = {k: shown.get(k) for k in _DATE_KEYS}
        formulas = dict(shown.get("formulas") or {})
        reason = old.get("reason")
        if no_end_date:
            # A person settles "the form's end date is wrong: it runs until terminated".
            # The contract keeps no expiry, person-set, so no recount brings the form's
            # date back, and every date counted from an expiry goes.
            cmeta = contract.metadata_json or {}
            contract.expiration_date = None
            contract.metadata_json = {
                **cmeta, "field_sources": {**(cmeta.get("field_sources") or {}), "expiration_date": "user"}}
            contract.updated_by_user_id = current_user.id
            final = {k: None for k in _DATE_KEYS}
            computed, formulas = dict(final), {}
            reason = "no end date - continues until terminated (set by a person)"
            from app.obligations.service import recompute_due_dates

            recompute_due_dates(db, contract)
        if expiration_date is not None and expiration_date.isoformat() != final["expiration_date"]:
            redo = _from_expiry(facts, expiration_date, _empty_block(facts))
            final = {k: redo[k] for k in _DATE_KEYS}
            # A recount reads this typed expiry back as the contract's person-set
            # date and counts exactly these values; baseline on them, or every
            # later recount would see "changed" against the old figures and nag.
            computed = dict(final)
            formulas = {**redo["formulas"], "expiration_date": "expiry set by a person"}
            reason = redo["reason"]
        if notice_date is not None and notice_date.isoformat() != final["notice_date"]:
            opens = shift(notice_date, WINDOW_LEAD, "before")
            final.update(notice_date=notice_date.isoformat(), window_opens=opens.isoformat())
            formulas.update(
                notice_date="notice date set by a person",
                window_opens=_formula("window opens", opens, anchor_label="notice by", anchor=notice_date,
                                      period=WINDOW_LEAD, direction="before"),
            )
        if not no_end_date and final["expiration_date"] is None and (
            old.get("conflict") or facts.term_kind != "evergreen"
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "There is no expiry to confirm - enter the expiry date",
            )
        before = {k: old.get(k) for k in (*_DATE_KEYS, "confirmed_by")}
        block = {
            **_empty_block(facts), **old, **final, "formulas": formulas, "computed": computed,
            "recomputed": None, "needs_reconfirm": False, "conflict": None,
            # With a date in hand only a still-missing notice period is worth showing.
            "reason": reason if no_end_date or final["expiration_date"] is None or final["notice_date"] is None else None,
            "confirmed_by": current_user.id, "confirmed_at": utcnow().isoformat(),
        }
        row.metadata_json = {**meta, "dates": block}
        _set_columns(row, final)
        row.updated_by_user_id = current_user.id
        if expiration_date is not None and (
            contract.expiration_date != expiration_date
            or ((contract.metadata_json or {}).get("field_sources") or {}).get("expiration_date") != "user"
        ):
            cmeta = contract.metadata_json or {}
            contract.expiration_date = expiration_date
            contract.metadata_json = {
                **cmeta, "field_sources": {**(cmeta.get("field_sources") or {}), "expiration_date": "user"}}
            contract.updated_by_user_id = current_user.id
            from app.obligations.service import recompute_due_dates

            recompute_due_dates(db, contract)
        elif not no_end_date:
            _fill_contract_expiry(db, contract, block)
        write_audit_log(
            db,
            action="renewal.dates_confirmed",
            resource_type="renewal_event",
            resource_id=row.id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            request_id=request_id,
            before=before,
            after={k: block[k] for k in (*_DATE_KEYS, "confirmed_by")},
        )
        write_timeline_event(
            db,
            org_id=current_user.org_id,
            resource_type="contract",
            resource_id=row.contract_id,
            event_type="renewal.dates_confirmed",
            title="Renewal dates confirmed",
            actor_user_id=current_user.id,
            request_id=request_id,
            details={"renewal_event_id": row.id, **{k: block[k] for k in _DATE_KEYS}},
        )
        db.commit()
        db.refresh(row)
        return row

    async def run_window_check(self, *, current_user: User, request_id: str | None) -> dict:
        """Detect contracts whose renewal/notice window has opened and move ACTIVE
        contracts to RENEWAL_DUE, notifying the owner."""
        db = self.db
        today = utcnow().date()
        events = db.scalars(
            select(RenewalEvent).where(RenewalEvent.org_id == current_user.org_id)
        ).all()
        moved = 0
        notify_failed = 0
        for event in events:
            window = event.renewal_window_starts_at or event.notice_date
            if window is None or window > today:
                continue
            contract = db.get(Contract, event.contract_id)
            if (
                contract is None
                or contract.org_id != current_user.org_id
                or contract.lifecycle_stage != ContractLifecycleStage.ACTIVE
                or contract.renewal_due  # already flagged → don't re-notify
            ):
                continue
            # Renewal-due is a flag on the (still ACTIVE) contract, not a stage.
            contract.renewal_due = True
            contract.updated_by_user_id = current_user.id
            owner = db.get(User, event.owner_user_id) if event.owner_user_id else None
            if owner is not None:
                safe_title = html.escape(contract.title or "Untitled contract")
                # The stage transition above is the durable outcome; a notification
                # failure must not roll it back or abort the rest of the sweep.
                try:
                    await self.resend.send_email(
                        to=owner.email,
                        subject=f"Renewal window open: {contract.title}{unconfirmed_note(event)}",
                        html=(
                            f"<p><b>{safe_title}</b> has entered its renewal window "
                            f"(notice date {event.notice_date}, expires {event.expiration_date})"
                            f"{html.escape(unconfirmed_note(event))}.</p>"
                        ),
                    )
                except Exception:
                    logger.exception(
                        "renewal window notification email failed",
                        extra={"renewal_event_id": event.id, "contract_id": contract.id},
                    )
                    notify_failed += 1
            moved += 1
        write_audit_log(
            db,
            action="renewal.window_check_run",
            resource_type="org",
            resource_id=current_user.org_id,
            org_id=current_user.org_id,
            actor_user_id=current_user.id,
            request_id=request_id,
            after={"contracts_moved_to_renewal_due": moved, "notifications_failed": notify_failed},
        )
        db.commit()
        return {"contracts_moved_to_renewal_due": moved, "notifications_failed": notify_failed}
