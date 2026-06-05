"""F-06 admin session access tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_admin_can_read_other_user_session():
    """Org admin reading another user's session returns the row (no 404)."""
    from proposals.audit_2026_06_02.rewrites.F06_assistant_session_admin_override import (  # type: ignore[import-not-found]
        assistant_routes_session_access as mod,
    )

    admin = SimpleNamespace(
        id="admin-1",
        org_id="org-A",
        permission_values={"admin_panel:access", "assistant:use"},
        roles=[SimpleNamespace(name="admin")],
    )
    session = SimpleNamespace(
        id="s-1", org_id="org-A", created_by_user_id="some-other-user"
    )
    db = MagicMock()
    db.get = MagicMock(return_value=session)
    result = mod._get_session_for_user_read(
        db, session_id="s-1", current_user=admin, request_id=None
    )
    assert result is session


def test_admin_cannot_write_other_user_session():
    """``_get_session_for_user_write`` is creator-only; admin gets 404."""
    from fastapi import HTTPException

    from proposals.audit_2026_06_02.rewrites.F06_assistant_session_admin_override import (  # type: ignore[import-not-found]
        assistant_routes_session_access as mod,
    )

    admin = SimpleNamespace(
        id="admin-1",
        org_id="org-A",
        permission_values={"admin_panel:access", "assistant:use"},
        roles=[SimpleNamespace(name="admin")],
    )
    session = SimpleNamespace(
        id="s-1", org_id="org-A", created_by_user_id="some-other-user"
    )
    db = MagicMock()
    db.get = MagicMock(return_value=session)
    raised = False
    try:
        mod._get_session_for_user_write(db, session_id="s-1", current_user=admin)
    except HTTPException as exc:
        raised = exc.status_code == 404
    assert raised


def test_non_admin_other_user_session_still_404():
    """Regular users still cannot read sessions they did not create."""
    from fastapi import HTTPException

    from proposals.audit_2026_06_02.rewrites.F06_assistant_session_admin_override import (  # type: ignore[import-not-found]
        assistant_routes_session_access as mod,
    )

    member = SimpleNamespace(
        id="member-1",
        org_id="org-A",
        permission_values={"assistant:use"},
        roles=[SimpleNamespace(name="member")],
    )
    session = SimpleNamespace(
        id="s-1", org_id="org-A", created_by_user_id="some-other-user"
    )
    db = MagicMock()
    db.get = MagicMock(return_value=session)
    raised = False
    try:
        mod._get_session_for_user_read(
            db, session_id="s-1", current_user=member, request_id=None
        )
    except HTTPException as exc:
        raised = exc.status_code == 404
    assert raised
