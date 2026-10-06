import asyncio
import inspect

from app.ai import confirmations
from app.ai.controller import INTERNAL_RESULT_KEYS, ai_controller
from app.ai.gateway.gateway import _clamp as _clamp_max_tokens
from app.ai.redaction import redact_ai_payload
from app.ai.tool_registry import ExternalShareInput, tool_registry
from app.ai.tool_runtime import tool_runtime
from app.assistant.routes import (
    _citations_from_tool_result,
    _events_from_tool_result,
    confirm_assistant_action,
    reject_assistant_action,
    stream_session,
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
            org_id="org-test",
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
            org_id="org-test",
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
    expire_source = inspect.getsource(confirmations.expire_confirmation)
    assert "AIConfirmationStatus.EXPIRED" in expire_source
    assert "Confirmation expired" in pending_source
    # The expiry is committed BEFORE the 409 is raised: get_db rolls back on the
    # exception, which used to discard it and leave the run waiting forever.
    expire_at = pending_source.index("expire_confirmation(db, confirmation)")
    assert expire_at < pending_source.index("db.commit()") < pending_source.index('"Confirmation expired"')


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
    prompt = ai_controller._assistant_user_prompt(
        message="Summarize this contract",
        contract_id=contract_id,
        contract_ids=[contract_id],
        handles=[{"handle": "contract-0", "contract_id": contract_id, "metadata": {}}],
    )

    assert "contract-0" in prompt
    assert contract_id not in prompt


def test_model_safe_result_strips_internal_identifier_keys():
    assert "current_authoritative_version_id" in INTERNAL_RESULT_KEYS
    assert "contract_version_id" in INTERNAL_RESULT_KEYS


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

    # read_contract now returns the windowed body under "text" (paginated); the
    # citation builder must still pick it up so sources don't go blank.
    paged = _citations_from_tool_result(
        {
            "contract_id": "contract-1",
            "text_snapshot_id": "snapshot-1",
            "text": "Windowed body text of the contract.",
            "has_more": True,
            "next_offset": 24000,
        }
    )
    assert paged[0]["excerpt"] == "Windowed body text of the contract."


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
    from app.ai.gateway import ledger

    # Skills and the assistant both write through the gateway's single ledger writer.
    assert "if (response is not None and settings.ai_store_raw_outputs) else None" in inspect.getsource(ledger.record_call)


def test_max_tokens_clamped_to_ceiling_before_claude():
    from app.core.config import settings

    assert _clamp_max_tokens(settings.claude_max_tokens_ceiling + 100000) == settings.claude_max_tokens_ceiling
    assert _clamp_max_tokens(123) == 123
    # Every Claude call site clamps its max_tokens.
    # Skills and the assistant go through the AI gateway, which clamps every
    # feature's budget.
    from app.ai.gateway import gateway as gateway_module

    assert "max_tokens=_clamp(feature.max_tokens)" in inspect.getsource(gateway_module.AIGateway._plan)


def test_every_claude_request_is_metered_inside_the_client():
    """LLM-06: the Claude client reserves against the daily cap before each request
    and settles afterwards, so no call site can skip it; every call names its org."""
    import pathlib
    import re

    from app.integrations import claude

    for name in ("complete_structured", "complete_vision_structured", "complete_text", "complete_with_tools", "stream_with_tools"):
        param = inspect.signature(getattr(claude.ClaudeClient, name)).parameters["org_id"]
        assert param.default is inspect.Parameter.empty
    for fn in (claude.ClaudeClient._post_messages, claude.ClaudeClient.stream_with_tools):
        source = inspect.getsource(fn)
        assert "reserve_tokens(" in source and "settle_tokens(" in source

    call = re.compile(r"\.(complete_structured|complete_vision_structured|complete_text|complete_with_tools|stream_with_tools)\(")
    app_root = pathlib.Path(claude.__file__).parents[1]
    missing = []
    for path in app_root.rglob("*.py"):
        text = path.read_text()
        for match in call.finditer(text):
            depth, end = 1, match.end()
            while depth:
                depth += {"(": 1, ")": -1}.get(text[end], 0)
                end += 1
            if "org_id=" not in text[match.end():end]:
                missing.append(f"{path.relative_to(app_root)}: {match.group(1)}")
    assert not missing, missing


def test_cost_cap_fails_open_and_is_noop_when_unset(monkeypatch):
    """Cap <= 0 is a no-op (no Redis touched) and any Redis error fails open."""
    from app.ai import cost_guard
    from app.core.config import settings

    def _explode():
        raise AssertionError("Redis must not be touched when cap <= 0")

    monkeypatch.setattr(settings, "claude_daily_token_cap_per_org", 0)
    monkeypatch.setattr(cost_guard, "_redis_client", _explode)
    assert cost_guard.reserve_tokens("org-A", 5000) is None
    cost_guard.settle_tokens(None, 5000)  # nothing reserved, nothing to settle

    def _fail():
        raise RuntimeError("redis down")

    monkeypatch.setattr(settings, "claude_daily_token_cap_per_org", 100)
    monkeypatch.setattr(cost_guard, "_redis_client", _fail)
    assert cost_guard.reserve_tokens("org-A", 50) is None  # fail open, no raise
    cost_guard.settle_tokens(("ai:token_cap:org-A:2026-01-01", 50), 10)  # fail open, no raise


def test_cost_cap_rejects_with_429_when_exceeded(monkeypatch):
    from fastapi import HTTPException

    from app.ai import cost_guard
    from app.core.config import settings

    class _FakeRedis:
        """Applies the reserve script's rule: refuse once spent, else add the reservation."""

        def __init__(self, spent):
            self.spent = spent

        def eval(self, _script, _numkeys, _key, cap, reserve, _ttl):
            if self.spent >= cap:
                return -1
            self.spent += reserve
            return 1

    monkeypatch.setattr(settings, "claude_daily_token_cap_per_org", 100)
    monkeypatch.setattr(cost_guard, "_redis_client", lambda: _FakeRedis(150))  # already over
    try:
        cost_guard.reserve_tokens("org-A", 10)
    except HTTPException as exc:
        assert exc.status_code == 429
    else:  # pragma: no cover - must raise
        raise AssertionError("expected HTTP 429 when daily cap is exceeded")

    monkeypatch.setattr(cost_guard, "_redis_client", lambda: _FakeRedis(50))  # under cap
    assert cost_guard.reserve_tokens("org-A", 10) is not None


def test_external_share_passcode_requires_at_least_eight_chars():
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        ExternalShareInput(contract_handle="contract-0", passcode="1234567")  # 7 chars
    ok = ExternalShareInput(contract_handle="contract-0", passcode="12345678")  # 8 chars
    assert ok.passcode == "12345678"


def test_legal_question_session_adds_intake_context_only_for_that_session_type():
    """A chat opened from Legal Intake's 'General legal question' form tells the model
    where the user came from and how to file; other sessions are unchanged."""
    kwargs = dict(message="Can we terminate early?", contract_id=None, contract_ids=[], handles=[])
    legal = ai_controller._assistant_user_prompt(**kwargs, session_type="legal_question")
    general = ai_controller._assistant_user_prompt(**kwargs, session_type="general")
    default = ai_controller._assistant_user_prompt(**kwargs)

    assert "General legal question" in legal
    assert '"Legal Question — General"' in legal
    assert "Indian law" in legal  # blank jurisdiction defaults to India, stated in the answer
    assert "General legal question" not in general
    assert general == default
    # The worker passes the session's type through to the controller.
    from app.assistant.runner import drive_run

    assert "session_type=session.session_type" in inspect.getsource(drive_run)


# Ask Aegis must ask before any high-risk action: deciding approvals, anything that
# reaches the counterparty, moving a contract or a governance workflow, or changing
# who owns a request. A prompt-injected contract could otherwise make the model do
# these on its own. (Decided 2026-10-05: lower-risk filing/notice/task/obligation
# tools stay instant.)
HIGH_RISK_TOOLS = {
    "decide_approval",
    "send_for_negotiation",
    "add_contract_comment",
    "advance_contract_stage",
    "reassign_request",
    "start_intake_workflow",
    "advance_intake_workflow",
    "create_workflow",
    # already confirmation-gated before this change
    "edit_contract",
    "redraft_contract",
    "redline_against_playbook",
    "send_for_signature",
    "external_share",
}


def test_high_risk_assistant_tools_require_confirmation():
    from app.ai.tool_registry import tool_registry

    missing = sorted(n for n in HIGH_RISK_TOOLS if not tool_registry.get(n).requires_confirmation)
    assert missing == []


def test_every_external_action_tool_requires_confirmation():
    from app.ai.tool_registry import tool_registry
    from app.core.enums import AssistantToolCategory

    external = [s.name for s in tool_registry._tools.values()
                if s.category == AssistantToolCategory.EXTERNAL_ACTION and not s.requires_confirmation]
    assert external == []


# Tools that change data but deliberately run without asking. Each one only
# adds a new analysis record for the user to read and changes nothing that
# exists; the reason is written next to its registration.
NO_CONFIRMATION_BY_DESIGN = {"run_playbook_review", "create_tabular_review"}


def test_every_tool_that_changes_data_asks_first_unless_listed():
    """Safe by default: a non-read-only tool must ask the user before it runs,
    so a hidden instruction in contract or email text can't trigger it. A new
    exception has to be added to NO_CONFIRMATION_BY_DESIGN on purpose."""
    from app.ai.tool_registry import tool_registry
    from app.core.enums import AssistantToolCategory

    unconfirmed = {s.name for s in tool_registry.all()
                   if s.category != AssistantToolCategory.READ_ONLY and not s.requires_confirmation}
    assert unconfirmed == NO_CONFIRMATION_BY_DESIGN


def test_read_only_tools_never_ask():
    from app.ai.tool_registry import tool_registry
    from app.core.enums import AssistantToolCategory

    asking = [s.name for s in tool_registry.all()
              if s.category == AssistantToolCategory.READ_ONLY and s.requires_confirmation]
    assert asking == []


def test_confirmation_card_shows_what_is_being_approved():
    """The confirmation event carries the action's arguments, so the user sees WHICH
    request / WHO / WHAT decision — not just the tool name."""
    from app.ai.controller import _confirmation_details

    details = _confirmation_details(
        {"request_id": "REQ-5960", "assignee": "Erin Legal", "notify": True,
         "nested": {"x": 1}, "note": "x" * 500, "blank": "  "}
    )
    assert details["request_id"] == "REQ-5960" and details["assignee"] == "Erin Legal"
    assert details["notify"] == "yes"
    assert "nested" not in details and "blank" not in details
    assert len(details["note"]) == 300
    assert _confirmation_details(None) == {}
    src = inspect.getsource(ai_controller.stream_assistant_run) + inspect.getsource(ai_controller.resume_assistant_run)
    assert src.count('"details": _confirmation_details(') == 2


def test_assistant_errors_shown_to_users_never_carry_internal_detail():
    """Provider/SDK/database errors are logged, not streamed: the user and the run
    row (returned by the runs API) get a generic message with the request id."""
    from fastapi import HTTPException

    from app.assistant.routes import _user_facing_error

    internal = RuntimeError("anthropic.APIStatusError: 401 invalid x-api-key sk-ant-123 at /app/integrations/claude.py")
    msg = _user_facing_error(internal, request_id="req-42", run_id="run-1")
    assert "sk-ant" not in msg and "claude.py" not in msg and "401" not in msg
    assert "req-42" in msg
    # Messages written for users (e.g. the AI budget guard) still pass through.
    budget = HTTPException(429, "Today's AI budget for your organisation is used up.")
    assert _user_facing_error(budget, request_id=None, run_id="run-1") == budget.detail
    for fn in (stream_session,):
        assert 'yield _sse("error", {"message": str(exc)' not in inspect.getsource(fn)
