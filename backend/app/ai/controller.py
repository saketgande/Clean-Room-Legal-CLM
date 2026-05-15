from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.citations import validate_citations
from app.ai.context import ContractAIContext, build_contract_context, list_contract_handles
from app.ai.fallback import fallback_metadata_from_text
from app.ai.models import AIConfirmation, AICitation, AISkillRun
from app.ai.prompt_builder import prompt_builder
from app.ai.prompt_versions import get_active_prompt_bundle
from app.ai.registry import skill_registry
from app.ai.schemas import (
    CitationInput,
    ClauseExtractionOutput,
    ContractEditSuggestionsOutput,
    ContractMetadataOutput,
)
from app.ai.skill import SkillSpec
from app.ai.tool_registry import tool_registry
from app.ai.tool_runtime import tool_runtime
from app.auth.models import User
from app.assistant.models import AssistantContractHandle, AssistantRun, AssistantToolCall
from app.contract_brain.models import ClauseExtraction
from app.contract_files.models import ContractEdit
from app.contracts.models import Contract
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
from app.core.enums import AICallStatus, AISkillRunStatus, AIValidationStatus
from app.core.models import AICallLog, AdminSetting, UsageRecord
from app.core.rbac import has_permission
from app.integrations.claude import ClaudeProviderResponse, claude_client
from app.jobs.models import JobRun


INTERNAL_RESULT_KEYS = {
    "text_snapshot_id",
    "contract_version_id",
    "contract_file_id",
    "storage_object_id",
    "source_version_id",
    "base_version_id",
    "contract_edit_id",
    "current_authoritative_version_id",
    "project_id",
    "workflow_id",
    "workflow_run_id",
    "tool_call_id",
    "confirmation_id",
}


