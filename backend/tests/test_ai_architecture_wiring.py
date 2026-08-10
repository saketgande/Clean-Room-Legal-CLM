import asyncio
import inspect

from app.ai import confirmations
from app.ai.controller import INTERNAL_RESULT_KEYS, _clamp_max_tokens, ai_controller
from app.ai.redaction import redact_ai_payload
from app.ai.tool_registry import ExternalShareInput, tool_registry
from app.ai.tool_runtime import tool_runtime
from app.assistant.routes import (
    _citations_from_tool_result,
    _events_from_tool_result,
    confirm_assistant_action,
    reject_assistant_action,
)
from app.contract_files.routes import _decision_summary
from app.contract_files.service import INITIAL_CONTRACT_AI_JOB_TYPES
from app.integrations import _http_retry
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


def test_resilient_call_skips_pybreaker_when_async_support_is_missing(monkeypatch):
    monkeypatch.setattr(_http_retry, "_PYBREAKER_ASYNC_AVAILABLE", False)

    @_http_retry.resilient_call("smoke", max_attempts=1)
    async def ok():
        return "ok"

    assert asyncio.run(ok()) == "ok"


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


# --- Prod-hardening: redaction, cost cap, max-tokens clamp, audit actor ------


def test_redaction_scrubs_pii_before_persistence_including_nested():
    """A known email / 16+ digit number must NOT survive into a persisted payload,
    at any nesting depth, and an oversized document body is replaced wholesale."""
    persisted = redact_ai_payload(
        {
            "instructions": "Email the signer at jane.doe@acme.com to confirm.",
            "contract_text": "Z" * 5000,
            "recipients": [
                {"email": "ceo@bigco.io", "card": "4111 1111 1111 1111"},
                {"note": "fallback 4242424242424242"},
            ],
        }
    )
    import json as _json

    blob = _json.dumps(persisted)
    assert "jane.doe@acme.com" not in blob
    assert "ceo@bigco.io" not in blob
    assert "4111 1111 1111 1111" not in blob
    assert "4242424242424242" not in blob
    # The contract body is a sensitive key → length-only placeholder, never verbatim.
    assert persisted["contract_text"] == "<redacted text length=5000>"


def test_tool_call_arguments_and_result_are_redacted_before_persist():
    """Both the persisted tool arguments and result run through redaction; the
    idempotency key is still hashed from the raw (unredacted) args."""
    source = inspect.getsource(tool_runtime.execute)

    assert "arguments=redact_ai_payload(validated_args)" in source
    assert "call.result = redact_ai_payload(result)" in source
    # idempotency must hash the raw args, not the redacted copy.
    assert "_idempotency_key(tool_name, session_id, validated_args)" in source


def test_confirmed_tool_result_is_redacted_and_actor_stamped():
    """execute_confirmed scrubs the persisted result and records who confirmed it."""
    source = inspect.getsource(tool_runtime.execute_confirmed)

    assert "call.result = redact_ai_payload(result)" in source
    assert "call.confirmation_id = confirmation.id" in source
    assert "call.confirmed_by_user_id = user.id" in source


def test_skill_run_input_payload_uses_strengthened_redaction():
    """run_structured_skill persists a redacted input payload, and _redacted_input
    delegates to the shared (non-toothless) redaction helper."""
    from app.ai import controller as controller_module

    assert "_redacted_input(input_payload)" in inspect.getsource(ai_controller.run_structured_skill)
    assert "redact_ai_payload(payload)" in inspect.getsource(controller_module._redacted_input)


def test_raw_ai_output_only_persisted_when_flag_enabled():
    """raw_ai_output must be gated on settings.ai_store_raw_outputs (defaults off)."""
    structured = inspect.getsource(ai_controller._log_ai_call)
    assistant = inspect.getsource(ai_controller._log_assistant_ai_call)

    assert "raw_ai_output=provider_response.raw_response if settings.ai_store_raw_outputs else None" in structured
    assert "raw_ai_output=provider_response.raw_response if settings.ai_store_raw_outputs else None" in assistant


def test_max_tokens_clamped_to_ceiling_before_claude():
    from app.core.config import settings

    assert _clamp_max_tokens(settings.claude_max_tokens_ceiling + 100000) == settings.claude_max_tokens_ceiling
    assert _clamp_max_tokens(123) == 123
    # Every Claude call site clamps its max_tokens.
    for fn in (
        ai_controller.run_structured_skill,
        ai_controller.stream_assistant_run,
        ai_controller.resume_assistant_run,
    ):
        assert "_clamp_max_tokens(spec.max_tokens)" in inspect.getsource(fn)


def test_cost_cap_enforced_before_every_claude_call():
    """Each Claude call site calls enforce_daily_token_cap first, and usage is
    recorded back into the daily counter."""
    for fn in (
        ai_controller.run_structured_skill,
        ai_controller.stream_assistant_run,
        ai_controller.resume_assistant_run,
    ):
        assert "enforce_daily_token_cap(" in inspect.getsource(fn)
    assert "record_token_usage(" in inspect.getsource(ai_controller._record_usage)


def test_cost_cap_fails_open_and_is_noop_when_unset():
    """Cap <= 0 is a no-op (no Redis touched) and any Redis error fails open."""
    from app.ai import cost_guard
    from app.core.config import settings

    original_cap = settings.claude_daily_token_cap_per_org
    original_client = cost_guard._redis_client
    try:
        # cap <= 0 → unlimited; Redis must not even be constructed.
        settings.claude_daily_token_cap_per_org = 0

        def _explode():
            raise AssertionError("Redis must not be touched when cap <= 0")

        cost_guard._redis_client = _explode
        cost_guard.enforce_daily_token_cap("org-A")  # no raise
        cost_guard.record_token_usage("org-A", 5000)  # no raise

        # Positive cap but Redis unreachable → fail open (no exception).
        settings.claude_daily_token_cap_per_org = 100

        def _fail():
            raise RuntimeError("redis down")

        cost_guard._redis_client = _fail
        cost_guard.enforce_daily_token_cap("org-A")  # fail open → no raise
        cost_guard.record_token_usage("org-A", 50)   # fail open → no raise
    finally:
        settings.claude_daily_token_cap_per_org = original_cap
        cost_guard._redis_client = original_client


def test_cost_cap_rejects_with_429_when_exceeded():
    from fastapi import HTTPException

    from app.ai import cost_guard
    from app.core.config import settings

    class _FakeClient:
        def __init__(self, value):
            self.value = value

        def get(self, _key):
            return self.value

        def close(self):
            pass

    original_cap = settings.claude_daily_token_cap_per_org
    original_client = cost_guard._redis_client
    try:
        settings.claude_daily_token_cap_per_org = 100
        cost_guard._redis_client = lambda: _FakeClient(b"150")  # already over
        try:
            cost_guard.enforce_daily_token_cap("org-A")
        except HTTPException as exc:
            assert exc.status_code == 429
        else:  # pragma: no cover - must raise
            raise AssertionError("expected HTTP 429 when daily cap is exceeded")

        cost_guard._redis_client = lambda: _FakeClient(b"50")  # under cap
        cost_guard.enforce_daily_token_cap("org-A")  # no raise
    finally:
        settings.claude_daily_token_cap_per_org = original_cap
        cost_guard._redis_client = original_client


def test_external_share_passcode_requires_at_least_eight_chars():
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        ExternalShareInput(contract_handle="contract-0", passcode="1234567")  # 7 chars
    ok = ExternalShareInput(contract_handle="contract-0", passcode="12345678")  # 8 chars
    assert ok.passcode == "12345678"
