"""Test-suite environment bootstrap.

These ``os.environ.setdefault`` calls run at import time — BEFORE any ``app.*``
module is imported and ``Settings()`` is constructed — so the suite is
deterministic regardless of a developer's local ``.env``.

Two things matter here:

* The production code now defaults the mock integrations OFF and treats
  ``DATABASE_URL`` as required (no hardcoded dev fallback). The suite opts the
  mocks back ON and supplies a local Postgres URL so tests don't reach out to
  real Claude/DocuSign/Reducto/Resend/SendGrid or a missing database.
* ``DATABASE_URL``/``ENVIRONMENT`` use ``setdefault``: an explicit value already
  exported (e.g. CI pointing at its own Postgres) still wins.
* The mocks are FORCED on, not defaulted. The documented way to run the suite is
  ``docker compose exec backend pytest``, and the dev container exports
  ``MOCK_CLAUDE=false`` with real API keys — ``setdefault`` would let every test
  run spend real Claude/Reducto calls.

pydantic-settings resolves env vars (``os.environ``) ahead of the ``.env``
file, so values set here also override anything stale in a local ``.env``.
"""

import os
from collections.abc import Generator

os.environ.setdefault("ENVIRONMENT", "test")
for _mock in ("MOCK_CLAUDE", "MOCK_DOCUSIGN", "MOCK_REDUCTO", "MOCK_RESEND", "MOCK_SENDGRID"):
    os.environ[_mock] = "true"
# Forced off for the same reason the mocks are forced on: the dev container
# exports DISABLE_RBAC=true, and inheriting it would make every permission
# assertion in the suite pass vacuously against a permission system that is
# switched off. CI (no compose override) already runs enforced; pin it so the
# documented in-container run tests the same thing CI does.
os.environ["DISABLE_RBAC"] = "false"
# Same leak, same fix: the dev container exports INTAKE_DEMO_AGENTS=true, and the
# production-config tests build a locked-down Settings() that reads the env for
# anything they don't name — so inheriting it made them fail in-container while
# passing in CI. Pin the production value.
os.environ["INTAKE_DEMO_AGENTS"] = "false"
# Same leak again: the dev container turns the Word editor on with its published
# local secret, which the production-config tests rightly refuse. CI has no
# editor; tests that need one switch it on themselves (test_word_editor.py).
os.environ.pop("ONLYOFFICE_URL", None)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://legal_clm:legal_clm@localhost:5432/legal_clm",
)

import pytest

# --- DI migration test harness ---------------------------------------------
# Added alongside the contracts/ DI conversion (see backend/DI_MIGRATION.md).
# The suite previously had no TestClient / dependency_overrides fixtures — all
# existing mocking is monkeypatch on the `settings` singleton or class
# internals (see e.g. test_phase6_9_integration.py). These fixtures are what
# make the DI conversion provable: a route's injected service can now be
# swapped for a fake via `app.dependency_overrides`, per-test, without
# touching module globals.

@pytest.fixture
def db_session() -> Generator:
    """A real DB session for tests that exercise a service against Postgres.

    Rolls back on teardown so tests don't leave rows behind. Requires the
    DATABASE_URL configured above to point at a reachable Postgres instance —
    consistent with the rest of this suite's assumptions.
    """
    from app.core.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client() -> Generator:
    """A TestClient bound to the real app, for exercising routes end-to-end
    with dependency_overrides. Clears any overrides left by a test on exit so
    they can't leak into the next one."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def override_dependency(client):
    """override_dependency(dependency, fake) swaps a FastAPI dependency for the
    lifetime of the test. `dependency` is the provider function itself (e.g.
    `get_contract_service`), `fake` is the replacement callable/value."""
    from app.main import app

    def _override(dependency, fake):
        app.dependency_overrides[dependency] = lambda: fake

    return _override
