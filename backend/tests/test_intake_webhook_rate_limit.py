"""The public intake webhooks share one Redis-backed rate-limit budget.

They used to call a bespoke in-memory sliding window kept in a module-level
dict. Two problems: the budget was per-process, so the systemd unit's
`--workers 4` quietly turned "30 a minute" into 120; and it sat outside the
shared limiter, so it did not benefit from the proxy-trust wiring the rest of
the app depends on to see a real client IP rather than nginx's.
"""

import inspect

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rate_limit import limiter, rate_limit_key
from app.intake import ingest, routes


def test_the_bespoke_per_process_limiter_is_gone():
    """Guards it creeping back. A module-level dict of timestamps cannot be a
    rate limit for a process that is replicated."""
    assert not hasattr(ingest, "rate_limit")
    assert not hasattr(ingest, "_hits")


@pytest.mark.parametrize("endpoint", ["email_webhook", "teams_webhook"])
def test_each_public_webhook_carries_the_shared_limiter(endpoint):
    """The decorator has to be on THIS route, not merely somewhere above it in
    the file: slowapi is opt-in, so a public endpoint without one is simply
    unlimited."""
    lines = inspect.getsource(routes).splitlines()
    definition = next(
        i for i, line in enumerate(lines)
        if line.startswith((f"def {endpoint}(", f"async def {endpoint}("))
    )
    assert lines[definition - 1].strip() == "@limiter.limit(settings.rate_limit_intake_webhook)"


def test_the_limit_is_configurable_and_sane():
    assert settings.rate_limit_intake_webhook.endswith("/minute")


def test_counters_are_shared_across_workers_not_per_process():
    """The property the in-memory window could not have: one budget for the
    whole deployment rather than one per worker. Asserts the storage backend
    itself, not just the configured URI — a limiter that silently fell back to
    memory would still report the URI."""
    assert type(limiter._storage).__name__ == "RedisStorage"
    assert limiter._storage_uri == settings.redis_url


def test_an_unauthenticated_caller_keys_on_its_own_address(monkeypatch):
    """Webhooks carry no authenticated user, so the key falls through to the
    remote address — which is the real client only because uvicorn is run with
    proxy headers trusted from the addresses in FORWARDED_ALLOW_IPS. Without
    that, every caller shares nginx's bucket and one noisy sender starves
    everyone."""
    class _Req:
        def __init__(self):
            self.state = type("S", (), {})()
            self.client = type("C", (), {"host": "203.0.113.9"})()
            self.headers = {}

        def __getattr__(self, name):
            raise AttributeError(name)

    assert rate_limit_key(_Req()) == "203.0.113.9"


def test_an_authenticated_caller_keys_on_the_user_not_the_address():
    """Guards a shared office NAT collapsing every colleague into one bucket."""
    class _Req:
        def __init__(self):
            self.state = type("S", (), {"current_user": type("U", (), {"id": "u-1"})()})()
            self.client = type("C", (), {"host": "203.0.113.9"})()
            self.headers = {}

    assert rate_limit_key(_Req()) == "user:u-1"


def test_the_webhook_still_answers_when_the_limit_is_not_hit():
    """The positive control: adding a limiter must not break the endpoint."""
    from app.main import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            f"{settings.api_v1_prefix}/intake/email-webhook",
            json={"from_email": "a@b.example", "subject": "hi", "body": "x"},
        )
    # 422 (no external_message_id) proves the handler ran — not 429, and not a
    # crash from a decorator applied to the wrong signature.
    assert response.status_code == 422
