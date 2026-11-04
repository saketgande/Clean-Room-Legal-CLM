"""Trademark clearance case: what the trademark module adds on top of the Legal
Intake request and workflow run that carry a case.

The request owns routing, teams, SLA and the workflow engine owns the steps (see
``app/workflows``); nothing here duplicates them. This service only holds what
those do not: saved similarity searches (with the LTE's risk rating and an Excel
export), the clearance decision, the filing record, the "informed" notifications
for the RACI roles, and a merged activity feed.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core import org_access
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import new_uuid
from app.core.enums import TrademarkDecision, TrademarkSearchPurpose, TrademarkStatus
from app.core.models import AuditLog, ResourceTimelineEvent
from app.core.rbac import has_permission
from app.intake.models import IntakeRequest, IntakeTeam, IntakeTeamMember
from app.trademarks.clearance_models import TrademarkSearchRun
from app.trademarks.models import Trademark
from app.trademarks.schemas import (
    POST_FILING_STAGES,
    ActivityEntry,
    AwaitingAction,
    DecisionRequest,
    FilingRequest,
    PostFilingUpdateRequest,
    ResponseRequest,
    SearchRunUpdate,
    SearchSimilarRequest,
    SearchSimilarResponse,
    TrademarkCase,
)
from app.workflows.models import WorkflowRun, WorkflowStepRun

# Intake Team keys that stand for the RACI roles. A workflow step routes to a
# team by this key (workflows/service.py::_resolve_role_team), so the same keys
# decide who is told about a decision or a filing.
TEAM_LTE = "lte"
TEAM_TM_LEGAL = "tm_legal"
TEAM_TM_HEAD = "tm_head"
TEAM_GC = "gc"

# The flag the wizard sets on the request's answers. The clearance workflow is
# selected by it (see workflows/builtin.py), so no other request is ever matched.
CLEARANCE_MARKER_FIELD = "tm_clearance"

# The examination / opposition response workflow works the same way: its request carries this flag
# (which selects the workflow) and names the mark it is for. Such a request is worked in Trademarks and
# kept out of the Legal Intake tables, like a clearance. app.intake.service holds the same names.
RESPONSE_MARKER_FIELD = "tm_response"
RESPONSE_FOR_FIELD = "tm_response_for"

_LIVE_RUN_STATUSES = ("running", "waiting")


class TrademarkClearanceService:
    def __init__(self, db: Session):
        self.db = db

    # --- saved searches ----------------------------------------------------

    def save_search_run(
        self, *, user: User, request: SearchSimilarRequest, response: SearchSimilarResponse
    ) -> TrademarkSearchRun:
        trademark_id = request.trademark_id
        trademark = self._get_trademark(user.org_id, trademark_id) if trademark_id else None
        purpose = request.purpose or (TrademarkSearchPurpose.DETAILED if trademark_id else None)
        dumped = response.model_dump(mode="json")
        run = TrademarkSearchRun(
            id=new_uuid(),
            org_id=user.org_id,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
            trademark_id=trademark_id,
            query_id=response.query_id,
            purpose=purpose.value if purpose else None,
            query=request.model_dump(mode="json", exclude={"trademark_id", "purpose"}),
            sources=dumped["sources"],
            summary=dumped["summary"],
        )
        self.db.add(run)
        if trademark is not None:
            self._record_event(
                user=user,
                trademark=trademark,
                event_type="trademark.search_run",
                title=f"{(purpose or TrademarkSearchPurpose.DETAILED).value.title()} search run saved",
                details={"search_run_id": run.id, "query_id": response.query_id},
            )
        self.db.commit()
        return run

    def attach_run(self, *, org_id: str, query_id: str | None, trademark_id: str) -> None:
        """Link the wizard's preliminary search to the mark it just created.
        A missing run is not an error: the search may predate saved runs."""
        if not query_id:
            return
        run = self.db.scalar(
            select(TrademarkSearchRun)
            .where(
                TrademarkSearchRun.org_id == org_id,
                TrademarkSearchRun.query_id == query_id,
                TrademarkSearchRun.trademark_id.is_(None),
            )
            .order_by(TrademarkSearchRun.created_at.desc())
        )
        if run is not None:
            run.trademark_id = trademark_id
            run.purpose = TrademarkSearchPurpose.PRELIMINARY.value

    def list_runs(self, *, org_id: str, trademark_id: str) -> list[TrademarkSearchRun]:
        self._get_trademark(org_id, trademark_id)
        return list(
            self.db.scalars(
                select(TrademarkSearchRun)
                .where(TrademarkSearchRun.org_id == org_id, TrademarkSearchRun.trademark_id == trademark_id)
                .order_by(TrademarkSearchRun.created_at.desc())
            ).all()
        )

    def get_run(self, *, org_id: str, run_id: str) -> TrademarkSearchRun:
        run = self.db.get(TrademarkSearchRun, run_id)
        if run is None or run.org_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Search run not found")
        return run

    def update_run(self, *, user: User, run_id: str, payload: SearchRunUpdate) -> TrademarkSearchRun:
        run = self.get_run(org_id=user.org_id, run_id=run_id)
        updates = payload.model_dump(exclude_unset=True)
        if "risk_level" in updates:
            run.risk_level = updates["risk_level"].value if updates["risk_level"] else None
        if "notes" in updates:
            run.notes = (updates["notes"] or "").strip() or None
        run.updated_by_user_id = user.id
        if run.trademark_id:
            trademark = self._get_trademark(user.org_id, run.trademark_id)
            self._record_event(
                user=user,
                trademark=trademark,
                event_type="trademark.search_rated",
                title=f"Search risk rated {run.risk_level or 'cleared'}",
                details={"search_run_id": run.id, "risk_level": run.risk_level},
            )
            self._notify(
                trademark=trademark,
                actor=user,
                user_ids=self._team_user_ids(user.org_id, (TEAM_TM_LEGAL,)),
                event_type="trademark.search_rated",
                subject=f"Search risk rated for {trademark.name}",
                body=f"{trademark.name}: overall risk recorded as {run.risk_level or 'cleared'}.",
            )
        self.db.commit()
        return run

    def export_run_xlsx(self, *, user: User, run_id: str) -> tuple[str, bytes]:
        run = self.get_run(org_id=user.org_id, run_id=run_id)
        if run.trademark_id:
            self._record_event(
                user=user,
                trademark=self._get_trademark(user.org_id, run.trademark_id),
                event_type="trademark.search_exported",
                title=f"Search report exported to Excel ({run.purpose or 'search'})",
                details={"search_run_id": run.id},
            )
            self.db.commit()
        workbook = Workbook()
        overview = workbook.active
        overview.title = "Summary"
        overview.append(["Trademark searched", run.query.get("trademark_name")])
        overview.append(["Description", run.query.get("description") or ""])
        overview.append(["Jurisdictions", ", ".join(run.query.get("jurisdictions") or [])])
        overview.append(["Search type", run.purpose or ""])
        overview.append(["Run on", run.created_at.strftime("%Y-%m-%d %H:%M UTC")])
        overview.append(["Overall risk (LTE)", run.risk_level or "Not rated"])
        overview.append(["LTE notes", run.notes or ""])
        overview.append(["High-risk matches", run.summary.get("high_risk_count", 0)])
        overview.append(["Medium-risk matches", run.summary.get("medium_risk_count", 0)])
        overview.append(["Low-risk matches", run.summary.get("low_risk_count", 0)])
        overview.append(["Recommendation", run.summary.get("recommendation", "")])
        for source_key, source in (run.sources or {}).items():
            sheet = workbook.create_sheet(source_key[:31])
            results = source.get("results") or []
            if not results:
                sheet.append([f"Status: {source.get('status')}", source.get("error_message") or "No results"])
                continue
            columns = list(dict.fromkeys(key for item in results for key in item))
            sheet.append(columns)
            for item in results:
                sheet.append([_cell(item.get(column)) for column in columns])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return f"trademark-search-{run.query_id}.xlsx", buffer.getvalue()

    # --- case read model ---------------------------------------------------

    def trademark_for_request(self, *, org_id: str, request_id: str) -> Trademark:
        """The mark a Legal Intake request carries, so a link to the request can open its case."""
        trademark = self.db.scalar(
            select(Trademark).where(
                Trademark.org_id == org_id,
                Trademark.source_intake_request_id == request_id,
                Trademark.deleted_at.is_(None),
            )
        )
        if trademark is None:
            # An examination / opposition response names the mark it is for.
            request = self.db.get(IntakeRequest, request_id)
            if request is not None and request.org_id == org_id:
                for_id = (request.field_values or {}).get(RESPONSE_FOR_FIELD)
                if isinstance(for_id, str):
                    trademark = self._get_trademark(org_id, for_id)
        if trademark is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No trademark case for this request")
        return trademark

    def responses_for(self, *, org_id: str, trademark_ids: list[str]) -> dict[str, list[TrademarkCase]]:
        """The examination / opposition responses opened on each mark, newest first."""
        if not trademark_ids:
            return {}
        requests = self.db.scalars(
            select(IntakeRequest)
            .where(IntakeRequest.org_id == org_id, IntakeRequest.field_values[RESPONSE_FOR_FIELD].as_string().in_(trademark_ids))
            .order_by(IntakeRequest.submitted_at.desc())
        ).all()
        if not requests:
            return {}
        cases = self.case_summaries(org_id=org_id, request_ids=[r.id for r in requests])
        found: dict[str, list[TrademarkCase]] = {}
        for request in requests:
            found.setdefault(request.field_values[RESPONSE_FOR_FIELD], []).append(cases[request.id])
        return found

    def case_summaries(self, *, org_id: str, request_ids: list[str]) -> dict[str, TrademarkCase]:
        """Request status plus the workflow run's current step, for many requests
        in a handful of queries (used by the portfolio list)."""
        if not request_ids:
            return {}
        db = self.db
        requests = {
            r.id: r
            for r in db.scalars(
                select(IntakeRequest).where(IntakeRequest.org_id == org_id, IntakeRequest.id.in_(request_ids))
            ).all()
        }
        latest_run: dict[str, WorkflowRun] = {}
        for run in db.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.org_id == org_id, WorkflowRun.request_id.in_(request_ids))
            .order_by(WorkflowRun.created_at.desc())
        ).all():
            latest_run.setdefault(run.request_id, run)
        current_step: dict[str, WorkflowStepRun] = {}
        if latest_run:
            run_by_id = {run.id: run for run in latest_run.values()}
            for step in db.scalars(
                select(WorkflowStepRun).where(WorkflowStepRun.flow_run_id.in_(list(run_by_id)))
            ).all():
                run = run_by_id[step.flow_run_id]
                if step.idx == run.current_index:
                    current_step[run.request_id] = step
        user_ids = {s.assignee_user_id for s in current_step.values() if s.assignee_user_id}
        names = self._user_names(user_ids)

        summaries: dict[str, TrademarkCase] = {}
        for request_id, request in requests.items():
            run = latest_run.get(request_id)
            step = current_step.get(request_id)
            steps = list(run.steps or []) if run else []
            finished = run is not None and run.status == "complete"
            step_name = None
            if run is not None:
                step_name = "Complete" if finished else (steps[run.current_index].get("name") if run.current_index < len(steps) else None)
            summaries[request_id] = TrademarkCase(
                request_id=request_id,
                request_ref=request.ref,
                request_status=request.status,
                title=request.subject,
                flow_name=run.flow_name if run else None,
                run_status=run.status if run else None,
                step_index=run.current_index if run else None,
                step_total=len(steps) if run else None,
                step_name=step_name,
                step_status=step.status if step else None,
                assignee_user_id=step.assignee_user_id if step else None,
                assignee_name=names.get(step.assignee_user_id) if step else None,
            )
        return summaries

    def step_counts(self, *, org_id: str, request_ids: list[str]) -> dict[str, int]:
        """How many cases sit at each workflow step ("Complete" once finished)."""
        counts: dict[str, int] = {}
        for case in self.case_summaries(org_id=org_id, request_ids=request_ids).values():
            if case.step_name:
                counts[case.step_name] = counts.get(case.step_name, 0) + 1
        return counts

    def awaiting_action(self, *, user: User, limit: int = 8) -> list[AwaitingAction]:
        """Cases whose current step is waiting on this user: assigned to them, or
        to a team they are an active member of."""
        db = self.db
        team_ids = set(
            db.scalars(
                select(IntakeTeamMember.team_id).where(
                    IntakeTeamMember.user_id == user.id, IntakeTeamMember.active.is_(True)
                )
            ).all()
        )
        rows = db.execute(
            select(Trademark, WorkflowStepRun)
            .join(WorkflowRun, WorkflowRun.request_id == Trademark.source_intake_request_id)
            .join(
                WorkflowStepRun,
                (WorkflowStepRun.flow_run_id == WorkflowRun.id) & (WorkflowStepRun.idx == WorkflowRun.current_index),
            )
            .where(
                Trademark.org_id == user.org_id,
                Trademark.deleted_at.is_(None),
                WorkflowRun.status.in_(_LIVE_RUN_STATUSES),
                WorkflowStepRun.status == "waiting_human",
            )
            .order_by(Trademark.updated_at.desc())
        ).all()
        actions: dict[tuple[str, str | None], AwaitingAction] = {}
        for trademark, step in rows:
            key = (trademark.id, trademark.source_intake_request_id)
            if key in actions or not (step.assignee_user_id == user.id or step.team_id in team_ids):
                continue
            actions[key] = AwaitingAction(
                trademark_id=trademark.id,
                name=trademark.name,
                step_name=step.step_name,
                request_id=trademark.source_intake_request_id,
            )

        # The examination / opposition responses waiting on this user, from the request that carries each.
        response_rows = db.execute(
            select(IntakeRequest, WorkflowStepRun)
            .join(WorkflowRun, WorkflowRun.request_id == IntakeRequest.id)
            .join(
                WorkflowStepRun,
                (WorkflowStepRun.flow_run_id == WorkflowRun.id) & (WorkflowStepRun.idx == WorkflowRun.current_index),
            )
            .where(
                IntakeRequest.org_id == user.org_id,
                IntakeRequest.field_values[RESPONSE_MARKER_FIELD].as_string() == "true",
                WorkflowRun.status.in_(_LIVE_RUN_STATUSES),
                WorkflowStepRun.status == "waiting_human",
            )
            .order_by(IntakeRequest.submitted_at.desc())
        ).all()
        mine = [
            (request, step)
            for request, step in response_rows
            if step.assignee_user_id == user.id or step.team_id in team_ids
        ]
        wanted = [(request.field_values or {}).get(RESPONSE_FOR_FIELD) for request, _ in mine]
        names = {
            trademark.id: trademark.name
            for trademark in db.scalars(
                select(Trademark).where(Trademark.org_id == user.org_id, Trademark.id.in_(wanted or [""]))
            ).all()
        }
        for request, step in mine:
            trademark_id = (request.field_values or {}).get(RESPONSE_FOR_FIELD)
            if trademark_id in names:
                actions[(trademark_id, request.id)] = AwaitingAction(
                    trademark_id=trademark_id, name=names[trademark_id], step_name=step.step_name, request_id=request.id
                )
        return list(actions.values())[:limit]

    # --- decision and filing ----------------------------------------------

    def record_decision(self, *, user: User, trademark_id: str, payload: DecisionRequest) -> Trademark:
        trademark = self._get_trademark(user.org_id, trademark_id)
        request = self._case_request(trademark)
        self._assert_step_owner(user, request)
        conditions = (payload.conditions or "").strip() or None
        if payload.decision == TrademarkDecision.CONDITIONAL and conditions is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Conditions are required for a conditional decision")

        before = {"decision": trademark.decision, "status": trademark.status}
        previous_decision = trademark.decision
        trademark.decision = payload.decision.value
        trademark.decision_conditions = conditions
        trademark.decided_by_user_id = user.id
        trademark.decided_at = datetime.now(UTC)
        trademark.updated_by_user_id = user.id
        # A final rejection closes the mark off; an override of an earlier
        # rejection (the escalation step) reopens it.
        if payload.decision == TrademarkDecision.REJECT and not payload.escalate:
            if trademark.status == TrademarkStatus.DRAFT:
                trademark.status = TrademarkStatus.ABANDONED
        elif previous_decision == TrademarkDecision.REJECT and trademark.status == TrademarkStatus.ABANDONED:
            trademark.status = TrademarkStatus.DRAFT

        # The workflow engine branches on the request's answers, so the escalation
        # step's condition reads these two keys.
        answers = dict(request.field_values or {})
        answers["tm_decision"] = payload.decision.value
        answers["tm_escalate"] = payload.escalate
        request.field_values = answers

        title = f"Decision recorded: {payload.decision.value.title()}" + (" - escalated" if payload.escalate else "")
        self._record_event(
            user=user,
            trademark=trademark,
            event_type="trademark.decision",
            title=title,
            details={"decision": payload.decision.value, "conditions": conditions, "escalate": payload.escalate},
        )
        write_audit_log(
            self.db,
            action="trademark.decision",
            resource_type="trademark",
            resource_id=trademark.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            before=before,
            after={"decision": trademark.decision, "status": trademark.status, "escalate": payload.escalate},
        )
        recipients = [request.requester_user_id, *self._team_user_ids(user.org_id, (TEAM_TM_LEGAL, TEAM_TM_HEAD, TEAM_GC))]
        self._notify(
            trademark=trademark,
            actor=user,
            user_ids=recipients,
            event_type="trademark.decision",
            subject=f"{trademark.name}: {payload.decision.value.title()}",
            body=title + (f". Conditions: {conditions}" if conditions else ""),
        )
        self.db.commit()
        self.db.refresh(trademark)
        return trademark

    def record_filing(self, *, user: User, trademark_id: str, payload: FilingRequest) -> Trademark:
        from app.trademarks.service import _DEFAULT_RENEWAL_CYCLE_DAYS

        trademark = self._get_trademark(user.org_id, trademark_id)
        request = self._case_request(trademark)
        self._assert_step_owner(user, request)
        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nothing to record")

        before = {"application_number": trademark.application_number, "status": trademark.status}
        for field in ("application_number", "filing_form", "portal_reference", "docket_number"):
            if field in updates:
                setattr(trademark, field, (updates[field] or "").strip() or None)
        if "filed_on" in updates:
            trademark.filed_on = updates["filed_on"]
        if trademark.application_number and trademark.filed_on is None:
            trademark.filed_on = datetime.now(UTC)
        if trademark.filed_on is not None:
            if trademark.status == TrademarkStatus.DRAFT:
                trademark.status = TrademarkStatus.FILED
            if trademark.renewal_due_on is None:
                trademark.renewal_due_on = trademark.filed_on + timedelta(days=_DEFAULT_RENEWAL_CYCLE_DAYS)
        trademark.updated_by_user_id = user.id

        title = "Filing recorded" + (f": application {trademark.application_number}" if trademark.application_number else "")
        self._record_event(
            user=user,
            trademark=trademark,
            event_type="trademark.filing",
            title=title,
            details={key: updates[key] if key != "filed_on" else str(updates[key]) for key in updates},
        )
        write_audit_log(
            self.db,
            action="trademark.filing",
            resource_type="trademark",
            resource_id=trademark.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            before=before,
            after={"application_number": trademark.application_number, "status": trademark.status},
        )
        recipients = [request.requester_user_id, *self._team_user_ids(user.org_id, (TEAM_TM_LEGAL, TEAM_TM_HEAD))]
        self._notify(
            trademark=trademark,
            actor=user,
            user_ids=recipients,
            event_type="trademark.filing",
            subject=f"{trademark.name}: filing recorded",
            body=title,
        )
        self.db.commit()
        self.db.refresh(trademark)
        return trademark

    # --- post-filing -------------------------------------------------------

    def log_post_filing_update(self, *, user: User, trademark_id: str, payload: PostFilingUpdateRequest) -> Trademark:
        """The LTE's update on a filed mark: what happened, where it stands now, what is due next. The LTE Lead,
        the TM Legal SPOC and the Business (the requester) are told."""
        trademark = self._get_trademark(user.org_id, trademark_id)
        self._assert_lte(user)
        self._assert_filed(trademark)
        if payload.stage is not None and payload.stage not in POST_FILING_STAGES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That is not a post-filing stage")
        fields = payload.model_dump(exclude_unset=True)

        before = {"status": trademark.status, "next_action_on": _iso(trademark.next_action_on)}
        stage_changed = payload.stage is not None and payload.stage.value != trademark.status
        if payload.stage is not None:
            trademark.status = payload.stage.value
        if "next_action_on" in fields:
            trademark.next_action_on = payload.next_action_on
            note = (payload.next_action_note or "").strip() or None
            trademark.next_action_note = note if payload.next_action_on else None
        trademark.updated_by_user_id = user.id

        title = f"Post-filing update: {payload.note.strip()[:160]}"
        if stage_changed:
            title += f" (now {_titleize(trademark.status)})"
        self._record_event(
            user=user,
            trademark=trademark,
            event_type="trademark.post_filing_update",
            title=title,
            details={
                "note": payload.note.strip(),
                "stage": trademark.status,
                "next_action_on": _iso(trademark.next_action_on),
            },
        )
        write_audit_log(
            self.db,
            action="trademark.post_filing_update",
            resource_type="trademark",
            resource_id=trademark.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            before=before,
            after={"status": trademark.status, "next_action_on": _iso(trademark.next_action_on)},
        )
        self._notify(
            trademark=trademark,
            actor=user,
            user_ids=self._informed_on_updates(trademark, (TEAM_LTE, TEAM_TM_LEGAL)),
            event_type="trademark.post_filing_update",
            subject=f"{trademark.name}: post-filing update",
            body=title,
        )
        self.db.commit()
        self.db.refresh(trademark)
        return trademark

    def open_response(self, *, user: User, trademark_id: str, payload: ResponseRequest) -> Trademark:
        """An examination report or an opposition needs a response: opens the response workflow on its own
        request (LTE Lead accountable, TM Legal SPOC coordinating) and tells the TM Head and the Business."""
        trademark = self._get_trademark(user.org_id, trademark_id)
        self._assert_lte(user)
        self._assert_filed(trademark)
        label = "Examination report" if payload.kind == "examination" else "Opposition"
        context = trademark.filing_context or {}

        lines = [f'{label} received for "{trademark.name}".', payload.summary.strip()]
        if payload.reference:
            lines.append(f"Office reference: {payload.reference.strip()}")
        if payload.due_on:
            lines.append(f"Response due: {payload.due_on.date().isoformat()}")
        business_unit = context.get("business_unit")
        ticket = self.open_flow_request(
            user=user,
            marker=RESPONSE_MARKER_FIELD,
            subject=f"{label} response: {trademark.name}",
            description="\n".join(lines),
            answers={
                "mark": trademark.name,
                RESPONSE_MARKER_FIELD: True,
                RESPONSE_FOR_FIELD: trademark.id,
                "tm_response_kind": payload.kind,
            },
            department=business_unit if isinstance(business_unit, str) else None,
        )

        trademark = self._get_trademark(user.org_id, trademark_id)  # the request's creation committed; reload
        status_after = TrademarkStatus.EXAMINATION if payload.kind == "examination" else TrademarkStatus.OPPOSED
        trademark.status = status_after.value
        if payload.due_on:
            trademark.next_action_on = payload.due_on
            trademark.next_action_note = f"Response to {label.lower()} due"
        trademark.updated_by_user_id = user.id
        title = f"{label} logged; response workflow opened ({ticket.get('ref') or ticket['id']})"
        self._record_event(
            user=user,
            trademark=trademark,
            event_type="trademark.response_opened",
            title=title,
            details={
                "request_id": ticket["id"],
                "kind": payload.kind,
                "reference": payload.reference,
                "due_on": _iso(payload.due_on),
            },
        )
        write_audit_log(
            self.db,
            action="trademark.response_opened",
            resource_type="trademark",
            resource_id=trademark.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            after={"request_id": ticket["id"], "kind": payload.kind, "status": trademark.status},
        )
        self._notify(
            trademark=trademark,
            actor=user,
            user_ids=self._informed_on_updates(trademark, (TEAM_LTE, TEAM_TM_LEGAL, TEAM_TM_HEAD)),
            event_type="trademark.response_opened",
            subject=f"{trademark.name}: {label.lower()} response opened",
            body=title,
        )
        self.db.commit()
        self.db.refresh(trademark)
        return trademark

    def open_flow_request(
        self, *, user: User, marker: str, subject: str, description: str, answers: dict, department: str | None = None
    ) -> dict:
        """Files the Legal Intake request that carries a trademark workflow and starts that workflow on it,
        chosen by `marker` (the answer its criteria test). Delegates to the intake engine rather than
        re-implementing routing, SLA or steps. If no such workflow has been seeded for the org the request
        still exists and can be started from Legal Intake."""
        from app.intake.schemas import RequestCreate
        from app.intake.service import IntakeService
        from app.integrations.claude import run_coro_blocking
        from app.workflows.models import Workflow
        from app.workflows.service import start_flow

        db = self.db
        ticket = IntakeService(db).create_request(
            actor=user,
            payload=RequestCreate(
                type_label="Trademark",
                subject=subject[:200],
                department=department,
                description=description,
                field_values=answers,
                source="api",
            ),
        )
        flow = next(
            (
                f
                for f in db.scalars(
                    select(Workflow)
                    .where(Workflow.org_id == user.org_id, Workflow.enabled.is_(True))
                    .order_by(Workflow.eval_order.asc())
                ).all()
                if (f.criteria or {}).get("field") == marker
            ),
            None,
        )
        if flow is not None:
            request = db.get(IntakeRequest, ticket["id"])
            run_coro_blocking(lambda: start_flow(db, actor=user, request=request, flow=flow))
        return ticket

    # --- activity ----------------------------------------------------------

    def activity(self, *, org_id: str, trademark_id: str) -> list[ActivityEntry]:
        """Trademark events plus the linked request's events (workflow steps,
        routing, escalation), newest first."""
        trademark = self._get_trademark(org_id, trademark_id)
        scopes = [("trademark", trademark.id)]
        request_ids = [trademark.source_intake_request_id] if trademark.source_intake_request_id else []
        # Each examination / opposition response is its own request with its own workflow events.
        request_ids += self.db.scalars(
            select(IntakeRequest.id).where(
                IntakeRequest.org_id == org_id,
                IntakeRequest.field_values[RESPONSE_FOR_FIELD].as_string() == trademark.id,
            )
        ).all()
        scopes += [("intake_request", request_id) for request_id in request_ids]
        events: list[ResourceTimelineEvent] = []
        for resource_type, resource_id in scopes:
            events.extend(
                self.db.scalars(
                    select(ResourceTimelineEvent)
                    .where(
                        ResourceTimelineEvent.org_id == org_id,
                        ResourceTimelineEvent.resource_type == resource_type,
                        ResourceTimelineEvent.resource_id == resource_id,
                    )
                    .order_by(ResourceTimelineEvent.created_at.desc())
                    .limit(200)
                ).all()
            )
        # Trademark events are mirrored onto the request (see _record_event); show each once.
        events = [
            e
            for e in events
            if not (e.resource_type == "intake_request" and (e.details or {}).get("trademark_id") == trademark.id)
        ]
        # The Legal Intake engine records attachments in the tamper-evident audit log, not on the timeline.
        documents = (
            self.db.scalars(
                select(AuditLog)
                .where(
                    AuditLog.org_id == org_id,
                    AuditLog.resource_type == "intake_request",
                    AuditLog.resource_id.in_(request_ids),
                    AuditLog.action == "intake.document.added",
                )
                .order_by(AuditLog.created_at.desc())
                .limit(200)
            ).all()
            if request_ids
            else []
        )
        names = self._user_names(
            {e.created_by_user_id for e in events if e.created_by_user_id}
            | {d.actor_user_id for d in documents if d.actor_user_id}
        )
        entries = [
            ActivityEntry(
                id=event.id,
                event_type=event.event_type,
                title=event.title,
                actor_user_id=event.created_by_user_id,
                actor_name=names.get(event.created_by_user_id),
                created_at=event.created_at,
                source="trademark" if event.resource_type == "trademark" else "request",
            )
            for event in events
        ] + [
            ActivityEntry(
                id=document.id,
                event_type="document.added",
                title=f"Document attached: {(document.after or {}).get('filename', 'file')}",
                actor_user_id=document.actor_user_id,
                actor_name=names.get(document.actor_user_id),
                created_at=document.created_at,
                source="request",
            )
            for document in documents
        ]
        entries.sort(key=lambda entry: entry.created_at, reverse=True)
        return entries[:200]

    # --- helpers -----------------------------------------------------------

    def _get_trademark(self, org_id: str, trademark_id: str) -> Trademark:
        trademark = self.db.get(Trademark, trademark_id)
        if trademark is None or trademark.org_id != org_id or trademark.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Trademark not found")
        return trademark

    def _case_request(self, trademark: Trademark) -> IntakeRequest:
        request = self.db.get(IntakeRequest, trademark.source_intake_request_id) if trademark.source_intake_request_id else None
        if request is None or request.org_id != trademark.org_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This trademark has no clearance case")
        return request

    def _assert_step_owner(self, user: User, request: IntakeRequest) -> None:
        """Only whoever the workflow's current step is waiting on (the assignee, or
        a member of the assigned team) may record the outcome of that step. Org
        admins can always act so a stuck case is never unrecoverable."""
        db = self.db
        run = db.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.request_id == request.id, WorkflowRun.org_id == request.org_id)
            .order_by(WorkflowRun.created_at.desc())
        )
        if run is None or run.status not in _LIVE_RUN_STATUSES:
            raise HTTPException(status.HTTP_409_CONFLICT, "The clearance workflow is not running on this case")
        step = db.scalar(
            select(WorkflowStepRun).where(WorkflowStepRun.flow_run_id == run.id, WorkflowStepRun.idx == run.current_index)
        )
        if step is None or step.status != "waiting_human":
            raise HTTPException(status.HTTP_409_CONFLICT, "The workflow is not waiting on a person right now")
        if step.assignee_user_id == user.id:
            return
        if step.team_id and db.scalar(
            select(IntakeTeamMember.id).where(
                IntakeTeamMember.team_id == step.team_id,
                IntakeTeamMember.user_id == user.id,
                IntakeTeamMember.active.is_(True),
            )
        ):
            return
        if has_permission(org_access.effective_permission_values(db, user=user), "admin_panel:access"):
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This step is assigned to someone else")

    def _assert_lte(self, user: User) -> None:
        """Post-filing updates and response logs are the LTE / Associates' job; an administrator can always act."""
        if user.id in self._team_user_ids(user.org_id, (TEAM_LTE,)):
            return
        if has_permission(org_access.effective_permission_values(self.db, user=user), "admin_panel:access"):
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the LTE team (or an administrator) can log post-filing work")

    def _assert_filed(self, trademark: Trademark) -> None:
        if trademark.filed_on is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Record the filing first: post-filing work starts once the mark is filed"
            )

    def _informed_on_updates(self, trademark: Trademark, team_keys: tuple[str, ...]) -> list[str | None]:
        """The teams named, plus the Business: whoever raised the clearance request."""
        request = (
            self.db.get(IntakeRequest, trademark.source_intake_request_id) if trademark.source_intake_request_id else None
        )
        return [request.requester_user_id if request else None, *self._team_user_ids(trademark.org_id, team_keys)]

    def log_event(self, *, user: User, trademark: Trademark, event_type: str, title: str, details: dict) -> None:
        """Public entry for the trademark service: one audit-log line on the mark and its request."""
        self._record_event(user=user, trademark=trademark, event_type=event_type, title=title, details=details)

    def _record_event(self, *, user: User, trademark: Trademark, event_type: str, title: str, details: dict) -> None:
        """Written on the mark and, when it has one, on its request, so both the
        trademark case page and the Legal Intake ticket show it."""
        write_timeline_event(
            self.db,
            org_id=user.org_id,
            resource_type="trademark",
            resource_id=trademark.id,
            event_type=event_type,
            title=title,
            actor_user_id=user.id,
            details=details,
        )
        if trademark.source_intake_request_id:
            write_timeline_event(
                self.db,
                org_id=user.org_id,
                resource_type="intake_request",
                resource_id=trademark.source_intake_request_id,
                event_type=event_type,
                title=title,
                actor_user_id=user.id,
                details={**details, "trademark_id": trademark.id},
            )

    def _team_user_ids(self, org_id: str, team_keys: tuple[str, ...]) -> list[str]:
        return list(
            self.db.scalars(
                select(IntakeTeamMember.user_id)
                .join(IntakeTeam, IntakeTeam.id == IntakeTeamMember.team_id)
                .where(
                    IntakeTeam.org_id == org_id,
                    IntakeTeam.active.is_(True),
                    IntakeTeam.key.in_(team_keys),
                    IntakeTeamMember.active.is_(True),
                )
            ).all()
        )

    def _notify(
        self, *, trademark: Trademark, actor: User, user_ids: list[str | None], event_type: str, subject: str, body: str
    ) -> None:
        """In-app notification for the RACI roles that are kept informed. Best
        effort, like the intake module's own notifications: never fails the action."""
        from app.notifications.models import Notification

        try:
            for user_id in dict.fromkeys(uid for uid in user_ids if uid and uid != actor.id):
                self.db.add(
                    Notification(
                        org_id=trademark.org_id,
                        user_id=user_id,
                        channel="in_app",
                        event_type=event_type,
                        subject=subject,
                        body=body,
                        status="sent",
                        metadata_json={"trademark_id": trademark.id},
                    )
                )
        except Exception:
            import logging

            logging.getLogger(__name__).debug("clearance notification failed for %s", trademark.id, exc_info=True)

    def _user_names(self, user_ids: set[str | None]) -> dict[str, str]:
        ids = {uid for uid in user_ids if uid}
        if not ids:
            return {}
        return {
            user.id: user.full_name or user.email
            for user in self.db.scalars(select(User).where(User.id.in_(ids))).all()
        }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _titleize(value: str) -> str:
    return value.replace("_", " ").title()


def _cell(value) -> str | int | float | bool | None:
    """Excel cells hold scalars; lists and objects from provider results are
    flattened to readable text."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return json.dumps(value, default=str)
