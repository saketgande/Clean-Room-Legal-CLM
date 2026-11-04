"""What was reviewed and decided on each version of a contract, newest version first.

The risk band only describes the CURRENT text, so once a new version arrives the earlier
review looks like it vanished. This lists it back, tied to the version it was made on:
the playbook review runs with their findings and the decision a person took on each, and the
suggested changes (redlines) with whether they were accepted or rejected. Read-only.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contract_files.models import ContractEdit, ContractVersion
from app.contracts.models import Contract
from app.playbooks.models import PlaybookDecision, PlaybookDeviation, PlaybookRun

_MAX_EDIT_TEXT = 400


def _clip(text: str | None) -> str | None:
    if not text:
        return None
    return text if len(text) <= _MAX_EDIT_TEXT else text[:_MAX_EDIT_TEXT].rstrip() + "…"


def review_history(db: Session, contract: Contract) -> list[dict]:
    versions = list(db.scalars(
        select(ContractVersion)
        .where(ContractVersion.org_id == contract.org_id, ContractVersion.contract_id == contract.id)
        .order_by(ContractVersion.version_number.desc())
    ))
    runs = list(db.scalars(
        select(PlaybookRun)
        .where(PlaybookRun.org_id == contract.org_id, PlaybookRun.contract_id == contract.id)
        .order_by(PlaybookRun.created_at.desc())
    ))
    devs = list(db.scalars(
        select(PlaybookDeviation)
        .where(
            PlaybookDeviation.org_id == contract.org_id,
            PlaybookDeviation.contract_id == contract.id,
            PlaybookDeviation.playbook_run_id.in_([r.id for r in runs] or [""]),
        )
        .order_by(PlaybookDeviation.created_at.asc())
    ))
    decisions: dict[str, tuple[PlaybookDecision, str | None]] = {}
    if devs:
        rows = db.execute(
            select(PlaybookDecision, User.full_name)
            .join(User, User.id == PlaybookDecision.decided_by_user_id, isouter=True)
            .where(
                PlaybookDecision.org_id == contract.org_id,
                PlaybookDecision.playbook_deviation_id.in_([d.id for d in devs]),
            )
            .order_by(PlaybookDecision.created_at.asc())
        ).all()
        for dec, who in rows:  # the latest decision on a finding is the one that stands
            decisions[dec.playbook_deviation_id] = (dec, who)
    edits = list(db.scalars(
        select(ContractEdit)
        .where(ContractEdit.org_id == contract.org_id, ContractEdit.contract_id == contract.id)
        .order_by(ContractEdit.created_at.asc())
    ))

    out = []
    for v in versions:
        v_runs = []
        for r in (x for x in runs if x.contract_version_id == v.id):
            findings = []
            for d in (x for x in devs if x.playbook_run_id == r.id):
                dec = decisions.get(d.id)
                findings.append({
                    "id": d.id,
                    "severity": d.severity,
                    "clause_type": d.clause_type,
                    "issue": d.issue,
                    "status": d.status,
                    "decision": dec[0].decision if dec else None,
                    "rationale": dec[0].rationale if dec else None,
                    "decided_by": dec[1] if dec else None,
                    "decided_at": dec[0].created_at.isoformat() if dec else None,
                })
            v_runs.append({
                "id": r.id,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
                "findings": findings,
            })
        v_edits = [
            {
                "id": e.id,
                "edit_type": e.edit_type,
                "status": e.status,
                "original_text": _clip(e.original_text),
                "replacement_text": _clip(e.replacement_text),
                "rationale": _clip(e.rationale),
            }
            for e in edits if e.contract_version_id == v.id
        ]
        out.append({
            "version_id": v.id,
            "version_number": v.version_number,
            "source": v.source,
            "created_at": v.created_at.isoformat(),
            "is_current": v.id == contract.current_authoritative_version_id,
            "reviews": v_runs,
            "edits": v_edits,
        })
    return out
