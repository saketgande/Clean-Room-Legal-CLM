"""The contract's ONE risk band, computed in code from its playbook findings.

There used to be a second, AI-judged risk (a weighted average of per-clause
ratings) next to the playbook review. On real contracts the two disagreed on
nearly every one: 11 of 11 reviewed "low" contracts had an open high issue.
Now the band is a pure function of the playbook review of the current text:

- any unresolved high/critical finding -> high; any unresolved medium -> medium;
  only low ones (or none) -> low;
- no qualifying review -> "unknown", with a note saying why. Unknown is never
  low: gates treat it as "a person has to look".

"low" therefore means "no unresolved playbook findings", not "no risk".

There is no numeric score any more (risk_score stays NULL): nothing honest maps
a list of findings onto 0-100. The columns on Contract are a cache for the list
page and search; gates call contract_risk() fresh.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.models import Contract
from app.core.database import utcnow

_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
# Only the playbook's own fallback position closes a finding on the reviewed text.
# accepted / waived are concessions -- the deviating wording stays in the contract,
# so it keeps counting. Otherwise whoever conceded could talk the band down past the
# approver whose authority exists to see conceded risk. rejected / escalated mean we
# pushed back, and the wording still stands. "fixed" (our wording accepted) does not
# close it either: the edit lands in a NEW version, and until then the deviating
# wording is still in this text. Once it lands, this run no longer qualifies (the text
# changed) and the automatic re-review gives the new band.
_RESOLVED = frozenset({"accepted_fallback"})
_CONCEDED = frozenset({"accepted", "waived"})
_MAX_DRIVERS = 8
# A finding a person has not decided yet. needs_review is one whose quote didn't check out:
# as open as "open".
OPEN_STATUSES = ("open", "needs_review")


def _effective_rank(severity: str | None, status: str, unverified: bool) -> int:
    # Same normalisation as playbooks.service._normalize_severity: unknown -> medium.
    rank = _RANK.get((severity or "medium").lower(), 2)
    # An unverified finding (its run's quotes didn't all check out) may be worse
    # than it reads; a person must look before it can make the contract "low".
    if unverified or status == "needs_review":
        rank = max(rank, 2)
    return rank


def band_for(devs: list[tuple[str | None, str, bool]]) -> str:
    """(severity, status, unverified) per finding -> 'low' | 'medium' | 'high'.

    Critical folds into high: medium maps to 0.65 in the workflow AI-review gate,
    which clears its 0.6 threshold, so an open high must never read as medium."""
    worst = 0
    for severity, status, unverified in devs:
        if status in _RESOLVED:
            continue
        worst = max(worst, _effective_rank(severity, status, unverified))
    return "high" if worst >= 3 else "medium" if worst == 2 else "low"


def _unknown_note(rows, version_ids: list[str], playbook_name: str) -> str:
    """Why no review qualifies -- the most specific reason the runs show."""
    if not version_ids:
        return "No text to review yet."
    if not rows:
        return f"Never reviewed against the {playbook_name} playbook."
    same_text = [r for r in rows if r.contract_version_id in version_ids]
    if not same_text:
        return "The text changed since the last review; re-run the playbook review."
    if not any(r.latest for r in same_text):
        return (
            f"The {playbook_name} playbook's rules were re-published since the last "
            "review; re-run the playbook review."
        )
    if any(r.status == "keyword_only" for r in same_text):
        return "Only a keyword scan ran on this text (the AI review did not); re-run the review."
    return "No completed AI review of this text yet."


def _review_runs(db: Session, contract: Contract):
    """(playbook, same-text version ids, every run of this contract against that playbook,
    the runs that qualify). playbook is None when none covers this contract type; the run
    lists are then empty (the version ids are still given). The ONE place that decides which reviews count: the band, the list of
    open findings and the checklist all read it, so they cannot disagree.

    A qualifying review: a succeeded Claude run, on a version with the current
    text (same_text_version_ids), of the playbook this contract type is reviewed
    against, at that playbook's latest PUBLISHED rules. validation_status 'valid'
    or 'needs_review' -- the latter's findings are floored at medium rather than
    dropped, since a run's validation never changes and dropping would leave the
    contract unknown until someone paid for a re-run. Findings of ALL qualifying
    runs count, so re-running on the same text can't fish for a cleaner answer.
    """
    # Local imports: playbooks.service and contracts.service both import widely.
    from app.contracts.service import same_text_version_ids
    from app.playbooks.models import PlaybookRun
    from app.playbooks.service import PlaybooksService, latest_published_version_id

    playbook = PlaybooksService(db).pick_playbook_for_contract(
        org_id=contract.org_id, contract_type=contract.contract_type
    )
    version_ids = same_text_version_ids(db, contract)
    if playbook is None:
        return None, version_ids, [], []
    rows = db.execute(
        select(
            PlaybookRun.id,
            PlaybookRun.contract_version_id,
            PlaybookRun.playbook_version_id,
            PlaybookRun.status,
            PlaybookRun.model_name,
            PlaybookRun.validation_status,
            (PlaybookRun.playbook_version_id == latest_published_version_id()).label("latest"),
        )
        .where(
            PlaybookRun.org_id == contract.org_id,
            PlaybookRun.contract_id == contract.id,
            PlaybookRun.playbook_id == playbook.id,
        )
        .order_by(PlaybookRun.created_at.desc())
    ).all()
    qualifying = [
        r for r in rows
        if r.contract_version_id in version_ids
        and r.latest
        and r.status == "succeeded"
        and r.model_name == "claude"
        and r.validation_status in ("valid", "needs_review")
    ]
    return playbook, version_ids, rows, qualifying


def current_open_findings(db: Session, contract: Contract) -> list:
    """The open playbook findings (open / needs_review) of the review that counts for the
    CURRENT text, oldest first: the findings behind the risk band, not every open row of
    every review ever run. A newer review never closes an older one's rows (only a person's
    decision does), so listing them all showed each unfixed problem twice -- once from the
    old review, once from the new -- next to a header that counted only the new one.

    With no qualifying review (band unknown) the only findings there are come from scans of
    this same text, e.g. a keyword scan after the AI failed, so those are listed; a test run
    against a DRAFT playbook is the author checking rules, not a review of this contract.
    Findings of an earlier text are not listed: that text is gone, and the automatic
    re-check after an accepted change finds what still applies."""
    from app.core.enums import PlaybookStatus
    from app.playbooks.models import PlaybookDeviation, PlaybookRun, PlaybookVersion

    _playbook, version_ids, _rows, qualifying = _review_runs(db, contract)
    if qualifying:
        run_ids = [r.id for r in qualifying]
    elif version_ids:
        run_ids = list(db.scalars(
            select(PlaybookRun.id)
            .join(PlaybookVersion, PlaybookVersion.id == PlaybookRun.playbook_version_id)
            .where(
                PlaybookRun.org_id == contract.org_id,
                PlaybookRun.contract_id == contract.id,
                PlaybookRun.contract_version_id.in_(version_ids),
                PlaybookVersion.status != PlaybookStatus.DRAFT,
            )
        ))
    else:
        run_ids = []
    if not run_ids:
        return []
    return list(db.scalars(
        select(PlaybookDeviation)
        .where(
            PlaybookDeviation.org_id == contract.org_id,
            PlaybookDeviation.contract_id == contract.id,
            PlaybookDeviation.playbook_run_id.in_(run_ids),
            PlaybookDeviation.status.in_(OPEN_STATUSES),
        )
        .order_by(PlaybookDeviation.created_at.asc(), PlaybookDeviation.id.asc())
    ))


def contract_risk(db: Session, contract: Contract) -> dict:
    """The band and the findings behind it, computed now. Read-only. Which reviews
    count is decided by _review_runs."""
    from app.playbooks.models import PlaybookDeviation

    summary: dict = {
        "band": "unknown",
        "counts": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "drivers": [],
        "contract_version_id": contract.current_authoritative_version_id,
        "playbook_id": None,
        "playbook_version_id": None,
        "playbook_run_ids": [],
        "note": None,
        "computed_at": utcnow().isoformat(),
    }
    playbook, version_ids, rows, qualifying = _review_runs(db, contract)
    if playbook is None:
        summary["note"] = "No playbook covers this contract type, so it can't be reviewed yet."
        return summary
    summary["playbook_id"] = playbook.id
    if not qualifying:
        summary["note"] = _unknown_note(rows, version_ids, playbook.name)
        return summary

    unverified_runs = {r.id for r in qualifying if r.validation_status == "needs_review"}
    devs = db.scalars(
        select(PlaybookDeviation).where(
            PlaybookDeviation.org_id == contract.org_id,
            PlaybookDeviation.contract_id == contract.id,
            PlaybookDeviation.playbook_run_id.in_([r.id for r in qualifying]),
        )
    ).all()
    keyed = [(d.severity, d.status, d.playbook_run_id in unverified_runs) for d in devs]
    open_devs: list[tuple[int, PlaybookDeviation]] = []
    for d, (severity, status, unverified) in zip(devs, keyed, strict=True):
        if status in _RESOLVED:
            continue
        open_devs.append((_effective_rank(severity, status, unverified), d))
    by_rank = {rank: name for name, rank in _RANK.items()}
    open_devs.sort(key=lambda pair: pair[0], reverse=True)
    for rank, _d in open_devs:
        summary["counts"][by_rank[rank]] += 1
    summary.update(
        band=band_for(keyed),
        # Severity here is the one the band used (an unverified low shows as medium).
        drivers=[
            {
                "deviation_id": d.id,
                "issue": d.issue,
                "severity": by_rank[rank],
                "status": d.status,
                "conceded": d.status in _CONCEDED,
            }
            for rank, d in open_devs[:_MAX_DRIVERS]
        ],
        playbook_version_id=qualifying[0].playbook_version_id,
        playbook_run_ids=[r.id for r in qualifying],
    )
    return summary


def refresh_contract_risk(db: Session, contract: Contract) -> dict:
    """Recompute and cache the band on the contract. Does NOT commit: it rides the
    caller's transaction. Unknown caches as NULL so the list page never shows a
    stale band as if it were current; risk_level mirrors it, so a hand-set legacy
    badge can't outlive a recompute."""
    summary = contract_risk(db, contract)
    band = None if summary["band"] == "unknown" else summary["band"]
    contract.risk_band = band
    contract.risk_level = band
    contract.risk_score = None
    contract.risk_summary = summary
    return summary
