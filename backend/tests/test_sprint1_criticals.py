"""Regression tests for the Sprint-1 critical fixes (audit 2026-06-02).

Covers the live-code behavior changed by findings F-02, F-03 and F-04. Matches
the repository's existing pure-function / lightweight-stub test style (no live DB
or TestClient harness is configured for this suite).
"""

from __future__ import annotations

import asyncio
import types
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException, status

from app.ai.tool_policy import flag_value_is_enabled, is_tool_enabled
from app.ai.tool_registry import tool_registry
from app.approvals.service import redeem_token_decision
from app.contracts import access as access_module
from app.contracts.access import accessible_contract_filter


# --- F-03: accessible_contract_filter must always be org-scoped -------------


def _compile(expr) -> str:
    return str(expr.compile()).lower()


def test_f03_admin_filter_is_org_scoped_not_tautology(monkeypatch):
    """An org admin's contract filter must constrain org_id, not collapse to true()."""
    # is_org_admin was deleted in the access-control consolidation; the filter
    # now delegates to app/core/policy.accessible_filter, which uses is_admin.
    monkeypatch.setattr("app.core.policy.is_admin", lambda user: True)
    admin = types.SimpleNamespace(id="u-admin", org_id="org-A", roles=[], department=None)

    sql = _compile(accessible_contract_filter(admin))

    assert "org_id" in sql, f"admin filter dropped the tenant boundary: {sql!r}"
    # The pre-fix bug returned a bare true(); a tautology has no column reference.
    assert sql.strip() not in {"true", "1 = 1"}


def test_f03_non_admin_filter_also_org_scoped(monkeypatch):
    """Non-admins keep their ownership/membership predicate AND an org_id scope."""
    monkeypatch.setattr("app.core.policy.is_admin", lambda user: False)
    member = types.SimpleNamespace(id="u-1", org_id="org-A", roles=[], department=None)

    sql = _compile(accessible_contract_filter(member))

    assert "org_id" in sql
    assert "owner_user_id" in sql  # the ownership branch survived the refactor


# --- F-04: feature flags are honored at runtime ----------------------------


class _StubDB:
    """Minimal Session stand-in exposing only scalar(), used to drive is_tool_enabled."""

    def __init__(self, scalar_result: object | None, *, forbid_query: bool = False) -> None:
        self._result = scalar_result
        self._forbid_query = forbid_query

    def scalar(self, *_args: object, **_kwargs: object) -> object | None:
        if self._forbid_query:
            raise AssertionError("AdminSetting must not be queried when feature_flag is None")
        return self._result


def test_f04_tool_without_flag_uses_static_default_and_skips_db():
    """A tool with no feature_flag never hits the DB and follows enabled_by_default."""
    spec = tool_registry.get("read_contract")  # registered without a feature_flag
    assert spec.feature_flag is None
    assert is_tool_enabled(_StubDB(None, forbid_query=True), org_id="org-A", spec=spec) is True


def test_f04_admin_setting_false_disables_a_flagged_tool():
    """An AdminSetting of value=False disables a flagged tool even if default-on."""
    spec = tool_registry.get("generate_contract_docx")  # has feature_flag
    assert spec.feature_flag is not None
    disabled = _StubDB(types.SimpleNamespace(value=False))
    assert is_tool_enabled(disabled, org_id="org-A", spec=spec) is False


def test_f04_absent_setting_falls_back_to_default():
    """With no AdminSetting row, a flagged tool falls back to its static default."""
    spec = tool_registry.get("generate_contract_docx")
    assert is_tool_enabled(_StubDB(None), org_id="org-A", spec=spec) is spec.enabled_by_default


def test_f04_string_false_value_disables_not_failopen():
    """A flag stored as the JSON STRING "false" must disable the tool, not fail open (bool("false") is True)."""
    spec = tool_registry.get("generate_contract_docx")
    for falsey in ("false", "False", "0", "off", "no", ""):
        db = _StubDB(types.SimpleNamespace(value=falsey))
        assert is_tool_enabled(db, org_id="org-A", spec=spec) is False, falsey


def test_f04_flag_value_coercion_matrix():
    """flag_value_is_enabled coerces JSON bool/int/str without the bool('false') fail-open."""
    assert flag_value_is_enabled(True, default=False) is True
    assert flag_value_is_enabled(False, default=True) is False
    assert flag_value_is_enabled("false", default=True) is False
    assert flag_value_is_enabled("true", default=False) is True
    assert flag_value_is_enabled(0, default=True) is False
    assert flag_value_is_enabled(1, default=False) is True
    assert flag_value_is_enabled(None, default=True) is True  # null value carries no decision → default


# --- F-02: token-decision failures are a single uniform 401 (no oracle) -----


class _TokenStubDB:
    """Session stand-in for redeem_token_decision's two scalar() lookups: the
    first (ApprovalToken) returns the preset row, the second (ApprovalRequest)
    returns None — so a valid-but-unusable token exercises the 4th failure
    branch (request_missing_or_org_mismatch) instead of falling through."""

    def __init__(self, row: object | None) -> None:
        self._row = row
        self._calls = 0

    def scalar(self, *_args: object, **_kwargs: object) -> object | None:
        self._calls += 1
        return self._row if self._calls == 1 else None

    def get(self, *_args: object, **_kwargs: object) -> None:  # pragma: no cover - unused here
        return None


def _redeem(db: object) -> HTTPException:
    # redeem_token_decision is async; every token-failure branch raises before
    # the first await, so asyncio.run propagates the HTTPException unchanged.
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            redeem_token_decision(db, token="x" * 16, decision="approve", comment=None)
        )
    return exc_info.value


def test_f02_missing_used_and_expired_tokens_all_return_identical_401():
    """Fake, already-used, and expired tokens must be indistinguishable to a caller."""
    now = datetime.now(UTC)
    used_row = types.SimpleNamespace(used_at=now, expires_at=now + timedelta(hours=1))
    expired_row = types.SimpleNamespace(used_at=None, expires_at=now - timedelta(hours=1))

    # Valid-looking token whose ApprovalRequest is missing/org-mismatched: _TokenStubDB.get()
    # returns None, exercising the 4th failure branch (request_missing_or_org_mismatch).
    valid_row = types.SimpleNamespace(
        used_at=None,
        expires_at=now + timedelta(hours=1),
        approval_request_id="req-1",
        org_id="org-A",
    )

    responses = [
        _redeem(_TokenStubDB(None)),           # token not found
        _redeem(_TokenStubDB(used_row)),       # already used
        _redeem(_TokenStubDB(expired_row)),    # expired
        _redeem(_TokenStubDB(valid_row)),      # request missing / org mismatch (4th branch)
    ]

    statuses = {r.status_code for r in responses}
    details = {r.detail for r in responses}
    assert statuses == {status.HTTP_401_UNAUTHORIZED}, statuses
    assert details == {"Invalid or expired approval token"}, details
