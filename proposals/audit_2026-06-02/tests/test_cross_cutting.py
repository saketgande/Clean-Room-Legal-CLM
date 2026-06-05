"""Tests for the five cross-cutting modules: CC-1..CC-5."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


# ----------------------------------------------------------------------------
# CC-1 — ContractAccessPolicy
# ----------------------------------------------------------------------------


def test_access_policy_org_filter_always_present_for_admin():
    """Even for an org admin, the policy emits a Contract.org_id predicate."""
    from app.contracts.models import Contract
    from app.core.access_policy import ContractAccessPolicy

    admin_user = SimpleNamespace(
        id="admin-1",
        org_id="org-A",
        permission_values={"admin_panel:access"},
        roles=[SimpleNamespace(name="admin")],
    )
    predicate = ContractAccessPolicy.access_predicate(admin_user)
    rendered = str(predicate.compile(compile_kwargs={"literal_binds": True}))
    assert "contract.org_id" in rendered
    assert "'org-A'" in rendered


def test_access_policy_non_admin_membership_clause_present():
    """Non-admin users get the membership/share OR predicate."""
    from app.contracts.models import Contract
    from app.core.access_policy import ContractAccessPolicy

    user = SimpleNamespace(
        id="u-1",
        org_id="org-A",
        permission_values={"contract:read"},
        roles=[SimpleNamespace(name="member")],
    )
    predicate = ContractAccessPolicy.access_predicate(user)
    rendered = str(predicate.compile(compile_kwargs={"literal_binds": True}))
    assert "contract.org_id" in rendered
    # The membership branch joins project_contract / project_member.
    assert "project_contract" in rendered.lower()


def test_can_admin_read_session_requires_same_org():
    """Admins can read in-org sessions; cross-org sessions remain 404."""
    from app.core.access_policy import ContractAccessPolicy

    admin = SimpleNamespace(
        id="admin-1",
        org_id="org-A",
        permission_values={"admin_panel:access"},
        roles=[SimpleNamespace(name="admin")],
    )
    same_org_session = SimpleNamespace(
        id="s-1", org_id="org-A", created_by_user_id="u-other"
    )
    other_org_session = SimpleNamespace(
        id="s-2", org_id="org-B", created_by_user_id="u-other"
    )
    assert ContractAccessPolicy.can_admin_read_session(same_org_session, admin) is True
    assert ContractAccessPolicy.can_admin_read_session(other_org_session, admin) is False


# ----------------------------------------------------------------------------
# CC-2 — session_state.allocate_contract_handle
# ----------------------------------------------------------------------------


def test_session_lock_key_is_stable_and_in_range():
    """The advisory lock key is deterministic per session_id and 63-bit."""
    from app.ai.session_state import _session_lock_key

    key_one = _session_lock_key("session-abc")
    key_two = _session_lock_key("session-abc")
    key_other = _session_lock_key("session-xyz")
    assert key_one == key_two
    assert key_one != key_other
    assert 0 <= key_one < (1 << 63)


# ----------------------------------------------------------------------------
# CC-3 — AssistantToolPolicy
# ----------------------------------------------------------------------------


def test_tool_policy_defaults_to_enabled_when_no_setting(monkeypatch):
    """Tools default-enabled when no AdminSetting row exists for the flag."""
    from app.ai.tool_policy import AssistantToolPolicy
    from app.ai.tool_registry import tool_registry

    # Pick any registered tool with a feature_flag declared.
    specs_with_flag = [
        spec for spec in tool_registry.all() if getattr(spec, "feature_flag", None)
    ]
    if not specs_with_flag:
        return
    spec = specs_with_flag[0]
    AssistantToolPolicy.clear_cache()

    class _DB:
        def scalar(self, _stmt):
            return None

    assert AssistantToolPolicy.is_enabled(spec.name, _DB(), org_id="org-A") is True


def test_tool_policy_disabled_when_setting_value_false(monkeypatch):
    """AdminSetting row with ``{"enabled": false}`` blocks the tool."""
    from app.ai.tool_policy import AssistantToolPolicy
    from app.ai.tool_registry import tool_registry

    specs_with_flag = [
        spec for spec in tool_registry.all() if getattr(spec, "feature_flag", None)
    ]
    if not specs_with_flag:
        return
    spec = specs_with_flag[0]
    AssistantToolPolicy.clear_cache()

    class _Row:
        def __init__(self) -> None:
            self.value = {"enabled": False}

    class _DB:
        def scalar(self, _stmt):
            return _Row()

    assert AssistantToolPolicy.is_enabled(spec.name, _DB(), org_id="org-A") is False


# ----------------------------------------------------------------------------
# CC-4 — streaming budget / disconnect / serializer
# ----------------------------------------------------------------------------


def test_streaming_budget_token_overflow_raises():
    """``check_token_budget`` raises ``CostBudgetExceeded`` past the cap."""
    from app.core.streaming import CostBudgetExceeded, StreamingBudget

    budget = StreamingBudget(max_tokens=100)
    budget.record_tokens(60)
    budget.check_token_budget()  # under budget
    budget.record_tokens(60)
    raised = False
    try:
        budget.check_token_budget()
    except CostBudgetExceeded:
        raised = True
    assert raised


def test_streaming_budget_loop_detector_raises():
    """``record_tool_call`` triggers ``ToolLoopDetected`` after the threshold."""
    from app.core.streaming import StreamingBudget, ToolLoopDetected

    budget = StreamingBudget(max_repeat_per_tool=3)
    raised = False
    try:
        for _ in range(3):
            budget.record_tool_call(tool_name="read_contract", idempotency_key="k-1")
    except ToolLoopDetected:
        raised = True
    assert raised


def test_sse_serialize_rejects_repr_leak():
    """``sse_serialize`` raises rather than dumping a Python repr()."""
    from app.core.streaming import sse_serialize

    class _Opaque:
        def __init__(self) -> None:
            self.x = 1

    raised = False
    try:
        sse_serialize("event", {"value": _Opaque()})
    except TypeError:
        raised = True
    assert raised


def test_sse_serialize_handles_datetime():
    """``sse_serialize`` serializes datetimes via ``isoformat()``."""
    from app.core.streaming import sse_serialize

    payload = {"when": datetime(2026, 6, 2, 12, 0, tzinfo=UTC)}
    out = sse_serialize("event", payload)
    assert "2026-06-02T12:00:00" in out


# ----------------------------------------------------------------------------
# CC-5 — idempotency builder
# ----------------------------------------------------------------------------


def test_minute_bucket_truncates_to_window():
    """``minute_bucket`` collapses two times within the same window to one key."""
    from app.jobs.idempotency import minute_bucket

    t1 = datetime(2026, 6, 2, 12, 1, 30, tzinfo=UTC)
    t2 = datetime(2026, 6, 2, 12, 4, 59, tzinfo=UTC)
    t3 = datetime(2026, 6, 2, 12, 6, 0, tzinfo=UTC)
    assert minute_bucket(t1, window=5) == minute_bucket(t2, window=5)
    assert minute_bucket(t1, window=5) != minute_bucket(t3, window=5)


def test_build_idempotency_key_is_deterministic():
    """``build_idempotency_key`` produces a stable string for stable inputs."""
    from app.jobs.idempotency import build_idempotency_key

    key_one = build_idempotency_key(
        "obligation_extraction",
        version_id="v-1",
        snapshot_id="s-1",
        trigger="manual",
        debounce_token="user-1:202606021205",
    )
    key_two = build_idempotency_key(
        "obligation_extraction",
        version_id="v-1",
        snapshot_id="s-1",
        trigger="manual",
        debounce_token="user-1:202606021205",
    )
    assert key_one == key_two


def test_build_auto_brain_ingestion_key_no_reason_suffix():
    """Auto brain-ingestion keys ignore the upstream-trigger reason."""
    from app.jobs.idempotency import build_auto_brain_ingestion_key

    key = build_auto_brain_ingestion_key(version_id="v-1", snapshot_id="s-1")
    assert "after_clause_extraction" not in key
    assert "after_obligation_extraction" not in key
    assert "after_renewal_extraction" not in key
