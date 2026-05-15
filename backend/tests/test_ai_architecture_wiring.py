import asyncio

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
