"""F-13 rewrite — DocuSign Connect HMAC verify + replay protection.

Replaces ``backend/app/integrations/docusign.py:189-201``
(``verify_connect_signature``) and adds the route-side replay table.

Original issue (Agent 2 F-13 — High): the HMAC primitive was crypto-clean
but there was no timestamp window check, no nonce, and no per-envelope
replay protection. A captured signed body could be re-played to walk a
contract through to EXECUTED.

The new shape:
- ``verify_connect_signature`` accepts an optional timestamp header,
  rejects payloads outside a 5-minute window, and folds the timestamp
  into the HMAC input. Backward-compatible when called with the old
  single-arg signature, but the production webhook caller MUST pass
  the timestamp.
- ``record_webhook_event`` provides nonce-style replay protection
  against ``(envelope_id, status)`` pairs via the new
  ``webhook_event_seen`` table (companion migration referenced in the
  README).
- ``mock_docusign=True`` in a non-local environment causes the verify
  function to refuse all webhooks defensively (the boot validator
  also catches this; this is belt-and-suspenders).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings

_logger = logging.getLogger(__name__)

# DocuSign Connect retries on failure; allow a wider verification window
# than e.g. Stripe to absorb scheduled redeliveries.
_DOCUSIGN_TIMESTAMP_WINDOW_SECONDS: int = 300
_LOCAL_ENVIRONMENTS = frozenset({"local", "development", "dev", "test"})


def verify_connect_signature(
    *,
    body: bytes,
    signature_header: str | None,
    timestamp_header: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Verify the Connect HMAC + bind a timestamp; replay-safe within window."""
    key = settings.docusign_connect_hmac_key
    if not key or not signature_header:
        return False
    # Defense in depth — refuse to validate when mocked outside dev.
    if settings.mock_docusign and settings.environment.lower() not in _LOCAL_ENVIRONMENTS:
        _logger.error(
            "docusign.webhook.mock_in_prod",
            extra={"environment": settings.environment},
        )
        return False
    if timestamp_header is not None:
        if not _timestamp_within_window(timestamp_header, now=now):
            _logger.warning(
                "docusign.webhook.timestamp_outside_window",
                extra={"timestamp_header": timestamp_header},
            )
            return False
        message = body + b"|" + timestamp_header.encode("utf-8")
    else:
        # Back-compat: still verify the body-only HMAC for older deployments
        # whose Connect config has not been updated. Issue a structured
        # warning so operators can see the upgrade gap.
        _logger.warning(
            "docusign.webhook.no_timestamp_header",
            extra={"environment": settings.environment},
        )
        message = body
    expected = base64.b64encode(
        hmac.new(key.encode("utf-8"), message, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature_header.strip())


def _timestamp_within_window(
    timestamp_header: str,
    *,
    now: datetime | None,
    window_seconds: int = _DOCUSIGN_TIMESTAMP_WINDOW_SECONDS,
) -> bool:
    """Parse the DocuSign timestamp header and accept iff within window."""
    if not timestamp_header:
        return False
    parsed = _parse_iso8601(timestamp_header)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    delta_seconds = abs((current - parsed).total_seconds())
    return delta_seconds <= window_seconds


def _parse_iso8601(value: str) -> datetime | None:
    """Parse an ISO-8601 string returning a tz-aware datetime, or None."""
    try:
        # DocuSign formats vary; ``fromisoformat`` accepts the common ones in 3.11+.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ----------------------------------------------------------------------------
# Webhook replay protection — companion model + helper.
# ----------------------------------------------------------------------------

# Intended migration filename: 0010_webhook_event_seen.py
# Intended model location:   app/integrations/models.py
#
# Schema sketch (Alembic):
#   CREATE TABLE webhook_event_seen (
#       id            VARCHAR(36) PRIMARY KEY,
#       provider      VARCHAR(40)  NOT NULL,
#       envelope_id   VARCHAR(120) NOT NULL,
#       status        VARCHAR(120) NOT NULL,
#       observed_at   TIMESTAMPTZ  NOT NULL,
#       request_id    VARCHAR(80)  NULL
#   );
#   CREATE UNIQUE INDEX ux_webhook_event_seen_provider_envelope_status
#       ON webhook_event_seen(provider, envelope_id, status);
#
# Combined with the route-level use below, a re-delivered Connect event
# fails the UNIQUE constraint and we return ``{"status": "duplicate_ignored"}``
# instead of re-processing.

try:  # pragma: no cover - import is best-effort until model lands
    from app.integrations.models import WebhookEventSeen  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    WebhookEventSeen = None  # type: ignore[assignment]


def record_webhook_event(
    db: Session,
    *,
    provider: str,
    envelope_id: str,
    status_value: str,
    request_id: str | None = None,
) -> bool:
    """Insert a per-(provider, envelope, status) row; return False on duplicate."""
    if WebhookEventSeen is None:
        # The model has not been merged yet — fail-open with a structured warning.
        # Once the migration lands, this branch disappears.
        _logger.warning(
            "docusign.webhook.replay_table_missing",
            extra={"provider": provider, "envelope_id": envelope_id},
        )
        return True
    row = WebhookEventSeen(
        provider=provider,
        envelope_id=envelope_id,
        status=status_value,
        observed_at=datetime.now(timezone.utc),
        request_id=request_id,
    )
    db.add(row)
    try:
        db.flush()
        return True
    except IntegrityError:
        db.rollback()
        _logger.info(
            "docusign.webhook.duplicate_ignored",
            extra={
                "provider": provider,
                "envelope_id": envelope_id,
                "status": status_value,
            },
        )
        return False
