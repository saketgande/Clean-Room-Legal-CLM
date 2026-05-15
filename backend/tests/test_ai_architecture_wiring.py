import asyncio
import inspect
from io import BytesIO
from zipfile import ZipFile

from app.ai import confirmations
from app.ai.controller import INTERNAL_RESULT_KEYS, ai_controller
from app.ai.tool_registry import tool_registry
from app.ai.tool_runtime import _build_edit_docx, tool_runtime
from app.assistant.routes import (
    _citations_from_tool_result,
    _events_from_tool_result,
    confirm_assistant_action,
    reject_assistant_action,
)
from app.contract_files.routes import _decision_summary
from app.contract_files.service import INITIAL_CONTRACT_AI_JOB_TYPES
from app.integrations.claude import claude_client


def test_initial_upload_jobs_do_not_queue_contract_brain_ingestion():
    assert INITIAL_CONTRACT_AI_JOB_TYPES == (
        "metadata_extraction",
        "clause_extraction",
        "embeddings",
    )
    assert "contract_brain_ingestion" not in INITIAL_CONTRACT_AI_JOB_TYPES


def test_mock_assistant_tool_loop_requests_contract_read_tool():
    from app.core.config import settings

    settings.mock_claude = True
    response = asyncio.run(
        claude_client.complete_with_tools(
            system_prompt="system",
            messages=[{"role": "user", "content": "summarize this contract"}],
            tools=[
                {
                    "name": "read_contract",
                    "description": "Read contract",
                    "input_schema": {
                        "type": "object",
                        "properties": {"contract_handle": {"type": "string"}},
                    },
                }
            ],
            max_tokens=256,
            temperature=0,
        )
    )

    assert response.stop_reason == "tool_use"
    assert response.tool_use_blocks[0]["name"] == "read_contract"
    assert response.tool_use_blocks[0]["input"] == {"contract_handle": "contract-0"}


def test_mock_assistant_tool_loop_requests_edit_tool_for_redlines():
    from app.core.config import settings

    settings.mock_claude = True
    response = asyncio.run(
        claude_client.complete_with_tools(
            system_prompt="system",
            messages=[{"role": "user", "content": "please redline this contract"}],
            tools=[
                {
                    "name": "edit_contract",
                    "description": "Edit contract",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "contract_handle": {"type": "string"},
                            "instructions": {"type": "string"},
                        },
                    },
                }
            ],
            max_tokens=256,
            temperature=0,
        )
    )

    assert response.stop_reason == "tool_use"
    assert response.tool_use_blocks[0]["name"] == "edit_contract"
    assert response.tool_use_blocks[0]["input"]["contract_handle"] == "contract-0"


def test_edit_contract_confirmation_is_policy_driven_server_side():
    spec = tool_registry.get("edit_contract")

    assert spec.requires_confirmation is True
    assert spec.confirmation_policy == "required"


def test_assistant_resume_executes_confirmed_tool_path():
    source = inspect.getsource(ai_controller.resume_assistant_run)

    assert "NotImplementedError" not in source
    assert "execute_confirmed" in source
    assert "tool_finished" in source


def test_mutating_assistant_tools_are_not_stubbed():
    source = inspect.getsource(tool_runtime._execute_validated)

    assert "queued_for_ai_controller" not in source
    assert "_generate_contract_docx" in source
    assert "_edit_contract" in source
    assert "_replicate_contract_version" in source


def test_assistant_edit_creates_new_stored_version_artifact():
    source = inspect.getsource(tool_runtime._edit_contract)

    assert "_store_docx" in source
    assert "storage_object_id=storage_object.id" in source
    assert "ContractVersionSource.ASSISTANT_EDIT" in source
    assert "is_authoritative=False" in source
    assert 'status="proposed"' in source
    # The proposed edit must NOT become the file's current version until it is
    # explicitly accepted (no split-pointer to an unaccepted proposal).
    assert "contract_file.current_version_id = edit_version.id" not in source


def test_assistant_generated_contract_flushes_jobs_before_dispatch_ids():
    source = inspect.getsource(tool_runtime._generate_contract_docx)

    assert "queued_jobs = _queue_initial_contract_jobs" in source
    assert "db.flush()" in source
    assert source.index("db.flush()", source.index("queued_jobs = _queue_initial_contract_jobs")) < source.index(
        "queued_job_ids = [job.id for job in queued_jobs]"
    )


