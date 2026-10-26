"""Per-user rate limits on the agent-facing intake routes (feature 007, T008).

An agent in a retry loop must not be able to flood the legal queue or the
database. The limiter key is the authenticated user (all of a user's keys share a
bucket) and the check runs after authentication but before the handler body, so a
429 stores nothing.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import func, select

from app.core.config import Settings, settings
from app.intake import routes
from app.intake.models import IntakeRequest
from tests.test_intake_agent_identity import PREFIX, World, file_request


@pytest.fixture
def world():
    w = World()
    try:
        yield w
    finally:
        w.cleanup()


CREATE = "@limiter.limit(settings.rate_limit_intake_create)"
READ = "@limiter.limit(settings.rate_limit_intake_read)"
DOCUMENT = "@limiter.limit(settings.rate_limit_intake_document)"


@pytest.mark.parametrize("endpoint, decorator", [
    ("create_request", CREATE),
    ("list_forms", READ),
    ("list_my_requests", READ),
    ("get_request", READ),
    ("sla_legs", READ),
    ("list_request_documents", READ),
    ("upload_document", DOCUMENT),
])
def test_each_agent_route_carries_its_limiter_directly_above_the_def(endpoint, decorator):
    """slowapi is opt-in per route, so a missing or displaced decorator means the
    route is silently unlimited. It also needs request + response parameters."""
    lines = inspect.getsource(routes).splitlines()
    definition = next(i for i, line in enumerate(lines) if line.startswith(f"def {endpoint}("))
    assert lines[definition - 1].strip() == decorator
    params = inspect.signature(getattr(routes, endpoint)).parameters
    assert "request" in params and "response" in params


def test_defaults_are_the_frozen_values_and_parse():
    from limits import parse_many

    assert settings.rate_limit_intake_create == "10/minute;100/hour"
    assert settings.rate_limit_intake_read == "120/minute;3000/hour"
    assert settings.rate_limit_intake_document == "30/minute;300/hour"
    for value in (settings.rate_limit_intake_create, settings.rate_limit_intake_read,
                  settings.rate_limit_intake_document):
        assert parse_many(value)


def test_limits_come_from_the_environment(monkeypatch):
    """AC-12: operators tune the numbers with env vars, not code."""
    monkeypatch.setenv("RATE_LIMIT_INTAKE_CREATE", "3/minute")
    monkeypatch.setenv("RATE_LIMIT_INTAKE_READ", "7/minute")
    monkeypatch.setenv("RATE_LIMIT_INTAKE_DOCUMENT", "5/hour")
    fresh = Settings()
    assert fresh.rate_limit_intake_create == "3/minute"
    assert fresh.rate_limit_intake_read == "7/minute"
    assert fresh.rate_limit_intake_document == "5/hour"


def test_the_eleventh_filing_in_a_minute_is_429_and_stores_nothing(client, world):
    """AC-11: ten filings pass; the eleventh is refused with a wait time the agent
    can act on, and no eleventh request exists."""
    agent = world.agent()
    for n in range(10):
        res = file_request(client, agent, description=f"limit test {n}")
        assert res.status_code == 201, (n, res.text)

    refused = file_request(client, agent, description="limit test 11")
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    body = refused.json()
    assert isinstance(body["retry_after_seconds"], int) and body["retry_after_seconds"] >= 1
    assert body["detail"].startswith("Too many requests")

    world.db.rollback()
    count = world.db.scalar(select(func.count()).select_from(IntakeRequest).where(
        IntakeRequest.org_id == world.org.id))
    assert count == 10


def test_reads_are_not_throttled_at_agent_polling_rates(client, world):
    """AC-38: polling GET /requests/mine fifteen times in a row all succeed."""
    agent = world.agent()
    for n in range(15):
        res = client.get(f"{PREFIX}/intake/requests/mine", headers=agent.headers)
        assert res.status_code == 200, (n, res.text)


def test_one_users_budget_does_not_throttle_another(client, world):
    """The key is the user, not the address: every test client shares one IP."""
    a, b = world.agent("a"), world.agent("b")
    for n in range(10):
        assert file_request(client, a, description=f"a{n}").status_code == 201
    assert file_request(client, a, description="a11").status_code == 429
    assert file_request(client, b, description="b1").status_code == 201
