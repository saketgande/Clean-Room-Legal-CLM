"""F-02 token-decision security tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_uniform_error_for_all_token_failure_modes(monkeypatch):
    """Not-found, used, and expired all surface the same 401 body."""
    from fastapi import HTTPException, status

    import app.approvals.service as svc

    # Capture the three failure paths.
    raised_codes: list[int] = []
    raised_details: list[str] = []

    def _capture(exc: HTTPException) -> None:
        raised_codes.append(exc.status_code)
        raised_details.append(exc.detail)

    # Case 1: token row does not exist.
    db = MagicMock()
    db.scalar = MagicMock(return_value=None)
    try:
        svc.redeem_token_decision(
            db,
            token="aaaaaaaaaaaaaaaa",
            decision="approve",
            comment=None,
        )
    except HTTPException as exc:
        _capture(exc)

    # Case 2: token row exists but is already used.
    row_used = SimpleNamespace(
        org_id="org-A",
        used_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        approval_request_id="ar-1",
        intended_approver_email="approver@example.com",
    )
    db2 = MagicMock()
    db2.scalar = MagicMock(return_value=row_used)
    try:
        svc.redeem_token_decision(
            db2,
            token="aaaaaaaaaaaaaaaa",
            decision="approve",
            comment=None,
        )
    except HTTPException as exc:
        _capture(exc)

    # Case 3: token row exists but is expired.
    row_expired = SimpleNamespace(
        org_id="org-A",
        used_at=None,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        approval_request_id="ar-1",
        intended_approver_email="approver@example.com",
    )
    db3 = MagicMock()
    db3.scalar = MagicMock(return_value=row_expired)
    try:
        svc.redeem_token_decision(
            db3,
            token="aaaaaaaaaaaaaaaa",
            decision="approve",
            comment=None,
        )
    except HTTPException as exc:
        _capture(exc)

    assert raised_codes == [status.HTTP_401_UNAUTHORIZED] * 3
    assert raised_details == ["Invalid or expired approval token"] * 3


def test_token_payload_caps_comment_length():
    """``TokenDecisionPayload.comment`` rejects values longer than 4_000 chars."""
    from pydantic import ValidationError

    from app.approvals.routes import TokenDecisionPayload

    long_comment = "x" * 5_000
    raised = False
    try:
        TokenDecisionPayload(
            token="a" * 32,
            decision="approve",
            comment=long_comment,
        )
    except ValidationError:
        raised = True
    assert raised


def test_token_payload_caps_token_length():
    """``TokenDecisionPayload.token`` rejects implausibly short or long values."""
    from pydantic import ValidationError

    from app.approvals.routes import TokenDecisionPayload

    raised = False
    try:
        TokenDecisionPayload(token="short", decision="approve")
    except ValidationError:
        raised = True
    assert raised
