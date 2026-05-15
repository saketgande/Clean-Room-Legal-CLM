import hashlib
import json
from io import BytesIO
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.confirmations import create_confirmation
from app.ai.models import AIConfirmation
from app.ai.tool_registry import (
    ContractHandleInput,
    EditContractInput,
    FindInContractInput,
    GenerateContractInput,
    ProjectContractsInput,
    WorkflowRunInput,
    tool_registry,
)
from app.assistant.models import AssistantContractHandle, AssistantToolCall
from app.auth.models import User
from app.contract_files.models import (
    ContractEdit,
    ContractFile,
    ContractTextSnapshot,
    ContractVersion,
    StorageObject,
)
from app.contract_files.service import next_version_number
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core.database import utcnow
from app.core.enums import (
    AssistantToolCallStatus,
    ContractLifecycleStage,
    ContractVersionSource,
    StorageBackend,
)
from app.core.rbac import has_permission
from app.integrations.storage import storage_service
from app.projects.access import get_project_for_user
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
        provider_tool_use_id: str | None = None,
        provider_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        spec = tool_registry.get(tool_name)
        if not has_permission(user.permission_values, spec.required_permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {spec.required_permission}")
        validated_input = spec.input_model.model_validate(tool_input)
        call = AssistantToolCall(
            org_id=user.org_id,
            session_id=session_id,
            assistant_run_id=assistant_run_id,
            provider_tool_use_id=provider_tool_use_id,
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
            confirmation = create_confirmation(
                db,
                org_id=user.org_id,
                user_id=user.id,
                session_id=session_id,
                assistant_run_id=assistant_run_id or "",
                tool_call=call,
                tool_input=validated_input.model_dump(mode="json"),
                policy={"confirmation_policy": spec.confirmation_policy, "category": spec.category},
                provider_state=provider_state,
            )
            db.flush()
            return {
                "confirmation_required": True,
                "tool_call_id": call.id,
                "confirmation_id": confirmation.id,
            }

        try:
            result = self._execute_validated(
                db,
                tool_name=tool_name,
                payload=validated_input,
                user=user,
                session_id=session_id,
            )
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

    def execute_confirmed(
        self,
        db: Session,
        *,
        confirmation: AIConfirmation,
        user: User,
    ) -> dict[str, Any]:
        call = db.get(AssistantToolCall, confirmation.tool_call_id)
        if call is None or call.org_id != user.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tool call not found")
        spec = tool_registry.get(call.tool_name)
        if not has_permission(user.permission_values, spec.required_permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {spec.required_permission}")
        if call.status not in {AssistantToolCallStatus.CONFIRMED, AssistantToolCallStatus.RUNNING}:
            raise HTTPException(status.HTTP_409_CONFLICT, "Tool call is not confirmed")
        validated_input = spec.input_model.model_validate(call.arguments)
        call.status = AssistantToolCallStatus.RUNNING
        call.started_at = call.started_at or utcnow()
        try:
            result = self._execute_validated(
                db,
                tool_name=call.tool_name,
                payload=validated_input,
                user=user,
                session_id=call.session_id,
            )
            call.result = result
            call.status = AssistantToolCallStatus.SUCCEEDED
            call.finished_at = utcnow()
            call.updated_by_user_id = user.id
            db.flush()
            return result
        except Exception as exc:
            call.status = AssistantToolCallStatus.FAILED
            call.error_message = str(exc)
            call.finished_at = utcnow()
            call.updated_by_user_id = user.id
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
        if tool_name == "generate_contract_docx":
            return self._generate_contract_docx(db, payload=payload, user=user)
        if tool_name == "edit_contract":
            return self._edit_contract(db, payload=payload, user=user, session_id=session_id)
        if tool_name == "replicate_contract_version":
            return self._replicate_contract_version(db, payload=payload, user=user, session_id=session_id)
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
        get_project_for_user(db, project_id=payload.project_id, user=user)
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

    def _generate_contract_docx(
        self,
        db: Session,
        *,
        payload: GenerateContractInput,
        user: User,
    ) -> dict[str, Any]:
        if payload.project_id:
            get_project_for_user(db, project_id=payload.project_id, user=user, access="update")
        body_text = "\n\n".join(
            [
                payload.title,
                "Generated from assistant instructions:",
                payload.instructions,
            ]
        )
        buffer = BytesIO()
        from docx import Document

        document = Document()
        document.add_heading(payload.title, level=1)
        document.add_paragraph(payload.instructions)
        document.save(buffer)
        content = buffer.getvalue()
        stored = storage_service.save_bytes(
            org_id=user.org_id,
            filename=f"{payload.title}.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content=content,
        )
        storage_object = StorageObject(
            org_id=user.org_id,
            storage_key=stored.storage_key,
            filename=stored.filename,
            mime_type=stored.mime_type,
            size_bytes=stored.size_bytes,
            sha256_hash=stored.sha256_hash,
            storage_backend=StorageBackend.LOCAL_VOLUME,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(storage_object)
        db.flush()
        contract = Contract(
            org_id=user.org_id,
            title=payload.title,
            lifecycle_stage=ContractLifecycleStage.DRAFTING,
            owner_user_id=user.id,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(contract)
        db.flush()
        contract_file = ContractFile(
            org_id=user.org_id,
            contract_id=contract.id,
            file_label=stored.filename,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(contract_file)
        db.flush()
        version = ContractVersion(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_file_id=contract_file.id,
            version_number=1,
            storage_object_id=storage_object.id,
            source=ContractVersionSource.ASSISTANT_GENERATED,
            change_summary="Assistant-generated DOCX",
            is_authoritative=True,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(version)
        db.flush()
        snapshot = ContractTextSnapshot(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_version_id=version.id,
            extraction_method="assistant_generated_text",
            extraction_quality_score=1.0,
            text=body_text,
            page_map=None,
            validation_status="complete",
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(snapshot)
        db.flush()
        version.text_snapshot_id = snapshot.id
        contract_file.current_version_id = version.id
        contract.current_contract_file_id = contract_file.id
        contract.current_authoritative_version_id = version.id
        if payload.project_id:
            db.add(
                ProjectContract(
                    org_id=user.org_id,
                    project_id=payload.project_id,
                    contract_id=contract.id,
                    created_by_user_id=user.id,
                    updated_by_user_id=user.id,
                )
            )
        db.flush()
        return {
            "status": "created",
            "contract_id": contract.id,
            "contract_file_id": contract_file.id,
            "contract_version_id": version.id,
            "storage_object_id": storage_object.id,
        }

    def _edit_contract(
        self,
        db: Session,
        *,
        payload: EditContractInput,
        user: User,
        session_id: str,
    ) -> dict[str, Any]:
        contract = self._resolve_contract(db, payload=payload, user=user, session_id=session_id)
        version = db.get(ContractVersion, contract.current_authoritative_version_id) if contract.current_authoritative_version_id else None
        if version is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Current contract version not found")
        contract_file = db.get(ContractFile, version.contract_file_id)
        if contract_file is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract file not found")
        edit = ContractEdit(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_version_id=version.id,
            edit_type="assistant_instruction",
            status="proposed",
            rationale=payload.instructions,
            citation=[],
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(edit)
        db.flush()
        edit_version = ContractVersion(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_file_id=contract_file.id,
            version_number=next_version_number(db, contract_file.id),
            storage_object_id=version.storage_object_id,
            source=ContractVersionSource.ASSISTANT_EDIT,
            change_summary=f"Assistant edit proposal: {payload.instructions[:240]}",
            is_authoritative=False,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(edit_version)
        db.flush()
        source_snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version.text_snapshot_id else None
        if source_snapshot is not None:
            snapshot = ContractTextSnapshot(
                org_id=user.org_id,
                contract_id=contract.id,
                contract_version_id=edit_version.id,
                extraction_method=source_snapshot.extraction_method,
                extraction_quality_score=source_snapshot.extraction_quality_score,
                text=source_snapshot.text + f"\n\n[Assistant proposed edit]\n{payload.instructions}",
                page_map=source_snapshot.page_map,
                ocr_provider=source_snapshot.ocr_provider,
                validation_status=source_snapshot.validation_status,
                created_by_user_id=user.id,
                updated_by_user_id=user.id,
            )
            db.add(snapshot)
            db.flush()
            edit_version.text_snapshot_id = snapshot.id
        db.flush()
        return {
            "status": "created",
            "contract_id": contract.id,
            "base_version_id": version.id,
            "contract_version_id": edit_version.id,
            "contract_edit_id": edit.id,
        }

    def _replicate_contract_version(
        self,
        db: Session,
        *,
        payload: ContractHandleInput,
        user: User,
        session_id: str,
    ) -> dict[str, Any]:
        contract = self._resolve_contract(db, payload=payload, user=user, session_id=session_id)
        version = db.get(ContractVersion, contract.current_authoritative_version_id) if contract.current_authoritative_version_id else None
        if version is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Current contract version not found")
        contract_file = db.get(ContractFile, version.contract_file_id)
        if contract_file is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Contract file not found")
        replicated = ContractVersion(
            org_id=user.org_id,
            contract_id=contract.id,
            contract_file_id=contract_file.id,
            version_number=next_version_number(db, contract_file.id),
            storage_object_id=version.storage_object_id,
            source=ContractVersionSource.ASSISTANT_GENERATED,
            change_summary=f"Replicated from version {version.version_number}",
            is_authoritative=False,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
        )
        db.add(replicated)
        db.flush()
        source_snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version.text_snapshot_id else None
        if source_snapshot is not None:
            snapshot = ContractTextSnapshot(
                org_id=user.org_id,
                contract_id=contract.id,
                contract_version_id=replicated.id,
                extraction_method=source_snapshot.extraction_method,
                extraction_quality_score=source_snapshot.extraction_quality_score,
                text=source_snapshot.text,
                page_map=source_snapshot.page_map,
                ocr_provider=source_snapshot.ocr_provider,
                validation_status=source_snapshot.validation_status,
                created_by_user_id=user.id,
                updated_by_user_id=user.id,
            )
            db.add(snapshot)
            db.flush()
            replicated.text_snapshot_id = snapshot.id
        db.flush()
        return {
            "status": "created",
            "contract_id": contract.id,
            "source_version_id": version.id,
            "contract_version_id": replicated.id,
        }

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
        return get_contract_for_user(db, contract_id=contract_id, user=user)


def _idempotency_key(tool_name: str, session_id: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{tool_name}:{session_id}:{canonical}".encode("utf-8")).hexdigest()


tool_runtime = ToolRuntime()
