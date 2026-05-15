import hashlib
import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.tool_registry import (
    ContractHandleInput,
    FindInContractInput,
    ProjectContractsInput,
    WorkflowRunInput,
    tool_registry,
)
from app.assistant.models import AssistantContractHandle, AssistantToolCall
from app.auth.models import User
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract
from app.core.database import utcnow
from app.core.enums import AssistantToolCallStatus
from app.core.rbac import has_permission
from app.projects.models import ProjectContract
from app.workflows.models import Workflow, WorkflowRun


class ToolRuntime:
    def execute(
        self,
        db: Session,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        user: User,
        session_id: str,
        assistant_run_id: str | None = None,
    ) -> dict[str, Any]:
        spec = tool_registry.get(tool_name)
        if not has_permission(user.permission_values, spec.required_permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {spec.required_permission}")
        validated_input = spec.input_model.model_validate(tool_input)
        call = AssistantToolCall(
            org_id=user.org_id,
            session_id=session_id,
            assistant_run_id=assistant_run_id,
            tool_name=tool_name,
            category=spec.category,
            arguments=validated_input.model_dump(mode="json"),
            status=AssistantToolCallStatus.RUNNING,
            confirmation_required=spec.requires_confirmation,
            idempotency_key=_idempotency_key(tool_name, session_id, validated_input.model_dump(mode="json")),
            output_schema_name=spec.output_model.__name__,
            started_at=utcnow(),
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(call)
        db.flush()
        if spec.requires_confirmation:
            call.status = AssistantToolCallStatus.CONFIRMATION_REQUIRED
            call.finished_at = utcnow()
            db.flush()
            return {"confirmation_required": True, "tool_call_id": call.id}

        try:
            result = self._execute_validated(db, tool_name=tool_name, payload=validated_input, user=user, session_id=session_id)
            call.result = result
            call.status = AssistantToolCallStatus.SUCCEEDED
            call.finished_at = utcnow()
            db.flush()
            return result
        except Exception as exc:
            call.status = AssistantToolCallStatus.FAILED
            call.error_message = str(exc)
            call.finished_at = utcnow()
            db.flush()
            raise

    def _execute_validated(
        self,
        db: Session,
        *,
        tool_name: str,
        payload: Any,
        user: User,
        session_id: str,
    ) -> dict[str, Any]:
        if tool_name == "read_contract":
            return self._read_contract(db, payload=payload, user=user, session_id=session_id)
        if tool_name == "find_in_contract":
            return self._find_in_contract(db, payload=payload, user=user, session_id=session_id)
        if tool_name == "list_project_contracts":
            return self._list_project_contracts(db, payload=payload, user=user)
        if tool_name == "get_contract_status":
            return self._get_contract_status(db, payload=payload, user=user, session_id=session_id)
        if tool_name == "list_workflows":
            return self._list_workflows(db, user=user)
        if tool_name == "run_workflow":
            return self._run_workflow(db, payload=payload, user=user)
        if tool_name in {"generate_contract_docx", "edit_contract", "replicate_contract_version"}:
            return {"status": "queued_for_ai_controller", "tool": tool_name}
        return {"status": "feature_not_enabled", "tool": tool_name}

    def _read_contract(
        self,
        db: Session,
        *,
        payload: ContractHandleInput,
        user: User,
        session_id: str,
    ) -> dict[str, Any]:
        contract = self._resolve_contract(db, payload=payload, user=user, session_id=session_id)
        version = db.get(ContractVersion, contract.current_authoritative_version_id) if contract.current_authoritative_version_id else None
        snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version and version.text_snapshot_id else None
        return {
            "contract_id": contract.id,
            "title": contract.title,
            "lifecycle_stage": contract.lifecycle_stage,
            "text_snapshot_id": snapshot.id if snapshot else None,
            "text_excerpt": (snapshot.text[:8000] if snapshot else ""),
            "text_truncated": bool(snapshot and len(snapshot.text) > 8000),
        }

    def _find_in_contract(
        self,
        db: Session,
        *,
        payload: FindInContractInput,
        user: User,
        session_id: str,
    ) -> dict[str, Any]:
        contract = self._resolve_contract(db, payload=payload, user=user, session_id=session_id)
        version = db.get(ContractVersion, contract.current_authoritative_version_id) if contract.current_authoritative_version_id else None
        snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version and version.text_snapshot_id else None
        text = snapshot.text if snapshot else ""
        matches = []
        lower_text = text.lower()
        query = payload.query.lower()
        start = lower_text.find(query)
        while start >= 0 and len(matches) < 10:
            end = start + len(query)
            excerpt_start = max(0, start - 160)
            excerpt_end = min(len(text), end + 160)
            matches.append({"start_char": start, "end_char": end, "excerpt": text[excerpt_start:excerpt_end]})
            start = lower_text.find(query, end)
        return {"contract_id": contract.id, "query": payload.query, "matches": matches}

    def _list_project_contracts(self, db: Session, *, payload: ProjectContractsInput, user: User) -> dict[str, Any]:
        contract_ids = db.scalars(
            select(ProjectContract.contract_id).where(
                ProjectContract.org_id == user.org_id,
                ProjectContract.project_id == payload.project_id,
            )
        ).all()
        contracts = db.scalars(
            select(Contract).where(Contract.org_id == user.org_id, Contract.id.in_(contract_ids))
        ).all() if contract_ids else []
        return {
            "project_id": payload.project_id,
            "contracts": [
                {
                    "contract_id": contract.id,
                    "title": contract.title,
                    "lifecycle_stage": contract.lifecycle_stage,
                    "risk_level": contract.risk_level,
                }
                for contract in contracts
            ],
        }

    def _get_contract_status(
        self,
        db: Session,
        *,
        payload: ContractHandleInput,
        user: User,
        session_id: str,
    ) -> dict[str, Any]:
        contract = self._resolve_contract(db, payload=payload, user=user, session_id=session_id)
        return {
            "contract_id": contract.id,
            "title": contract.title,
            "lifecycle_stage": contract.lifecycle_stage,
            "risk_level": contract.risk_level,
            "counterparty_name": contract.counterparty_name,
            "current_authoritative_version_id": contract.current_authoritative_version_id,
        }

    def _list_workflows(self, db: Session, *, user: User) -> dict[str, Any]:
        workflows = db.scalars(
            select(Workflow).where(Workflow.org_id == user.org_id, Workflow.deleted_at.is_(None))
        ).all()
        return {"workflows": [{"workflow_id": row.id, "name": row.name, "workflow_type": row.workflow_type} for row in workflows]}

    def _run_workflow(self, db: Session, *, payload: WorkflowRunInput, user: User) -> dict[str, Any]:
        workflow = db.get(Workflow, payload.workflow_id)
        if workflow is None or workflow.org_id != user.org_id or workflow.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
        run = WorkflowRun(
            org_id=user.org_id,
            workflow_id=workflow.id,
            status="queued",
            input_contract_ids=payload.contract_ids,
            input_prompt=payload.prompt,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(run)
        db.flush()
        return {"workflow_run_id": run.id, "status": run.status}

    def _resolve_contract(
        self,
        db: Session,
        *,
        payload: ContractHandleInput,
        user: User,
        session_id: str,
    ) -> Contract:
        contract_id = payload.contract_id
        if payload.contract_handle:
            handle = db.scalar(
                select(AssistantContractHandle).where(
                    AssistantContractHandle.org_id == user.org_id,
                    AssistantContractHandle.session_id == session_id,
                    AssistantContractHandle.handle == payload.contract_handle,
                )
            )
            if handle is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract handle not found")
            contract_id = handle.contract_id
        if not contract_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "contract_id or contract_handle is required")
        contract = db.get(Contract, contract_id)
        if contract is None or contract.org_id != user.org_id or contract.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract not found")
        return contract


def _idempotency_key(tool_name: str, session_id: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{tool_name}:{session_id}:{canonical}".encode("utf-8")).hexdigest()


tool_runtime = ToolRuntime()
