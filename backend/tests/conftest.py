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

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MOCK_CLAUDE", "true")
os.environ.setdefault("MOCK_DOCUSIGN", "true")
os.environ.setdefault("MOCK_REDUCTO", "true")
os.environ.setdefault("MOCK_RESEND", "true")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://legal_clm:legal_clm@localhost:5432/legal_clm",
)
