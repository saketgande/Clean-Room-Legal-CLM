"""Test-suite environment bootstrap.

These ``os.environ.setdefault`` calls run at import time — BEFORE any ``app.*``
module is imported and ``Settings()`` is constructed — so the suite is
deterministic regardless of a developer's local ``.env``.

Two things matter here:

* The production code now defaults the mock integrations OFF and treats
  ``DATABASE_URL`` as required (no hardcoded dev fallback). The suite opts the
  mocks back ON and supplies a local Postgres URL so tests don't reach out to
  real Claude/DocuSign/Reducto/Resend or a missing database.
* ``setdefault`` is used deliberately: an explicit value already exported in
  the environment (e.g. CI pointing at its own Postgres) still wins.

pydantic-settings resolves env vars (``os.environ``) ahead of the ``.env``
file, so values set here also override anything stale in a local ``.env``.
"""

import os
from collections.abc import Generator

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MOCK_CLAUDE", "true")
os.environ.setdefault("MOCK_DOCUSIGN", "true")
os.environ.setdefault("MOCK_REDUCTO", "true")
os.environ.setdefault("MOCK_RESEND", "true")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://legal_clm:legal_clm@localhost:5432/legal_clm",
)

import pytest  # noqa: E402  (must follow the env setdefault calls above)


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
