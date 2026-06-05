"""F-03 access-filter tenant-leak tests."""

from __future__ import annotations

from types import SimpleNamespace


def test_admin_filter_constrains_to_org_id():
    """The admin code path no longer returns bare ``true()``."""
    from app.contracts.access import accessible_contract_filter

    admin = SimpleNamespace(
        id="admin-1",
        org_id="org-A",
        permission_values={"admin_panel:access"},
        roles=[SimpleNamespace(name="admin")],
    )
    predicate = accessible_contract_filter(admin)
    rendered = str(predicate.compile(compile_kwargs={"literal_binds": True}))
    # The org filter must be present; a bare true() would only render "true" or "1".
    assert "contract.org_id" in rendered
    assert "'org-A'" in rendered
    assert rendered.strip().lower() not in {"true", "1"}


def test_non_admin_filter_includes_membership_predicate():
    """Non-admin users get the OR membership/share predicate."""
    from app.contracts.access import accessible_contract_filter

    user = SimpleNamespace(
        id="u-1",
        org_id="org-A",
        permission_values={"contract:read"},
        roles=[SimpleNamespace(name="member")],
    )
    predicate = accessible_contract_filter(user)
    rendered = str(predicate.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "contract.org_id" in rendered
    assert "owner_user_id" in rendered or "created_by_user_id" in rendered
