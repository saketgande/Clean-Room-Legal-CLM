"""Which database the test suite may use.

The suite commits rows: many tests open their own ``SessionLocal()`` and many drive the
API through ``TestClient``. Run against the dev database that fills it with test users,
roles, approval chains and contracts (it once held 1,079 test contracts, 686 test users
and thousands of orphaned jobs). So outside CI the suite uses a sibling ``*_test``
database and refuses to start against anything else.

Pure function of the environment so it can be tested without touching a database.
"""

from collections.abc import Mapping

from sqlalchemy.engine import make_url

_FALLBACK = "postgresql+psycopg://legal_clm:legal_clm@localhost:5432/legal_clm"


def resolve_test_database_url(env: Mapping[str, str]) -> str:
    """The URL the suite will use; raises ``RuntimeError`` when it would not be a test DB.

    * ``TEST_DATABASE_URL`` wins when set.
    * On CI (``CI=true``, set by GitHub Actions) the configured ``DATABASE_URL`` is kept:
      the CI service database is its own throwaway.
    * Anywhere else the database name of ``DATABASE_URL`` gets a ``_test`` suffix, and
      the result must end in ``_test``.
    """
    ci = env.get("CI") == "true"
    explicit = env.get("TEST_DATABASE_URL")
    url = make_url(explicit or env.get("DATABASE_URL") or _FALLBACK)
    if not explicit and not ci:
        name = url.database or "legal_clm"
        if not name.endswith("_test"):
            url = url.set(database=f"{name}_test")
    if not ci and not (url.database or "").endswith("_test"):
        raise RuntimeError(
            f"refusing to run the tests against database {url.database!r}: outside CI the "
            "name must end in '_test' (see CLAUDE.md, 'Tests and lint')."
        )
    return url.render_as_string(hide_password=False)
