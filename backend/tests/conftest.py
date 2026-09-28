"""Test-suite environment bootstrap.

These ``os.environ.setdefault`` calls run at import time — BEFORE any ``app.*``
module is imported and ``Settings()`` is constructed — so the suite is
deterministic regardless of a developer's local ``.env``.

Two things matter here:

* The production code now defaults the mock integrations OFF and treats
  ``DATABASE_URL`` as required (no hardcoded dev fallback). The suite opts the
  mocks back ON and supplies a local Postgres URL so tests don't reach out to
  real Claude/DocuSign/Reducto/Resend or a missing database.
* ``DATABASE_URL``/``ENVIRONMENT`` use ``setdefault``: an explicit value already
  exported (e.g. CI pointing at its own Postgres) still wins.
* The mocks are FORCED on, not defaulted. The documented way to run the suite is
  ``docker compose exec backend pytest``, and the dev container exports
  ``MOCK_CLAUDE=false`` with real API keys — ``setdefault`` would let every test
  run spend real Claude/Databricks calls.

pydantic-settings resolves env vars (``os.environ``) ahead of the ``.env``
file, so values set here also override anything stale in a local ``.env``.
"""

import os

os.environ.setdefault("ENVIRONMENT", "test")
for _mock in ("MOCK_CLAUDE", "MOCK_DATABRICKS", "MOCK_DOCUSIGN", "MOCK_REDUCTO", "MOCK_RESEND"):
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
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://legal_clm:legal_clm@localhost:5432/legal_clm",
)
