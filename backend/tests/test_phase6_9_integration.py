import inspect

from app.admin.routes import upsert_setting
from app.ai.controller import INTERNAL_RESULT_KEYS
from app.ai.tool_registry import tool_registry
from app.ai.tool_runtime import ToolRuntime, tool_runtime
from app.approvals.routes import decide_approval, list_approvals
from app.contract_brain.ingestion import ingest_contract_brain
from app.contract_brain.routes import list_brain_queries
from app.integrations.docusign import DocuSignClient
from app.jobs import tasks
from app.obligations.routes import _get_obligation, list_obligations
from app.renewals.routes import _get_renewal, list_renewals
from app.signatures.routes import list_signature_requests, sync_signature
from app.tabular_review.routes import _get_review_for_user, list_reviews, rerun_cell


def test_phase6_9_assistant_tools_are_enabled_with_expected_policies():
    expected = {
        "ask_contract_brain": ("assistant:use", False),
        "submit_for_approval": ("contract:approve", True),
        "send_for_signature": ("contract:sign", True),
        "extract_obligations": ("obligation:update", False),
        "create_tabular_review": ("assistant:use_ai_tools", False),
        "read_table_cells": ("assistant:use", False),
        "external_share": ("contract_file:share", True),
        "archive_contract": ("contract:archive", True),
    }

    for tool_name, (permission, requires_confirmation) in expected.items():
        spec = tool_registry.get(tool_name)
        assert spec.enabled_by_default is True
        assert spec.required_permission == permission
        assert spec.requires_confirmation is requires_confirmation


def test_tool_runtime_wires_phase6_9_tools_and_supports_async_providers():
    execute_source = inspect.getsource(ToolRuntime._execute_validated)

    assert inspect.iscoroutinefunction(ToolRuntime.execute)
    assert inspect.iscoroutinefunction(ToolRuntime.execute_confirmed)
    for method_name in [
        "_ask_contract_brain",
        "_submit_for_approval",
        "_send_for_signature",
        "_extract_obligations",
        "_create_tabular_review",
        "_read_table_cells",
        "_external_share",
        "_archive_contract",
    ]:
        assert method_name in execute_source
        assert hasattr(tool_runtime, method_name)


def test_later_phase_routes_reuse_contract_and_project_access_checks():
    checked_sources = [
        inspect.getsource(_get_obligation),
        inspect.getsource(list_obligations),
        inspect.getsource(_get_renewal),
        inspect.getsource(list_renewals),
        inspect.getsource(list_signature_requests),
        inspect.getsource(sync_signature),
        inspect.getsource(_get_review_for_user),
        inspect.getsource(list_reviews),
        inspect.getsource(rerun_cell),
        inspect.getsource(list_brain_queries),
    ]
    combined = "\n".join(checked_sources)

    assert "get_contract_for_user" in combined
    assert "accessible_contract_filter(current_user)" in combined
    assert "_review_is_accessible" in combined
    assert "_can_view_brain_query" in combined


def test_approval_decisions_are_assignment_limited():
    list_source = inspect.getsource(list_approvals)
    decide_source = inspect.getsource(decide_approval)

    assert "_can_view_approval" in list_source
    assert "_can_decide_approval" in decide_source
    assert "You are not assigned to decide this approval" in decide_source


def test_contract_brain_ingestion_is_chained_and_stale_not_deleted():
    task_source = inspect.getsource(tasks._run_ai_job)
    helper_source = inspect.getsource(tasks._queue_contract_brain_ingestion)
    ingestion_source = inspect.getsource(ingest_contract_brain)

    assert '_queue_contract_brain_ingestion(db, job=job, reason="after_clause_extraction")' in task_source
    assert '_queue_contract_brain_ingestion(db, job=job, reason="after_obligation_extraction")' in task_source
    assert '_queue_contract_brain_ingestion(db, job=job, reason="after_renewal_extraction")' in task_source
    assert "idempotency_key" in helper_source
    assert "delete(" not in ingestion_source
    assert "edge.is_stale = True" in ingestion_source
    assert "node.is_stale = True" in ingestion_source


def test_docu_sign_real_path_is_not_a_notimplemented_stub():
    source = inspect.getsource(DocuSignClient.create_envelope)
    token_source = inspect.getsource(DocuSignClient._jwt_access_token)

    assert "NotImplementedError" not in source
    assert "oauth/token" in token_source
    assert "urn:ietf:params:oauth:grant-type:jwt-bearer" in token_source
    assert "/envelopes" in source


def test_admin_settings_and_sensitive_tool_outputs_are_audited_or_sanitized():
    admin_source = inspect.getsource(upsert_setting)

    assert "write_audit_log" in admin_source
    assert "admin.setting_updated" in admin_source
    assert "external_share_token" in INTERNAL_RESULT_KEYS
    assert "approval_request_ids" in INTERNAL_RESULT_KEYS
    assert "signature_request_id" in INTERNAL_RESULT_KEYS