def test_assistant_edit_docx_contains_native_word_revision_markup():
    content = _build_edit_docx(
        contract_title="Vendor Agreement",
        base_version_number=1,
        instructions="Add a 30-day termination notice requirement.",
        source_text="Either party may terminate this agreement immediately.",
    )

    with ZipFile(BytesIO(content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
        settings_xml = archive.read("word/settings.xml").decode("utf-8")

    assert "<w:trackRevisions" in settings_xml
    assert "<w:del " in document_xml
    assert "<w:delText" in document_xml
    assert "<w:ins " in document_xml
    assert "Either party may terminate this agreement immediately." in document_xml
    assert "Add a 30-day termination notice requirement." in document_xml


def test_confirmation_reject_and_expiry_paths_are_explicit():
    reject_source = inspect.getsource(confirmations.reject_confirmation)
    pending_source = inspect.getsource(confirmations._get_pending_confirmation)

    assert "AIConfirmationStatus.REJECTED" in reject_source
    assert "AssistantToolCallStatus.REJECTED" in reject_source
    assert "AIConfirmationStatus.EXPIRED" in pending_source
    assert "Confirmation expired" in pending_source


def test_resume_requires_confirmed_confirmation_before_execution():
    source = inspect.getsource(ai_controller.resume_assistant_run)

    assert 'confirmation.status != "confirmed"' in source
    assert "Confirmation must be confirmed before resume" in source
    assert source.index('confirmation.status != "confirmed"') < source.index("execute_confirmed")


def test_confirmation_decisions_require_ai_tool_permission():
    confirm_source = inspect.getsource(confirm_assistant_action)
    reject_source = inspect.getsource(reject_assistant_action)

    assert "_require_ai_tools(current_user)" in confirm_source
    assert "_require_ai_tools(current_user)" in reject_source


def test_assistant_prompt_uses_handles_not_internal_ids():
    contract_id = "11111111-1111-1111-1111-111111111111"
    project_id = "22222222-2222-2222-2222-222222222222"
    prompt = ai_controller._assistant_user_prompt(
        message="Summarize this contract",
        project_id=project_id,
        contract_id=contract_id,
        contract_ids=[contract_id],
        handles=[{"handle": "contract-0", "contract_id": contract_id, "metadata": {}}],
    )

    assert "contract-0" in prompt
    assert contract_id not in prompt
    assert project_id not in prompt


def test_model_safe_result_strips_internal_identifier_keys():
    assert "current_authoritative_version_id" in INTERNAL_RESULT_KEYS
    assert "contract_version_id" in INTERNAL_RESULT_KEYS
    assert "project_id" in INTERNAL_RESULT_KEYS


def test_phase3_tool_results_emit_frontend_artifact_events():
    generated_events = _events_from_tool_result(
        {
            "artifact_type": "generated_contract",
            "contract_id": "contract-1",
            "contract_file_id": "file-1",
            "contract_version_id": "version-1",
        }
    )
    edit_events = _events_from_tool_result(
        {
            "artifact_type": "assistant_edit",
            "contract_id": "contract-1",
            "base_version_id": "version-1",
            "contract_version_id": "version-2",
            "contract_edit_id": "edit-1",
        }
    )

    assert generated_events[0]["event"] == "contract_generated"
    assert edit_events[0]["event"] == "tracked_change_created"


def test_read_contract_result_creates_verifiable_text_snapshot_citation():
    citations = _citations_from_tool_result(
        {
            "contract_id": "contract-1",
            "text_snapshot_id": "snapshot-1",
            "text_excerpt": "This agreement includes a confidentiality clause.",
        }
    )

    assert citations[0]["type"] == "text_snapshot"
    assert citations[0]["excerpt"] == "This agreement includes a confidentiality clause."
    assert citations[0]["start_char"] == 0


def test_tracked_edit_decision_summary_preserves_existing_summary():
    summary = _decision_summary("Assistant edit proposal", decision="accepted", comment="Looks good")

    assert "Assistant edit proposal" in summary
    assert "Assistant edit accepted" in summary
    assert "Looks good" in summary
