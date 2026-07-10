"""Command-console aggregate for the Contracts Hub.

One endpoint returns every operational signal the console renders — action
queue, approvals in flight, deadlines, pipeline vs SLA, risk board, engine
log, register — so the page paints from a single request instead of a
client-side N+1 sweep. Pure reads over data other features already maintain.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalRequest, ApproverGroup
from app.auth.models import User
from app.contract_files.models import ContractEdit, ContractShare
from app.contracts.access import accessible_contract_filter
from app.contracts.lifecycle import parse_stage_slas
from app.contracts.models import Contract, ContractStageHistory
from app.core.models import AuditLog, ResourceTimelineEvent
from app.core.config import settings
from app.core.enums import ApprovalStatus, ContractLifecycleStage
from app.obligations.models import Obligation
from app.playbooks.models import PlaybookDeviation
from app.renewals.models import RenewalEvent

_PRE_APPROVAL = {"intake", "drafting", "review"}

_ENGINE_ACTIONS = {
    "approval.fast_laned": "fast-laned",
    "contract.lifecycle_changed": "stage moved",
    "playbook.redline_created": "redlines proposed",
    "playbook.run_completed": "playbook run finished",
    "contract.text_manually_edited": "document edited",
    "contract.manual_edit_proposed": "redline proposed",
    "contract.edit_accepted": "redline accepted",
    "contract.ai_metadata_extracted": "AI metadata extracted",
    "contract.brain_ingested": "knowledge graph updated",
    "contract.uploaded": "contract uploaded",
    "contract.share_created": "shared with counterparty",
    "approval.requested": "approval requested",
    "approval.decided": "approval decided",
    "approval.step_activated": "next approver activated",
    "assistant.contract_generated": "contract drafted by AI",
}


def _days(a: datetime, b: datetime) -> int:
    return max(0, (b - a).days)


def build_console(db: Session, *, user: User) -> dict:
    now = datetime.now(UTC)
    sla_map = parse_stage_slas(settings.stage_sla_days)

    contracts = db.scalars(
        select(Contract).where(
            Contract.org_id == user.org_id,
            Contract.deleted_at.is_(None),
            accessible_contract_filter(user),
        )
    ).all()
    ids = [c.id for c in contracts]
    titles = {c.id: c.title for c in contracts}

    # ---- stage history: entered-at per contract + per-stage durations ------
    history = db.scalars(
        select(ContractStageHistory)
        .where(ContractStageHistory.contract_id.in_(ids) if ids else False)
        .order_by(ContractStageHistory.contract_id, ContractStageHistory.changed_at)
    ).all() if ids else []
    entered_at: dict[str, datetime] = {}
    by_contract: dict[str, list[ContractStageHistory]] = {}
    for h in history:
        by_contract.setdefault(h.contract_id, []).append(h)
        entered_at[h.contract_id] = h.changed_at  # last row wins (sorted)
    stage_durations: dict[str, list[float]] = {}
    for cid, rows in by_contract.items():
        for i, row in enumerate(rows[:-1]):
            nxt = rows[i + 1]
            d = (nxt.changed_at - row.changed_at).total_seconds() / 86_400
            stage_durations.setdefault(row.to_stage, []).append(d)

    def days_in_stage(c: Contract) -> int:
        return _days(entered_at.get(c.id, c.created_at), now)

    # ---- per-contract open work counts (bulk) -------------------------------
    dev_rows = db.execute(
        select(PlaybookDeviation.contract_id, PlaybookDeviation.severity, func.count())
        .where(
            PlaybookDeviation.contract_id.in_(ids) if ids else False,
            PlaybookDeviation.status.in_(["open", "needs_review"]),
        )
        .group_by(PlaybookDeviation.contract_id, PlaybookDeviation.severity)
    ).all() if ids else []
    issues: dict[str, int] = {}
    high_issues: dict[str, int] = {}
    for cid, sev, n in dev_rows:
        issues[cid] = issues.get(cid, 0) + n
        if sev in ("high", "critical"):
            high_issues[cid] = high_issues.get(cid, 0) + n
    redline_rows = db.execute(
        select(ContractEdit.contract_id, func.count())
        .where(
            ContractEdit.contract_id.in_(ids) if ids else False,
            ContractEdit.status == "proposed",
        )
        .group_by(ContractEdit.contract_id)
    ).all() if ids else []
    redlines = dict(redline_rows)

    # ---- approvals in flight ------------------------------------------------
    pending = db.scalars(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.org_id == user.org_id,
            ApprovalRequest.contract_id.in_(ids) if ids else False,
            ApprovalRequest.status.in_([ApprovalStatus.PENDING, ApprovalStatus.WAITING]),
        )
        .order_by(ApprovalRequest.contract_id, ApprovalRequest.step_order)
    ).all() if ids else []
    group_ids = {r.approver_group_id for r in pending if r.approver_group_id}
    group_names = {
        g.id: g.name
        for g in db.scalars(select(ApproverGroup).where(ApproverGroup.id.in_(group_ids))).all()
    } if group_ids else {}
    chains: dict[str, list[dict]] = {}
    overdue_approvals = 0
    for r in pending:
        overdue = bool(
            r.status == ApprovalStatus.PENDING and r.due_at is not None and r.due_at < now
        )
        if overdue:
            overdue_approvals += 1
        chains.setdefault(r.contract_id, []).append(
            {
                "label": group_names.get(r.approver_group_id)
                or r.approver_role
                or "Approver",
                "status": str(r.status),
                "overdue": overdue,
                "due_at": r.due_at.isoformat() if r.due_at else None,
            }
        )
    approvals_in_flight = [
        {"contract_id": cid, "title": titles.get(cid, ""), "steps": steps}
        for cid, steps in chains.items()
    ]

    # ---- action queue -------------------------------------------------------
    queue: list[dict] = []
    for c in contracts:
        stage = str(c.lifecycle_stage)
        d = days_in_stage(c)
        sla = sla_map.get(stage)
        breached = bool(sla is not None and d > sla)
        n_issues = issues.get(c.id, 0)
        n_red = redlines.get(c.id, 0)
        entry = None
        if stage in _PRE_APPROVAL and (n_issues or n_red or breached):
            entry = {"kind": "resolve", "action": "Resolve"}
        elif stage == "approval" and any(s["overdue"] for s in chains.get(c.id, [])):
            entry = {"kind": "nudge", "action": "Nudge"}
        elif stage == "signature":
            entry = {"kind": "send", "action": "Send"}
        if entry:
            queue.append(
                {
                    **entry,
                    "contract_id": c.id,
                    "title": c.title,
                    "counterparty": c.counterparty_name,
                    "stage": stage,
                    "days_in_stage": d,
                    "sla_days": sla,
                    "sla_breached": breached,
                    "issues": n_issues,
                    "high_issues": high_issues.get(c.id, 0),
                    "redlines": n_red,
                }
            )
    queue.sort(key=lambda q: (not q["sla_breached"], -(q["issues"] + q["redlines"])))
    queue = queue[:8]

    # ---- deadlines: obligations + renewals ----------------------------------
    horizon = (now + timedelta(days=90)).date()
    today = now.date()
    deadlines: list[dict] = []
    if ids:
        obs = db.scalars(
            select(Obligation).where(
                Obligation.contract_id.in_(ids),
                Obligation.deleted_at.is_(None),
                Obligation.due_date.is_not(None),
                Obligation.due_date >= today,
                Obligation.due_date <= horizon,
                Obligation.status.in_(["open", "due_soon", "overdue"]),
            ).order_by(Obligation.due_date).limit(6)
        ).all()
        for o in obs:
            deadlines.append(
                {
                    "kind": "obligation",
                    "what": (o.description or "Obligation")[:90],
                    "contract_id": o.contract_id,
                    "contract_title": titles.get(o.contract_id, ""),
                    "due": o.due_date.isoformat(),
                    "days": (o.due_date - today).days,
                }
            )
        rens = db.scalars(
            select(RenewalEvent).where(
                RenewalEvent.contract_id.in_(ids),
                RenewalEvent.notice_date.is_not(None),
                RenewalEvent.notice_date >= today,
                RenewalEvent.notice_date <= horizon,
            ).order_by(RenewalEvent.notice_date).limit(4)
        ).all()
        for r in rens:
            deadlines.append(
                {
                    "kind": "renewal",
                    "what": "Renewal notice window opens",
                    "contract_id": r.contract_id,
                    "contract_title": titles.get(r.contract_id, ""),
                    "due": r.notice_date.isoformat(),
                    "days": (r.notice_date - today).days,
                }
            )
    for c in contracts:
        exp = getattr(c, "expiration_date", None)
        if exp and today <= exp <= horizon and str(c.lifecycle_stage) in ("active", "signature"):
            deadlines.append(
                {
                    "kind": "expiry",
                    "what": "Contract expires",
                    "contract_id": c.id,
                    "contract_title": c.title,
                    "due": exp.isoformat(),
                    "days": (exp - today).days,
                }
            )
    deadlines.sort(key=lambda d: d["days"])
    deadlines = deadlines[:8]
    obligations_14d = sum(1 for d in deadlines if d["kind"] == "obligation" and d["days"] <= 14)
    total_obligations = db.scalar(
        select(func.count(Obligation.id)).where(Obligation.contract_id.in_(ids))
    ) if ids else 0
    ob_counts = dict(
        db.execute(
            select(Obligation.contract_id, func.count())
            .where(Obligation.contract_id.in_(ids), Obligation.deleted_at.is_(None))
            .group_by(Obligation.contract_id)
        ).all()
    ) if ids else {}

    # ---- pipeline ------------------------------------------------------------
    order = ["intake", "drafting", "review", "approval", "signature", "active", "closed"]
    pipeline = []
    for stage in order:
        members = [c for c in contracts if str(c.lifecycle_stage) == stage]
        durs = stage_durations.get(stage, [])
        sla = sla_map.get(stage)
        pipeline.append(
            {
                "stage": stage,
                "count": len(members),
                "avg_days": round(sum(durs) / len(durs), 1) if durs else None,
                "sla_days": sla,
                "breached": sum(
                    1 for c in members if sla is not None and days_in_stage(c) > sla
                ),
            }
        )

    # ---- risk board -----------------------------------------------------------
    risk_board = []
    for c in sorted(contracts, key=lambda c: (c.risk_score is None, -(c.risk_score or 0))):
        driver = None
        if c.risk_summary and isinstance(c.risk_summary, dict):
            drivers = c.risk_summary.get("drivers") or []
            top = next((d for d in drivers if d.get("risk") in ("high", "medium")), None) or (
                drivers[0] if drivers else None
            )
            if top:
                driver = top.get("rationale") or top.get("label")
        risk_board.append(
            {
                "contract_id": c.id,
                "title": c.title,
                "score": c.risk_score,
                "band": c.risk_band,
                "top_driver": (driver or "")[:60] or None,
            }
        )
    risk_board = risk_board[:6]

    # ---- engine log ------------------------------------------------------------
    audits = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.org_id == user.org_id,
            AuditLog.action.in_(list(_ENGINE_ACTIONS.keys())),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(80)
    ).all()
    engine_log = []
    week_ago = now - timedelta(days=7)
    lifecycle_total = lifecycle_auto = 0
    for a in audits:
        meta = a.metadata_json or {}
        reason = (meta.get("reason") or "") if isinstance(meta, dict) else ""
        auto = a.actor_user_id is None or a.action == "approval.fast_laned" or reason.startswith(
            ("Fast-lane", "Closed automatically", "Guided")
        )
        if a.action == "contract.lifecycle_changed" and a.created_at >= week_ago:
            lifecycle_total += 1
            if auto:
                lifecycle_auto += 1
        title = titles.get(a.resource_id, "")
        if not title:
            continue  # deleted contracts render as bare labels — skip
        label = _ENGINE_ACTIONS.get(a.action, a.action)
        if a.action == "contract.lifecycle_changed" and isinstance(meta, dict):
            to_stage = (a.after or {}).get("lifecycle_stage") if isinstance(a.after, dict) else None
            if to_stage:
                label = f"moved to {to_stage}"
        engine_log.append(
            {
                "ts": a.created_at.isoformat(),
                "event": f"{label} — {title}",
                "auto": bool(auto),
                "contract_id": a.resource_id,
            }
        )
    engine_log = engine_log[:9]
    automated_pct = (
        round(100 * lifecycle_auto / lifecycle_total) if lifecycle_total else None
    )

    # ---- cycle time (created -> active/closed) ---------------------------------
    cycle_days: list[int] = []
    for cid, rows in by_contract.items():
        act = next((r for r in rows if r.to_stage in ("active", "closed")), None)
        if act:
            c = next((x for x in contracts if x.id == cid), None)
            if c:
                cycle_days.append(_days(c.created_at, act.changed_at))
    cycle_days.sort()
    cycle_median = cycle_days[len(cycle_days) // 2] if cycle_days else None

    # ---- friction ---------------------------------------------------------------
    share_rows = db.execute(
        select(ContractShare.contract_id, func.count())
        .where(ContractShare.contract_id.in_(ids) if ids else False)
        .group_by(ContractShare.contract_id)
    ).all() if ids else []
    shares = dict(share_rows)
    fr: dict[str, dict] = {}
    for c in contracts:
        if not c.counterparty_name or str(c.lifecycle_stage) == "closed":
            continue
        f = fr.setdefault(c.counterparty_name, {"counterparty": c.counterparty_name, "rounds": 0, "contracts": 0})
        f["contracts"] += 1
        f["rounds"] += shares.get(c.id, 0)
    friction = sorted(fr.values(), key=lambda f: -f["rounds"])[:4]

    # ---- register + strip ---------------------------------------------------------
    def _health(c: Contract) -> str:
        stage = str(c.lifecycle_stage)
        breached = bool(
            sla_map.get(stage) is not None and days_in_stage(c) > sla_map[stage]
        )
        if breached:
            return "critical"
        if issues.get(c.id, 0) or redlines.get(c.id, 0) or any(
            st["overdue"] for st in chains.get(c.id, [])
        ):
            return "working"
        if stage in ("active",):
            return "healthy"
        if stage == "closed":
            return "idle"
        return "moving"

    _health_rank = {"critical": 0, "working": 1, "moving": 2, "healthy": 3, "idle": 4}
    register = [
        {
            "contract_id": c.id,
            "title": c.title,
            "stage": str(c.lifecycle_stage),
            "risk_score": c.risk_score,
            "risk_band": c.risk_band,
            "days_in_stage": days_in_stage(c),
            "sla_days": sla_map.get(str(c.lifecycle_stage)),
            "sla_breached": bool(
                sla_map.get(str(c.lifecycle_stage)) is not None
                and days_in_stage(c) > sla_map[str(c.lifecycle_stage)]
            ),
            "health": _health(c),
            "counterparty": c.counterparty_name,
            "value": c.value_amount,
            "issues": issues.get(c.id, 0),
            "redlines": redlines.get(c.id, 0),
            "obligations": ob_counts.get(c.id, 0),
        }
        for c in sorted(
            contracts,
            key=lambda c: (
                _health_rank.get(_health(c), 9),
                -days_in_stage(c),
            ),
        )
    ]

    # ---- triage: ranked, reasoned, actionable ------------------------------
    triage: list[dict] = []
    for c in contracts:
        stage = str(c.lifecycle_stage)
        d = days_in_stage(c)
        sla = sla_map.get(stage)
        breached = bool(sla is not None and d > sla)
        n_issues = issues.get(c.id, 0)
        n_red = redlines.get(c.id, 0)
        overdue_steps = [st for st in chains.get(c.id, []) if st["overdue"]]
        item = None
        if stage == "signature":
            item = {
                "kind": "send",
                "title": f"Send {c.title}",
                "detail": (
                    f"Approved and ready — {d}d in Signature"
                    + (f" vs {sla}d SLA" if breached else "")
                    + ". One click ends it."
                ),
                "reason": ("SLA breach × ready-unsent" if breached else "ready-unsent"),
                "urgency": (d - (sla or 0)) * 3 if breached else 1,
            }
        elif overdue_steps:
            item = {
                "kind": "nudge",
                "title": f"Nudge {overdue_steps[0]['label']} on {c.title}",
                "detail": f"Approval step overdue — waiting since {overdue_steps[0]['due_at'] or 'due date'}.",
                "reason": "approver overdue",
                "urgency": 40,
            }
        elif stage == "intake" and breached:
            item = {
                "kind": "classify",
                "title": f"Clear intake stray: {c.title}",
                "detail": f"{d}d idle in Intake (SLA {sla}d) — classify into the pipeline or archive it.",
                "reason": "SLA breach × no activity",
                "urgency": (d - (sla or 0)),
            }
        elif n_issues or n_red:
            what = []
            if n_issues:
                what.append(f"{n_issues} issues")
            if n_red:
                what.append(f"{n_red} redlines")
            verb = "Work" if n_issues else "Decide"
            item = {
                "kind": "work" if n_issues else "decide",
                "title": f"{verb} {c.title}",
                "detail": " · ".join(what)
                + f" pending · {d}d"
                + (f"/{sla}d" if sla is not None else "")
                + f" in {stage}"
                + (f" · ${int(c.value_amount):,} on the line" if c.value_amount else ""),
                "reason": "blocker volume" if n_issues else "work ready — unblocks stage advance",
                "urgency": 20 + n_issues + n_red + (30 if breached else 0),
            }
        elif breached:
            item = {
                "kind": "move",
                "title": f"Unstick {c.title}",
                "detail": f"{d}d in {stage} vs {sla}d SLA with nothing pending — advance or flag the blocker.",
                "reason": "SLA breach",
                "urgency": d - (sla or 0),
            }
        if item:
            triage.append(
                {
                    **item,
                    "contract_id": c.id,
                    "stage": stage,
                    "sla_breached": breached,
                }
            )
    triage.sort(key=lambda t: -t["urgency"])
    for i, t in enumerate(triage):
        t["rank"] = i + 1
    triage = triage[:6]
    live = [c for c in contracts if str(c.lifecycle_stage) != "closed"]
    strip = {
        "live_value": sum(c.value_amount or 0 for c in live),
        "contracts": len(live),
        "needs_you": len(queue),
        "sla_breaches": sum(1 for q in queue if q["sla_breached"]),
        "approvals_pending": len(approvals_in_flight),
        "approvals_overdue": overdue_approvals,
        "to_sign": sum(1 for c in contracts if str(c.lifecycle_stage) == "signature"),
        "obligations_due_14d": obligations_14d,
        "obligations_total": total_obligations or 0,
        "renewals_90d": sum(1 for d in deadlines if d["kind"] == "renewal"),
        "automated_pct": automated_pct,
        "cycle_median_days": cycle_median,
    }

    timeline = db.scalars(
        select(ResourceTimelineEvent)
        .where(
            ResourceTimelineEvent.org_id == user.org_id,
            ResourceTimelineEvent.resource_type == "contract",
            ResourceTimelineEvent.resource_id.in_(ids) if ids else False,
        )
        .order_by(ResourceTimelineEvent.created_at.desc())
        .limit(8)
    ).all() if ids else []
    recent_activity = [
        {
            "ts": t.created_at.isoformat(),
            "title": t.title,
            "contract_id": t.resource_id,
            "contract_title": titles.get(t.resource_id, ""),
        }
        for t in timeline
    ]

    value_by_stage = []
    for stage in order:
        total = sum(
            c.value_amount or 0 for c in contracts if str(c.lifecycle_stage) == stage
        )
        if total > 0:
            value_by_stage.append({"stage": stage, "value": total})

    return {
        "strip": strip,
        "action_queue": queue,
        "approvals_in_flight": approvals_in_flight,
        "deadlines": deadlines,
        "pipeline": pipeline,
        "risk_board": risk_board,
        "engine_log": engine_log,
        "friction": friction,
        "register": register,
        "triage": triage,
        "recent_activity": recent_activity,
        "value_by_stage": value_by_stage,
    }
