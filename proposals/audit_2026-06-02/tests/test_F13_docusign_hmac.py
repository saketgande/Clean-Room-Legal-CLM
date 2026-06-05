"""F-13 DocuSign webhook HMAC + replay tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from unittest.mock import patch


def _sign(body: bytes, *, key: str, timestamp: str | None) -> str:
    message = body if timestamp is None else body + b"|" + timestamp.encode("utf-8")
    return base64.b64encode(
        hmac.new(key.encode("utf-8"), message, hashlib.sha256).digest()
    ).decode("ascii")


def test_verify_rejects_payload_without_key():
    """No HMAC key configured → verify returns False even with a header."""
    from proposals.audit_2026_06_02.rewrites.F13_docusign_hmac_replay import (  # type: ignore[import-not-found]
        integrations_docusign_verify as mod,
    )

    with patch.object(mod.settings, "docusign_connect_hmac_key", None):
        assert (
            mod.verify_connect_signature(body=b"x", signature_header="anything")
            is False
        )


def test_verify_accepts_valid_signature_with_timestamp():
    """With a fresh timestamp and matching HMAC, verify returns True."""
    from proposals.audit_2026_06_02.rewrites.F13_docusign_hmac_replay import (  # type: ignore[import-not-found]
        integrations_docusign_verify as mod,
    )

    key = "super-secret-hmac-key-for-tests"
    body = b'{"event":"envelope-completed"}'
    now = datetime.now(UTC)
    timestamp = now.isoformat()
    signature = _sign(body, key=key, timestamp=timestamp)
    with patch.object(mod.settings, "docusign_connect_hmac_key", key):
        with patch.object(mod.settings, "mock_docusign", False):
            assert (
                mod.verify_connect_signature(
                    body=body,
                    signature_header=signature,
                    timestamp_header=timestamp,
                    now=now,
                )
                is True
            )


def test_verify_rejects_old_timestamp():
    """A timestamp 10 minutes in the past is outside the 5-minute window."""
    from proposals.audit_2026_06_02.rewrites.F13_docusign_hmac_replay import (  # type: ignore[import-not-found]
        integrations_docusign_verify as mod,
    )

    key = "super-secret-hmac-key-for-tests"
    body = b'{"event":"envelope-completed"}'
    now = datetime.now(UTC)
    old_timestamp = (now - timedelta(minutes=10)).isoformat()
    signature = _sign(body, key=key, timestamp=old_timestamp)
    with patch.object(mod.settings, "docusign_connect_hmac_key", key):
        with patch.object(mod.settings, "mock_docusign", False):
            assert (
                mod.verify_connect_signature(
                    body=body,
                    signature_header=signature,
                    timestamp_header=old_timestamp,
                    now=now,
                )
                is False
            )


def test_verify_rejects_mock_in_non_local():
    """``mock_docusign=True`` outside local env returns False."""
    from proposals.audit_2026_06_02.rewrites.F13_docusign_hmac_replay import (  # type: ignore[import-not-found]
        integrations_docusign_verify as mod,
    )

    with patch.object(mod.settings, "docusign_connect_hmac_key", "k"):
        with patch.object(mod.settings, "mock_docusign", True):
            with patch.object(mod.settings, "environment", "production"):
                assert (
                    mod.verify_connect_signature(
                        body=b"x", signature_header="anything"
                    )
                    is False
                )