class AIController:
    async def run_job_skill(
        self,
        db: Session,
        *,
        job: JobRun,
        skill_name: str,
        input_payload: dict[str, Any],
    ) -> BaseModel:
        return await self.run_structured_skill(
            db,
            skill_name=skill_name,
            org_id=job.org_id,
            created_by_user_id=job.created_by_user_id,
            input_payload=input_payload,
            resource_type=job.resource_type,
            resource_id=job.resource_id,
            job_id=job.id,
        )

    async def stream_assistant_run(
        self,
        db: Session,
        *,
        user: User,
        org_id: str,
        created_by_user_id: str,
        session_id: str,
        assistant_run_id: str,
        message: str,
        request_id: str | None,
        project_id: str | None = None,
        contract_id: str | None = None,
        contract_ids: list[str] | None = None,
    ):
        spec = skill_registry.get("assistant_streaming")
        model_config = {"temperature": spec.temperature, "max_tokens": spec.max_tokens}
        prompt_bundle = get_active_prompt_bundle(
            db,
            org_id=org_id,
            prompt_key=spec.prompt_key,
            default_version=spec.prompt_version,
            model_config=model_config,
        )
        handles = list_contract_handles(db, session_id=session_id)
        tools = self._assistant_tool_schemas(user=user)
        skill_run = AISkillRun(
            org_id=org_id,
            skill_name=spec.name,
            skill_version=spec.version,
            execution_mode=spec.execution_mode,
            status=AISkillRunStatus.RUNNING,
            resource_type="assistant_session",
            resource_id=session_id,
            session_id=session_id,
            assistant_run_id=assistant_run_id,
            prompt_key=prompt_bundle.prompt_key,
            prompt_version=prompt_bundle.version,
            prompt_hash=prompt_bundle.prompt_hash,
            model=prompt_bundle.model_name,
            model_config_hash=prompt_bundle.model_config_hash,
            input_payload={
                "message": message,
                "project_id": project_id,
                "contract_id": contract_id,
                "contract_ids": contract_ids or [],
                "handles": handles,
            },
            started_at=utcnow(),
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(skill_run)
        db.flush()
        db.commit()

        contract_summaries = self._contract_context_summaries(db, org_id=org_id, handles=handles)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": self._assistant_user_prompt(
                    message=message,
                    project_id=project_id,
                    contract_id=contract_id,
                    contract_ids=contract_ids or [],
                    handles=handles,
                    contract_summaries=contract_summaries,
                ),
            }
        ]
        final_answer_parts: list[str] = []
        tool_results: list[dict[str, Any]] = []

        try:
            for iteration in range(settings.ai_max_tool_iterations):
                provider_response = await claude_client.complete_with_tools(
                    system_prompt=prompt_bundle.shared_system_prompt + "\n\n" + prompt_bundle.skill_prompt,
                    messages=messages,
                    tools=tools,
                    max_tokens=spec.max_tokens,
                    temperature=spec.temperature,
                    model=prompt_bundle.model_name,
                )
                self._log_assistant_ai_call(
                    db,
                    org_id=org_id,
                    created_by_user_id=created_by_user_id,
                    request_id=request_id,
                    skill_run=skill_run,
                    spec=spec,
                    provider_response=provider_response,
                    assistant_run_id=assistant_run_id,
                    session_id=session_id,
                )
                text_delta = "".join(
                    block.get("text", "")
                    for block in provider_response.content_blocks
                    if block.get("type") == "text"
                )
                if not provider_response.tool_use_blocks:
                    if text_delta:
                        final_answer_parts.append(text_delta)
                        yield {"event": "message_delta", "payload": {"text": text_delta}}
                    skill_run.status = AISkillRunStatus.SUCCEEDED
                    skill_run.validation_status = AIValidationStatus.VALID
                    skill_run.output_payload = {
                        "answer": "".join(final_answer_parts),
                        "tool_results": tool_results,
                    }
                    skill_run.finished_at = utcnow()
                    db.commit()
                    return

                if text_delta:
                    final_answer_parts.append(text_delta)
                    yield {"event": "message_delta", "payload": {"text": text_delta}}

                messages.append({"role": "assistant", "content": provider_response.content_blocks})
                tool_result_blocks = []
                for tool_use in provider_response.tool_use_blocks:
                    tool_name = tool_use.get("name")
                    tool_input = tool_use.get("input") or {}
                    yield {
                        "event": "tool_started",
                        "payload": {"tool_name": tool_name, "tool_use_id": tool_use.get("id")},
                    }
                    try:
                        result = tool_runtime.execute(
                            db,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            user=user,
                            session_id=session_id,
                            assistant_run_id=assistant_run_id,
                            provider_tool_use_id=tool_use.get("id"),
                            provider_state={
                                "schema_version": 1,
                                "pending_tool_use": tool_use,
                                "messages": messages,
                                "skill_run_id": skill_run.id,
                            },
                        )
                        db.commit()
                        if result.get("confirmation_required"):
                            run = db.get(AssistantRun, assistant_run_id)
                            if run is not None:
                                run.status = "waiting_confirmation"
                                run.provider_state = {
                                    "schema_version": 1,
                                    "pending_tool_use": tool_use,
                                    "messages": messages,
                                    "skill_run_id": skill_run.id,
                                    "confirmation_id": result.get("confirmation_id"),
                                }
                            skill_run.status = AISkillRunStatus.WAITING_CONFIRMATION
                            skill_run.validation_status = AIValidationStatus.NOT_VALIDATED
                            skill_run.output_payload = {
                                "status": "waiting_confirmation",
                                "tool_name": tool_name,
                                "tool_call_id": result.get("tool_call_id"),
                                "confirmation_id": result.get("confirmation_id"),
                            }
                            skill_run.finished_at = utcnow()
                            db.commit()
                            yield {
                                "event": "confirmation_required",
                                "payload": {
                                    "tool_name": tool_name,
                                    "tool_call_id": result.get("tool_call_id"),
                                    "confirmation_id": result.get("confirmation_id"),
                                    "assistant_run_id": assistant_run_id,
                                },
                            }
                            return
                        tool_results.append({"tool_name": tool_name, "result": result})
                        yield {
                            "event": "tool_finished",
                            "payload": {"tool_name": tool_name, "tool_use_id": tool_use.get("id"), "result": result},
                        }
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.get("id"),
                                "content": self._json_tool_result(
                                    self._model_safe_result(
                                        db,
                                        value=result,
                                        org_id=org_id,
                                        session_id=session_id,
                                        user_id=created_by_user_id,
                                    )
                                ),
                            }
                        )
                    except Exception as exc:
                        db.commit()
                        error_result = {"error": str(exc), "tool_name": tool_name}
                        tool_results.append(error_result)
                        yield {
                            "event": "tool_finished",
                            "payload": {
                                "tool_name": tool_name,
                                "tool_use_id": tool_use.get("id"),
                                "error": str(exc),
                            },
                        }
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.get("id"),
                                "content": self._json_tool_result(
                                    self._model_safe_result(
                                        db,
                                        value=error_result,
                                        org_id=org_id,
                                        session_id=session_id,
                                        user_id=created_by_user_id,
                                    )
                                ),
                                "is_error": True,
                            }
                        )
                messages.append({"role": "user", "content": tool_result_blocks})

            raise RuntimeError("Assistant tool loop exceeded maximum iterations")
        except Exception:
            skill_run.status = AISkillRunStatus.FAILED
            skill_run.validation_status = AIValidationStatus.INVALID
            skill_run.finished_at = utcnow()
            db.commit()
            raise

    async def resume_assistant_run(
        self,
        db: Session,
        *,
        user: User,
        assistant_run_id: str,
        confirmation_id: str | None = None,
        request_id: str | None = None,
    ):
        run = db.get(AssistantRun, assistant_run_id)
        if run is None or run.org_id != user.org_id:
            raise RuntimeError("Assistant run not found")
        provider_state = dict(run.provider_state or {})
        resolved_confirmation_id = confirmation_id or provider_state.get("confirmation_id")
        if not resolved_confirmation_id:
            raise RuntimeError("No confirmation is available to resume")
        confirmation = db.get(AIConfirmation, resolved_confirmation_id)
        if confirmation is None or confirmation.org_id != user.org_id:
            raise RuntimeError("Confirmation not found")
        if confirmation.status != "confirmed":
            raise RuntimeError("Confirmation must be confirmed before resume")
        tool_call = db.get(AssistantToolCall, confirmation.tool_call_id)
        if tool_call is None or tool_call.org_id != user.org_id:
            raise RuntimeError("Tool call not found")
        spec = skill_registry.get("assistant_streaming")
        skill_run = (
            db.get(AISkillRun, provider_state.get("skill_run_id"))
            if provider_state.get("skill_run_id")
            else None
        )
        prompt_bundle = get_active_prompt_bundle(
            db,
            org_id=user.org_id,
            prompt_key=skill_run.prompt_key if skill_run else spec.prompt_key,
            default_version=skill_run.prompt_version if skill_run else spec.prompt_version,
            model_config={"temperature": spec.temperature, "max_tokens": spec.max_tokens},
        )
        if skill_run is None:
            # No skill run was carried in provider_state; create one so the
            # resumed Claude call is still logged (every call must be logged).
            skill_run = AISkillRun(
                org_id=user.org_id,
                skill_name=spec.name,
                skill_version=spec.version,
                execution_mode=spec.execution_mode,
                status=AISkillRunStatus.RUNNING,
                resource_type="assistant_session",
                resource_id=run.session_id,
                session_id=run.session_id,
                assistant_run_id=assistant_run_id,
                prompt_key=prompt_bundle.prompt_key,
                prompt_version=prompt_bundle.version,
                prompt_hash=prompt_bundle.prompt_hash,
                model=prompt_bundle.model_name,
                model_config_hash=prompt_bundle.model_config_hash,
                input_payload={"resumed_confirmation_id": confirmation.id},
                started_at=utcnow(),
                created_by_user_id=user.id,
                updated_by_user_id=user.id,
            )
            db.add(skill_run)
            db.flush()
        else:
            skill_run.status = AISkillRunStatus.RUNNING
            skill_run.finished_at = None

        yield {
            "event": "tool_started",
            "payload": {"tool_name": tool_call.tool_name, "tool_use_id": tool_call.provider_tool_use_id},
        }
        result = tool_runtime.execute_confirmed(db, confirmation=confirmation, user=user)
        db.commit()
        write_audit_log(
            db,
            action="assistant.confirmation_executed",
            resource_type="ai_confirmation",
            resource_id=confirmation.id,
            org_id=user.org_id,
            actor_user_id=user.id,
            after={
                "tool_name": tool_call.tool_name,
                "tool_call_id": tool_call.id,
                "assistant_run_id": assistant_run_id,
            },
        )
        yield {
            "event": "tool_finished",
            "payload": {
                "tool_name": tool_call.tool_name,
                "tool_use_id": tool_call.provider_tool_use_id,
                "result": result,
            },
        }

        messages = list(provider_state.get("messages") or [])
        pending_tool_use = provider_state.get("pending_tool_use") or {}
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": pending_tool_use.get("id") or tool_call.provider_tool_use_id,
                        "content": self._json_tool_result(
                            self._model_safe_result(
                                db,
                                value=result,
                                org_id=user.org_id,
                                session_id=run.session_id,
                                user_id=user.id,
                            )
                        ),
                    }
                ],
            }
        )

        final_answer_parts: list[str] = []
        try:
            for _iteration in range(settings.ai_max_tool_iterations):
                provider_response = await claude_client.complete_with_tools(
                    system_prompt=prompt_bundle.shared_system_prompt + "\n\n" + prompt_bundle.skill_prompt,
                    messages=messages,
                    tools=self._assistant_tool_schemas(user=user),
                    max_tokens=spec.max_tokens,
                    temperature=spec.temperature,
                    model=prompt_bundle.model_name,
                )
                self._log_assistant_ai_call(
                    db,
                    org_id=user.org_id,
                    created_by_user_id=user.id,
                    request_id=request_id,
                    skill_run=skill_run,
                    spec=spec,
                    provider_response=provider_response,
                    assistant_run_id=assistant_run_id,
                    session_id=run.session_id,
                )
                text_delta = "".join(
                    block.get("text", "")
                    for block in provider_response.content_blocks
                    if block.get("type") == "text"
                )
                if not provider_response.tool_use_blocks:
                    if text_delta:
                        final_answer_parts.append(text_delta)
                        yield {"event": "message_delta", "payload": {"text": text_delta}}
                    skill_run.status = AISkillRunStatus.SUCCEEDED
                    skill_run.validation_status = AIValidationStatus.VALID
                    skill_run.output_payload = {
                        "answer": "".join(final_answer_parts),
                        "confirmed_tool_result": result,
                    }
                    skill_run.finished_at = utcnow()
                    run.status = "succeeded"
                    run.provider_state = {**provider_state, "resumed_at": utcnow().isoformat()}
                    run.completed_at = utcnow()
                    db.commit()
                    return
                if text_delta:
                    final_answer_parts.append(text_delta)
                    yield {"event": "message_delta", "payload": {"text": text_delta}}
                messages.append({"role": "assistant", "content": provider_response.content_blocks})
                tool_result_blocks = []
                for tool_use in provider_response.tool_use_blocks:
                    tool_name = tool_use.get("name")
                    tool_input = tool_use.get("input") or {}
                    yield {
                        "event": "tool_started",
                        "payload": {"tool_name": tool_name, "tool_use_id": tool_use.get("id")},
                    }
                    try:
                        loop_result = tool_runtime.execute(
                            db,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            user=user,
                            session_id=run.session_id,
                            assistant_run_id=assistant_run_id,
                            provider_tool_use_id=tool_use.get("id"),
                            provider_state={
                                "schema_version": 1,
                                "pending_tool_use": tool_use,
                                "messages": messages,
                                "skill_run_id": skill_run.id,
                            },
                        )
                        db.commit()
                        if loop_result.get("confirmation_required"):
                            run.status = "waiting_confirmation"
                            run.provider_state = {
                                "schema_version": 1,
                                "pending_tool_use": tool_use,
                                "messages": messages,
                                "skill_run_id": skill_run.id,
                                "confirmation_id": loop_result.get("confirmation_id"),
                            }
                            skill_run.status = AISkillRunStatus.WAITING_CONFIRMATION
                            skill_run.finished_at = utcnow()
                            db.commit()
                            yield {
                                "event": "confirmation_required",
                                "payload": {
                                    "tool_name": tool_name,
                                    "tool_call_id": loop_result.get("tool_call_id"),
                                    "confirmation_id": loop_result.get("confirmation_id"),
                                    "assistant_run_id": assistant_run_id,
                                },
                            }
                            return
                        yield {
                            "event": "tool_finished",
                            "payload": {"tool_name": tool_name, "tool_use_id": tool_use.get("id"), "result": loop_result},
                        }
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.get("id"),
                                "content": self._json_tool_result(
                                    self._model_safe_result(
                                        db,
                                        value=loop_result,
                                        org_id=user.org_id,
                                        session_id=run.session_id,
                                        user_id=user.id,
                                    )
                                ),
                            }
                        )
                    except Exception as exc:
                        db.commit()
                        error_result = {"error": str(exc), "tool_name": tool_name}
                        yield {
                            "event": "tool_finished",
                            "payload": {
                                "tool_name": tool_name,
                                "tool_use_id": tool_use.get("id"),
                                "error": str(exc),
                            },
                        }
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.get("id"),
                                "content": self._json_tool_result(error_result),
                                "is_error": True,
                            }
                        )
                messages.append({"role": "user", "content": tool_result_blocks})
            raise RuntimeError("Assistant tool loop exceeded maximum iterations")
        except Exception:
            skill_run.status = AISkillRunStatus.FAILED
            skill_run.validation_status = AIValidationStatus.INVALID
            skill_run.finished_at = utcnow()
            run.status = "failed"
            db.commit()
            raise

    def _assistant_tool_schemas(self, *, user: User) -> list[dict[str, Any]]:
        tools = []
        for tool in tool_registry.all():
            if not tool.enabled_by_default:
                continue
            if not has_permission(user.permission_values, tool.required_permission):
                continue
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_model.model_json_schema(),
                }
            )
        return tools

    def _assistant_user_prompt(
        self,
        *,
        message: str,
        project_id: str | None,
        contract_id: str | None,
        contract_ids: list[str],
        handles: list[dict[str, Any]],
        contract_summaries: list[dict[str, Any]] | None = None,
    ) -> str:
        safe_handles = [{"handle": h.get("handle")} for h in handles]
        scope = {
            "has_project_scope": bool(project_id),
            "primary_contract_handle": _handle_for_contract_id(contract_id, handles),
            "contract_handles": [
                handle
                for contract in contract_ids
                if (handle := _handle_for_contract_id(contract, handles))
            ],
        }
        return "\n\n".join(
            [
                f"User message:\n{message}",
                "Available contract handles for tool use:",
                self._json_tool_result(safe_handles),
                "Session scope:",
                self._json_tool_result(scope),
                "Contract status context:",
                self._json_tool_result(contract_summaries or []),
                "Use tools when contract/project data is needed. Use handles like contract-0 in tool inputs.",
            ]
        )

    def _log_assistant_ai_call(
        self,
        db: Session,
        *,
        org_id: str,
        created_by_user_id: str | None,
        request_id: str | None,
        skill_run: AISkillRun,
        spec: SkillSpec,
        provider_response: ClaudeProviderResponse,
        assistant_run_id: str,
        session_id: str,
    ) -> AICallLog:
        text_output = "".join(
            block.get("text", "")
            for block in provider_response.content_blocks
            if block.get("type") == "text"
        )
        row = AICallLog(
            org_id=org_id,
            request_id=request_id,
            skill_run_id=skill_run.id,
            session_id=session_id,
            assistant_run_id=assistant_run_id,
            resource_type="assistant_session",
            resource_id=session_id,
            provider=claude_client.provider,
            model=provider_response.model,
            model_config_hash=skill_run.model_config_hash,
            prompt_key=skill_run.prompt_key,
            prompt_version=skill_run.prompt_version,
            prompt_hash=skill_run.prompt_hash,
            input_payload=skill_run.input_payload,
            output_schema_name=spec.output_schema_name,
            prompt_tokens=provider_response.token_usage.get("prompt_tokens"),
            completion_tokens=provider_response.token_usage.get("completion_tokens"),
            total_tokens=provider_response.token_usage.get("total_tokens"),
            latency_ms=provider_response.latency_ms,
            status=AICallStatus.SUCCEEDED,
            validation_status=AIValidationStatus.NOT_VALIDATED,
            provider_request_id=provider_response.provider_request_id,
            stop_reason=provider_response.stop_reason,
            redaction_status="redacted",
            raw_ai_output=provider_response.raw_response if settings.ai_store_raw_outputs else None,
            validated_output={
                "text": text_output,
                "tool_uses": [
                    {"id": block.get("id"), "name": block.get("name")}
                    for block in provider_response.tool_use_blocks
                ],
            },
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(row)
        db.flush()
        self._record_usage(
            db,
            org_id=org_id,
            created_by_user_id=created_by_user_id,
            resource_type="assistant_session",
            resource_id=session_id,
            provider_response=provider_response,
            skill_run=skill_run,
        )
        db.commit()
        return row

    def _handle_for_contract(
        self,
        db: Session,
        *,
        org_id: str,
        session_id: str,
        contract_id: str,
        user_id: str | None,
    ) -> str:
        existing = db.scalar(
            select(AssistantContractHandle).where(
                AssistantContractHandle.org_id == org_id,
                AssistantContractHandle.session_id == session_id,
                AssistantContractHandle.contract_id == contract_id,
            )
        )
        if existing is not None:
            return existing.handle
        count = len(
            db.scalars(
                select(AssistantContractHandle).where(
                    AssistantContractHandle.org_id == org_id,
                    AssistantContractHandle.session_id == session_id,
                )
            ).all()
        )
        handle_value = f"contract-{count}"
        db.add(
            AssistantContractHandle(
                org_id=org_id,
                session_id=session_id,
                contract_id=contract_id,
                handle=handle_value,
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
            )
        )
        db.flush()
        return handle_value

    def _contract_context_summaries(
        self,
        db: Session,
        *,
        org_id: str,
        handles: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        summaries = []
        for handle in handles:
            contract_id = handle.get("contract_id")
            if not contract_id:
                continue
            contract = db.get(Contract, contract_id)
            if contract is None or contract.org_id != org_id or contract.deleted_at is not None:
                continue
            summaries.append(
                {
                    "handle": handle.get("handle"),
                    "title": contract.title,
                    "contract_type": contract.contract_type,
                    "lifecycle_stage": contract.lifecycle_stage,
                    "risk_level": contract.risk_level,
                    "counterparty_name": contract.counterparty_name,
                    "jurisdiction": contract.jurisdiction,
                    "effective_date": contract.effective_date,
                    "expiration_date": contract.expiration_date,
                    "value_amount": contract.value_amount,
                    "currency": contract.currency,
                }
            )
        return summaries

    def _model_safe_result(
        self,
        db: Session,
        *,
        value: Any,
        org_id: str,
        session_id: str,
        user_id: str | None,
    ) -> Any:
        """Strip internal UUIDs from anything sent back into the Claude
        conversation; expose stable contract handles instead."""
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                if key in INTERNAL_RESULT_KEYS:
                    continue
                if key == "contract_id" and isinstance(item, str) and item:
                    out["contract_handle"] = self._handle_for_contract(
                        db,
                        org_id=org_id,
                        session_id=session_id,
                        contract_id=item,
                        user_id=user_id,
                    )
                    continue
                out[key] = self._model_safe_result(
                    db, value=item, org_id=org_id, session_id=session_id, user_id=user_id
                )
            return out
        if isinstance(value, list):
            return [
                self._model_safe_result(
                    db, value=item, org_id=org_id, session_id=session_id, user_id=user_id
                )
                for item in value
            ]
        return value

    def _json_tool_result(self, value: Any) -> str:
        import json

        return json.dumps(value, default=str, sort_keys=True)

    async def run_structured_skill(
        self,
        db: Session,
        *,
        skill_name: str,
        org_id: str,
        created_by_user_id: str | None,
        input_payload: dict[str, Any],
        request_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        job_id: str | None = None,
        session_id: str | None = None,
        assistant_run_id: str | None = None,
        tool_call_id: str | None = None,
        commit: bool = True,
    ) -> BaseModel:
        spec = skill_registry.get(skill_name)
        self._ensure_skill_enabled(db, org_id=org_id, spec=spec)

        model_config = {"temperature": spec.temperature, "max_tokens": spec.max_tokens}
        prompt_bundle = get_active_prompt_bundle(
            db,
            org_id=org_id,
            prompt_key=spec.prompt_key,
            default_version=spec.prompt_version,
            model_config=model_config,
        )
        contract_context = self._maybe_contract_context(
            db,
            org_id=org_id,
            input_payload=input_payload,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        built_prompt = prompt_builder.build_structured_skill_prompt(
            spec=spec,
            prompt_bundle=prompt_bundle,
            input_payload=input_payload,
            contract_context=contract_context,
        )
        skill_run = AISkillRun(
            org_id=org_id,
            skill_name=spec.name,
            skill_version=spec.version,
            execution_mode=spec.execution_mode,
            status=AISkillRunStatus.RUNNING,
            resource_type=resource_type,
            resource_id=resource_id,
            contract_id=contract_context.contract.id if contract_context else input_payload.get("contract_id"),
            contract_version_id=contract_context.version.id if contract_context and contract_context.version else input_payload.get("contract_version_id"),
            text_snapshot_id=contract_context.snapshot.id if contract_context and contract_context.snapshot else input_payload.get("text_snapshot_id"),
            job_id=job_id,
            session_id=session_id,
            assistant_run_id=assistant_run_id,
            tool_call_id=tool_call_id,
            prompt_key=prompt_bundle.prompt_key,
            prompt_version=prompt_bundle.version,
            prompt_hash=prompt_bundle.prompt_hash,
            model=prompt_bundle.model_name,
            model_config_hash=prompt_bundle.model_config_hash,
            input_payload=_redacted_input(input_payload),
            started_at=utcnow(),
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(skill_run)
        db.flush()
        write_timeline_event(
            db,
            org_id=org_id,
            resource_type=resource_type or "ai_skill",
            resource_id=resource_id or skill_run.id,
            event_type="ai.skill_started",
            title=f"AI skill started: {spec.name}",
            actor_user_id=created_by_user_id,
            request_id=request_id,
            job_id=job_id,
            skill_run_id=skill_run.id,
            details={"skill_run_id": skill_run.id, "skill_name": spec.name},
        )

        ai_call_log: AICallLog | None = None
        try:
            provider_response = await claude_client.complete_structured(
                system_prompt=built_prompt.system_prompt,
                user_prompt=built_prompt.user_prompt,
                tool_name=spec.return_tool_name,
                input_schema=spec.output_model.model_json_schema(),
                max_tokens=spec.max_tokens,
                temperature=spec.temperature,
                model=prompt_bundle.model_name,
            )
            raw_output = self._extract_structured_output(provider_response, spec)
            validated = spec.output_model.model_validate(raw_output)
            validation_status = self._validate_and_store_citations(
                db,
                org_id=org_id,
                skill_run=skill_run,
                resource_type=resource_type,
                resource_id=resource_id,
                contract_context=contract_context,
                output=validated,
                created_by_user_id=created_by_user_id,
            )
            skill_run.validation_status = validation_status
            skill_run.status = (
                AISkillRunStatus.NEEDS_REVIEW
                if validation_status == AIValidationStatus.NEEDS_REVIEW
                else AISkillRunStatus.SUCCEEDED
            )
            skill_run.output_payload = validated.model_dump(mode="json")
            skill_run.finished_at = utcnow()
            ai_call_log = self._log_ai_call(
                db,
                org_id=org_id,
                created_by_user_id=created_by_user_id,
                request_id=request_id,
                skill_run=skill_run,
                spec=spec,
                provider_response=provider_response,
                raw_output=raw_output,
                validated=validated,
                status=AICallStatus.SUCCEEDED,
                validation_status=validation_status,
                job_id=job_id,
                session_id=session_id,
                assistant_run_id=assistant_run_id,
                tool_call_id=tool_call_id,
                resource_type=resource_type,
                resource_id=resource_id,
            )
            self._persist_skill_output(
                db,
                spec=spec,
                output=validated,
                contract_context=contract_context,
                created_by_user_id=created_by_user_id,
                request_id=request_id,
                skill_run=skill_run,
                ai_call_log=ai_call_log,
            )
            self._record_usage(
                db,
                org_id=org_id,
                created_by_user_id=created_by_user_id,
                resource_type=resource_type,
                resource_id=resource_id,
                provider_response=provider_response,
                skill_run=skill_run,
            )
            write_timeline_event(
                db,
                org_id=org_id,
                resource_type=resource_type or "ai_skill",
                resource_id=resource_id or skill_run.id,
                event_type="ai.skill_succeeded",
                title=f"AI skill completed: {spec.name}",
                actor_user_id=created_by_user_id,
                request_id=request_id,
                job_id=job_id,
                skill_run_id=skill_run.id,
                ai_call_id=ai_call_log.id,
                details={"skill_run_id": skill_run.id, "validation_status": validation_status},
            )
            if job_id:
                self._finish_job(db, job_id=job_id, status="succeeded", error_message=None)
            if commit:
                db.commit()
            return validated
        except Exception as exc:
            fallback = self._fallback_output(spec, contract_context, exc)
            if fallback is not None:
                skill_run.status = AISkillRunStatus.NEEDS_REVIEW
                skill_run.validation_status = AIValidationStatus.NEEDS_REVIEW
                skill_run.output_payload = fallback.model_dump(mode="json")
                skill_run.validation_error = f"Claude failed; fallback used: {exc.__class__.__name__}"
                skill_run.finished_at = utcnow()
                self._persist_skill_output(
                    db,
                    spec=spec,
                    output=fallback,
                    contract_context=contract_context,
                    created_by_user_id=created_by_user_id,
                    request_id=request_id,
                    skill_run=skill_run,
                    ai_call_log=ai_call_log,
                )
                if job_id:
                    self._finish_job(db, job_id=job_id, status="succeeded", error_message="Fallback metadata used")
                if commit:
                    db.commit()
                return fallback
            skill_run.status = AISkillRunStatus.FAILED
            skill_run.validation_status = AIValidationStatus.INVALID
            skill_run.error_message = str(exc)
            skill_run.finished_at = utcnow()
            if job_id:
                self._finish_job(db, job_id=job_id, status="failed", error_message=str(exc))
            write_timeline_event(
                db,
                org_id=org_id,
                resource_type=resource_type or "ai_skill",
                resource_id=resource_id or skill_run.id,
                event_type="ai.skill_failed",
                title=f"AI skill failed: {spec.name}",
                actor_user_id=created_by_user_id,
                request_id=request_id,
                job_id=job_id,
                skill_run_id=skill_run.id,
                details={"skill_run_id": skill_run.id, "error": str(exc)},
            )
            if commit:
                db.commit()
            raise

    def _ensure_skill_enabled(self, db: Session, *, org_id: str, spec: SkillSpec) -> None:
        if spec.feature_flag is None:
            return
        setting = db.scalar(
            select(AdminSetting).where(AdminSetting.org_id == org_id, AdminSetting.key == spec.feature_flag)
        )
        enabled = spec.enabled_by_default if setting is None else bool(setting.value)
        if not enabled:
            raise RuntimeError(f"AI skill is disabled: {spec.name}")

    def _maybe_contract_context(
        self,
        db: Session,
        *,
        org_id: str,
        input_payload: dict[str, Any],
        resource_type: str | None,
        resource_id: str | None,
    ) -> ContractAIContext | None:
        contract_id = input_payload.get("contract_id")
        if resource_type == "contract" and resource_id:
            contract_id = resource_id
        if not contract_id:
            return None
        return build_contract_context(
            db,
            org_id=org_id,
            contract_id=contract_id,
            contract_version_id=input_payload.get("contract_version_id"),
            text_snapshot_id=input_payload.get("text_snapshot_id"),
        )

    def _extract_structured_output(self, response: ClaudeProviderResponse, spec: SkillSpec) -> dict[str, Any]:
        for block in response.tool_use_blocks:
            if block.get("name") == spec.return_tool_name:
                return block.get("input") or {}
        raise RuntimeError(f"Claude did not return expected tool output: {spec.return_tool_name}")

    def _validate_and_store_citations(
        self,
        db: Session,
        *,
        org_id: str,
        skill_run: AISkillRun,
        resource_type: str | None,
        resource_id: str | None,
        contract_context: ContractAIContext | None,
        output: BaseModel,
        created_by_user_id: str | None,
    ) -> str:
        citations = _collect_citations(output)
        if not citations:
            return AIValidationStatus.VALID
        source_text = contract_context.text if contract_context else ""
        is_ocr = bool(contract_context and contract_context.snapshot and contract_context.snapshot.ocr_provider)
        results = validate_citations(citations, source_text, is_ocr=is_ocr)
        status = AIValidationStatus.VALID
        for result in results:
            if result.validation_status != "valid":
                status = AIValidationStatus.NEEDS_REVIEW
            db.add(
                AICitation(
                    org_id=org_id,
                    skill_run_id=skill_run.id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    contract_id=contract_context.contract.id if contract_context else None,
                    contract_version_id=contract_context.version.id if contract_context and contract_context.version else None,
                    text_snapshot_id=contract_context.snapshot.id if contract_context and contract_context.snapshot else None,
                    label=result.citation.label,
                    quote=result.citation.quote,
                    normalized_quote=result.normalized_quote,
                    page_number=result.citation.page_number,
                    start_char=result.citation.start_char,
                    end_char=result.citation.end_char,
                    validation_status=result.validation_status,
                    similarity_score=result.similarity_score,
                    metadata_json={"message": result.message},
                    created_by_user_id=created_by_user_id,
                    updated_by_user_id=created_by_user_id,
                )
            )
        return status

    def _log_ai_call(
        self,
        db: Session,
        *,
        org_id: str,
        created_by_user_id: str | None,
        request_id: str | None,
        skill_run: AISkillRun,
        spec: SkillSpec,
        provider_response: ClaudeProviderResponse,
        raw_output: dict[str, Any],
        validated: BaseModel,
        status: str,
        validation_status: str,
        job_id: str | None,
        session_id: str | None,
        assistant_run_id: str | None,
        tool_call_id: str | None,
        resource_type: str | None,
        resource_id: str | None,
    ) -> AICallLog:
        row = AICallLog(
            org_id=org_id,
            request_id=request_id,
            skill_run_id=skill_run.id,
            job_id=job_id,
            session_id=session_id,
            assistant_run_id=assistant_run_id,
            tool_call_id=tool_call_id,
            resource_type=resource_type,
            resource_id=resource_id,
            provider=claude_client.provider,
            model=provider_response.model,
            model_config_hash=skill_run.model_config_hash,
            prompt_key=skill_run.prompt_key,
            prompt_version=skill_run.prompt_version,
            prompt_hash=skill_run.prompt_hash,
            input_payload=skill_run.input_payload,
            output_schema_name=spec.output_schema_name,
            prompt_tokens=provider_response.token_usage.get("prompt_tokens"),
            completion_tokens=provider_response.token_usage.get("completion_tokens"),
            total_tokens=provider_response.token_usage.get("total_tokens"),
            latency_ms=provider_response.latency_ms,
            status=status,
            validation_status=validation_status,
            provider_request_id=provider_response.provider_request_id,
            stop_reason=provider_response.stop_reason,
            redaction_status="redacted",
            raw_ai_output=provider_response.raw_response if settings.ai_store_raw_outputs else None,
            validated_output=validated.model_dump(mode="json"),
            created_by_user_id=created_by_user_id,
            updated_by_user_id=created_by_user_id,
        )
        db.add(row)
        db.flush()
        return row

    def _persist_skill_output(
        self,
        db: Session,
        *,
        spec: SkillSpec,
        output: BaseModel,
        contract_context: ContractAIContext | None,
        created_by_user_id: str | None,
        request_id: str | None,
        skill_run: AISkillRun,
        ai_call_log: AICallLog | None,
    ) -> None:
        if contract_context is None:
            return
        if spec.name == "contract_metadata_extraction":
            self._persist_metadata(db, output=output, context=contract_context, created_by_user_id=created_by_user_id, request_id=request_id, skill_run=skill_run)
        elif spec.name == "clause_extraction":
            self._persist_clauses(db, output=output, context=contract_context, created_by_user_id=created_by_user_id)
        elif spec.name == "contract_edit_suggestions":
            self._persist_contract_edits(db, output=output, context=contract_context, created_by_user_id=created_by_user_id)

    def _persist_metadata(
        self,
        db: Session,
        *,
        output: BaseModel,
        context: ContractAIContext,
        created_by_user_id: str | None,
        request_id: str | None,
        skill_run: AISkillRun,
    ) -> None:
        metadata = output if isinstance(output, ContractMetadataOutput) else ContractMetadataOutput.model_validate(output)
        contract = context.contract
        before = {
            "title": contract.title,
            "contract_type": contract.contract_type,
            "counterparty_name": contract.counterparty_name,
            "jurisdiction": contract.jurisdiction,
            "risk_level": contract.risk_level,
            "value_amount": contract.value_amount,
            "currency": contract.currency,
            "effective_date": contract.effective_date,
            "expiration_date": contract.expiration_date,
        }
        suggestions = metadata.model_dump(mode="json", exclude_none=True)
        existing_metadata = dict(contract.metadata_json or {})
        existing_metadata.setdefault("ai_suggestions", {})["metadata"] = suggestions
        existing_metadata["latest_metadata_skill_run_id"] = skill_run.id
        contract.metadata_json = existing_metadata
        if metadata.confidence == "high" and metadata.citations:
            for field in [
                "contract_type",
                "counterparty_name",
                "jurisdiction",
                "risk_level",
                "value_amount",
                "currency",
                "effective_date",
                "expiration_date",
            ]:
                value = getattr(metadata, field)
                if value is not None:
                    setattr(contract, field, value)
        contract.updated_by_user_id = created_by_user_id
        write_audit_log(
            db,
            action="contract.ai_metadata_extracted",
            resource_type="contract",
            resource_id=contract.id,
            org_id=contract.org_id,
            actor_user_id=created_by_user_id,
            request_id=request_id,
            before=before,
            after=suggestions,
            metadata={"skill_run_id": skill_run.id},
        )
        write_timeline_event(
            db,
            org_id=contract.org_id,
            resource_type="contract",
            resource_id=contract.id,
            event_type="contract.ai_metadata_extracted",
            title="AI metadata extracted",
            actor_user_id=created_by_user_id,
            request_id=request_id,
            skill_run_id=skill_run.id,
            details={
                "confidence": metadata.confidence,
                "applied_to_contract": metadata.confidence == "high" and bool(metadata.citations),
                "fields": sorted(suggestions.keys()),
            },
        )

    def _persist_clauses(
        self,
        db: Session,
        *,
        output: BaseModel,
        context: ContractAIContext,
        created_by_user_id: str | None,
    ) -> None:
        clauses = output if isinstance(output, ClauseExtractionOutput) else ClauseExtractionOutput.model_validate(output)
        if context.version is None or context.snapshot is None:
            return
        existing = db.scalars(
            select(ClauseExtraction).where(
                ClauseExtraction.contract_id == context.contract.id,
                ClauseExtraction.contract_version_id == context.version.id,
                ClauseExtraction.is_stale.is_(False),
            )
        ).all()
        for clause in existing:
            clause.is_stale = True
            clause.updated_by_user_id = created_by_user_id
        for clause in clauses.clauses:
            db.add(
                ClauseExtraction(
                    org_id=context.contract.org_id,
                    contract_id=context.contract.id,
                    contract_version_id=context.version.id,
                    text_snapshot_id=context.snapshot.id,
                    clause_type=clause.clause_type,
                    heading=clause.heading,
                    text=clause.text,
                    start_char=clause.start_char,
                    end_char=clause.end_char,
                    confidence=clause.confidence,
                    created_by_user_id=created_by_user_id,
                    updated_by_user_id=created_by_user_id,
                )
            )

    def _persist_contract_edits(
        self,
        db: Session,
        *,
        output: BaseModel,
        context: ContractAIContext,
        created_by_user_id: str | None,
    ) -> None:
        edits = output if isinstance(output, ContractEditSuggestionsOutput) else ContractEditSuggestionsOutput.model_validate(output)
        if context.version is None:
            return
        for edit in edits.edits:
            db.add(
                ContractEdit(
                    org_id=context.contract.org_id,
                    contract_id=context.contract.id,
                    contract_version_id=context.version.id,
                    edit_type=edit.edit_type,
                    status="proposed",
                    original_text=edit.original_text,
                    replacement_text=edit.replacement_text,
                    rationale=edit.rationale,
                    citation=[citation.model_dump(mode="json") for citation in edit.citations],
                    created_by_user_id=created_by_user_id,
                    updated_by_user_id=created_by_user_id,
                )
            )

    def _record_usage(
        self,
        db: Session,
        *,
        org_id: str,
        created_by_user_id: str | None,
        resource_type: str | None,
        resource_id: str | None,
        provider_response: ClaudeProviderResponse,
        skill_run: AISkillRun,
    ) -> None:
        for metric, quantity in [
            ("ai.call", 1),
            ("ai.prompt_tokens", provider_response.token_usage.get("prompt_tokens") or 0),
            ("ai.completion_tokens", provider_response.token_usage.get("completion_tokens") or 0),
            ("ai.total_tokens", provider_response.token_usage.get("total_tokens") or 0),
        ]:
            db.add(
                UsageRecord(
                    org_id=org_id,
                    user_id=created_by_user_id,
                    resource_type=resource_type or "ai_skill",
                    resource_id=resource_id or skill_run.id,
                    metric=metric,
                    quantity=float(quantity),
                    metadata_json={"skill_run_id": skill_run.id, "skill_name": skill_run.skill_name},
                    created_by_user_id=created_by_user_id,
                    updated_by_user_id=created_by_user_id,
                )
            )

    def _finish_job(self, db: Session, *, job_id: str, status: str, error_message: str | None) -> None:
        job = db.get(JobRun, job_id)
        if job is None:
            return
        job.status = status
        job.progress = 100 if status == "succeeded" else job.progress
        job.finished_at = utcnow()
        job.error_message = error_message

    def _fallback_output(
        self,
        spec: SkillSpec,
        contract_context: ContractAIContext | None,
        exc: Exception,
    ) -> BaseModel | None:
        if spec.name == "contract_metadata_extraction" and contract_context is not None:
            return fallback_metadata_from_text(contract_context.text, filename=contract_context.contract.title)
        if isinstance(exc, ValidationError):
            return None
        return None


def _collect_citations(output: BaseModel) -> list[CitationInput]:
    citations: list[CitationInput] = []
    root_citations = getattr(output, "citations", None)
    if root_citations:
        citations.extend(root_citations)
    for item_name in ["clauses", "edits"]:
        for item in getattr(output, item_name, []) or []:
            citations.extend(getattr(item, "citations", []) or [])
    return citations


def _handle_for_contract_id(contract_id: str | None, handles: list[dict[str, Any]]) -> str | None:
    if not contract_id:
        return None
    for handle in handles:
        metadata = handle.get("metadata") or {}
        if handle.get("contract_id") == contract_id or metadata.get("contract_id") == contract_id:
            return handle.get("handle")
    return None


def _redacted_input(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = {}
    for key, value in payload.items():
        if isinstance(value, str) and "text" in key.lower() and len(value) > 500:
            redacted[key] = f"<redacted text length={len(value)}>"
        else:
            redacted[key] = value
    return redacted


ai_controller = AIController()
