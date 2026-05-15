from datetime import UTC, datetime

import pytest

from app.core.audit import compute_audit_row_hash
from app.core.models import AuditLog, _prevent_audit_log_delete, _prevent_audit_log_update
from app.auth.models import Permission, Role, User
from app.core.config import Settings, validate_runtime_settings
from app.core.enums import UserStatus
from app.core.security import create_access_token, create_token_secret, decode_access_token, hash_token


def test_access_token_includes_type_and_jti():
    token = create_access_token("user-1", {"org_id": "org-1", "role_id": "role-1"})

    payload = decode_access_token(token)

    assert payload["sub"] == "user-1"
    assert payload["org_id"] == "org-1"
    assert payload["role_id"] == "role-1"
    assert payload["typ"] == "access"
    assert payload["jti"]


def test_runtime_settings_reject_default_production_secrets():
    settings = Settings(environment="production")

    with pytest.raises(RuntimeError):
        validate_runtime_settings(settings)


def test_active_role_limits_permission_values():
    read_permission = Permission(value="contract:read")
    admin_permission = Permission(value="admin_panel:access")
    member_role = Role(id="member-role", org_id="org-1", name="member")
    admin_role = Role(id="admin-role", org_id="org-1", name="admin")
    member_role.permissions = [read_permission]
    admin_role.permissions = [admin_permission]
    user = User(
        id="user-1",
        org_id="org-1",
        email="user@example.com",
        full_name="User Example",
        hashed_password="hash",
        status=UserStatus.ACTIVE,
        active_role_id="member-role",
    )
    user.roles = [member_role, admin_role]

    assert user.permission_values == {"contract:read"}


def test_token_hashing_and_api_key_prefix():
    api_key = create_token_secret(prefix="clm_")

    assert api_key.startswith("clm_")
    assert hash_token(api_key) == hash_token(api_key)
    assert hash_token(api_key) != api_key


def test_audit_log_has_no_onupdate_timestamp():
    assert AuditLog.__table__.c.updated_at.onupdate is None


def test_audit_log_hash_includes_previous_hash_and_payload():
    created_at = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    first = AuditLog(
        id="audit-1",
        action="contract.uploaded",
        resource_type="contract",
        resource_id="contract-1",
        org_id="org-1",
        actor_user_id="user-1",
        request_id="request-1",
        before=None,
        after={"title": "A"},
        metadata_json={"source": "test"},
        prev_hash=None,
        created_at=created_at,
        updated_at=created_at,
    )
    first.row_hash = compute_audit_row_hash(first)
    second = AuditLog(
        id="audit-2",
        action="contract.updated",
        resource_type="contract",
        resource_id="contract-1",
        org_id="org-1",
        actor_user_id="user-1",
        request_id="request-2",
        before={"title": "A"},
        after={"title": "B"},
        metadata_json={"source": "test"},
        prev_hash=first.row_hash,
        created_at=created_at,
        updated_at=created_at,
    )

    assert compute_audit_row_hash(second) == compute_audit_row_hash(second)
    original_hash = compute_audit_row_hash(second)
    second.after = {"title": "tampered"}
    assert compute_audit_row_hash(second) != original_hash


def test_audit_log_orm_guards_reject_mutation():
    with pytest.raises(RuntimeError):
        _prevent_audit_log_update(None, None, object())
    with pytest.raises(RuntimeError):
        _prevent_audit_log_delete(None, None, object())
