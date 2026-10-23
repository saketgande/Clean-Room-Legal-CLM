"""Decisions a person already made are not asked for again.

Each playbook review is a fresh AI call that knows nothing about earlier decisions, so after the
text changed somewhere else (an accepted fix) it raised the same problems again on clauses nobody
had touched, and the person had to decide them a second time.

A new finding inherits an earlier decision when ALL of these hold:

- it is about the same clause, unchanged: the finding's quote lands in a block with the same
  content hash as the block the earlier finding's quote landed in (blocks.py hashes the clause
  text, so equal ids mean equal text; one changed word gives a different id);
- it is for the same rule and clause type (a republished playbook has new rule rows, so a changed
  playbook is asked about again, which is intended);
- the earlier decision was accepted, waived, rejected or escalated. "fixed" says our wording went
  in, so the clause the new review sees is no longer the one that was decided. "accepted_fallback"
  is the one decision that takes a finding out of the risk band, so it has to be earned again;
- keeping a high risk still has a reason on record (see decision_problem): a carried decision is
  never a way round the reason that a high or critical risk needs.

The carried decision is a real decision row with the original decider and reason, marked as
carried, so the history shows who decided and that this review inherited it. A carried "accepted"
or "waived" still counts toward the risk band, exactly as it did when it was made.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contract_files.blocks import anchor_quote, split_blocks
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract
from app.core.audit import write_audit_log
from app.playbooks.models import PlaybookDecision, PlaybookDeviation, PlaybookRun

# What a later review may inherit. See the module docstring for why "fixed" and
# "accepted_fallback" are left out.
CARRIED_DECISIONS = ("accepted", "waived", "rejected", "escalated")
CARRIED_NOTE = "Carried over from your earlier decision on this same clause"
# Bounds the work of one review: this many of the contract's most recent decided findings are compared.
_MAX_CANDIDATES = 200


def _quote_of(deviation: PlaybookDeviation) -> str | None:
    citation = deviation.citation if isinstance(deviation.citation, dict) else {}
    quote = (citation.get("quote") or "").strip()
    return quote or None


def _clause_id(blocks, deviation: PlaybookDeviation) -> str | None:
    """The block the finding's quote belongs to, or None when it is not credibly one block
    (no quote, a quote that did not check out, or repeated text that cannot be told apart)."""
    citation = deviation.citation if isinstance(deviation.citation, dict) else {}
    if citation.get("validation_status") not in ("valid", "not_applicable"):
        return None
    return anchor_quote(blocks, _quote_of(deviation))


def _text_of_run(db: Session, run_id: str, cache: dict[str, list]) -> list | None:
    """The clause blocks of the text a run reviewed, cached per run."""
    if run_id not in cache:
        blocks = None
        run = db.get(PlaybookRun, run_id)
        version = db.get(ContractVersion, run.contract_version_id) if run else None
        snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version and version.text_snapshot_id else None
        if snapshot is not None:
            blocks = split_blocks(snapshot.text)
        cache[run_id] = blocks
    return cache[run_id]


def _earlier_decisions(db: Session, *, contract: Contract, run: PlaybookRun):
    """(deviation, its newest decision) of this contract's findings decided earlier, newest first."""
    rows = db.execute(
        select(PlaybookDeviation, PlaybookDecision)
        .join(PlaybookDecision, PlaybookDecision.playbook_deviation_id == PlaybookDeviation.id)
        .where(
            PlaybookDeviation.org_id == contract.org_id,
            PlaybookDeviation.contract_id == contract.id,
            PlaybookDeviation.playbook_run_id != run.id,
            PlaybookDeviation.status.in_(CARRIED_DECISIONS),
            PlaybookDecision.decision == PlaybookDeviation.status,
        )
        .order_by(PlaybookDecision.created_at.desc(), PlaybookDecision.id.desc())
        .limit(_MAX_CANDIDATES)
    ).all()
    return rows


def carry_over_decisions(
    db: Session, *, contract: Contract, run: PlaybookRun, snapshot: ContractTextSnapshot,
    deviations: list[PlaybookDeviation],
) -> list[PlaybookDeviation]:
    """Give each still-open finding of `run` the earlier decision on the same unchanged clause.
    Returns the findings that got one. Adds rows to the caller's transaction; does not commit."""
    from app.playbooks.service import decision_problem

    open_now = [d for d in deviations if d.status == "open"]
    if not open_now:
        return []
    earlier = _earlier_decisions(db, contract=contract, run=run)
    if not earlier:
        return []

    new_blocks = split_blocks(snapshot.text)
    block_cache: dict[str, list | None] = {}
    carried: list[PlaybookDeviation] = []
    for new in open_now:
        new_clause = _clause_id(new_blocks, new)
        if new_clause is None:
            continue
        for old, decision in earlier:
            if (
                (old.clause_type or "").strip().lower() != (new.clause_type or "").strip().lower()
                or old.playbook_rule_id != new.playbook_rule_id
            ):
                continue
            old_blocks = _text_of_run(db, old.playbook_run_id, block_cache)
            if old_blocks is None or _clause_id(old_blocks, old) != new_clause:
                continue
            # The newest matching decision decides. If it cannot be carried (a high risk with no
            # reason on record), an older one must not be used instead.
            if decision_problem(severity=new.severity, decision=decision.decision, rationale=decision.rationale):
                break
            _carry(db, new=new, old=old, decision=decision)
            carried.append(new)
            break
    return carried


def _carry(db: Session, *, new: PlaybookDeviation, old: PlaybookDeviation, decision: PlaybookDecision) -> None:
    reason = (decision.rationale or "").strip()
    note = f'{CARRIED_NOTE} ("{old.issue}").'
    row = PlaybookDecision(
        org_id=new.org_id,
        playbook_deviation_id=new.id,
        decision=decision.decision,
        rationale=f"{note} {reason}".strip(),
        decided_by_user_id=decision.decided_by_user_id,
        created_by_user_id=decision.decided_by_user_id,
        updated_by_user_id=decision.decided_by_user_id,
    )
    new.status = decision.decision
    db.add(row)
    db.flush()
    write_audit_log(
        db,
        action="playbook.deviation_decision_carried",
        resource_type="playbook_deviation",
        resource_id=new.id,
        org_id=new.org_id,
        actor_user_id=decision.decided_by_user_id,
        after={"decision": decision.decision, "carried_from_deviation_id": old.id},
    )
