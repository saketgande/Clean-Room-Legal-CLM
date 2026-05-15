import asyncio
import inspect

from app.ai import confirmations
from app.ai.controller import ai_controller
from app.ai.tool_registry import tool_registry
from app.ai.tool_runtime import tool_runtime
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
