"""The suite must never write into the dev database.

It used to: the container pins DATABASE_URL to the dev database and conftest only set a
default, so every full run left test users, roles, approval chains, contracts and orphaned
jobs behind (1,079 test contracts, 686 test users at the time this guard was added).
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.database import SessionLocal
from tests._testdb import resolve_test_database_url

DEV = "postgresql+psycopg://legal_clm:legal_clm@postgres:5432/legal_clm"


def test_the_suite_is_connected_to_a_test_database():
    """Failure mode: the suite silently runs against (and pollutes) the dev database."""
    name = make_url(settings.database_url).database
    assert name.endswith("_test") or os.environ.get("CI") == "true", name
    with SessionLocal() as s:
        assert s.scalar(text("select current_database()")) == name


def test_outside_ci_the_dev_url_becomes_the_sibling_test_database():
    """Failure mode: the container's dev DATABASE_URL is used as-is, as it was before."""
    url = make_url(resolve_test_database_url({"DATABASE_URL": DEV}))
    assert url.database == "legal_clm_test"
    assert (url.host, url.username, url.password) == ("postgres", "legal_clm", "legal_clm")


def test_an_url_that_already_ends_in_test_is_left_alone():
    """Failure mode: a second '_test' suffix is appended on every run."""
    url = make_url(resolve_test_database_url({"DATABASE_URL": DEV.replace("/legal_clm", "/legal_clm_test")}))
    assert url.database == "legal_clm_test"


def test_ci_keeps_its_own_throwaway_database():
    """Failure mode: CI breaks because its service database is renamed to '<name>_test'."""
    assert make_url(resolve_test_database_url({"CI": "true", "DATABASE_URL": DEV})).database == "legal_clm"


def test_an_explicit_test_database_url_wins():
    """Failure mode: TEST_DATABASE_URL is ignored in favour of DATABASE_URL."""
    explicit = "postgresql+psycopg://u:p@db.example:5433/other_test"
    got = make_url(resolve_test_database_url({"TEST_DATABASE_URL": explicit, "DATABASE_URL": DEV}))
    assert (got.host, got.port, got.database) == ("db.example", 5433, "other_test")


def test_an_explicit_url_that_is_not_a_test_database_is_refused_outside_ci():
    """Failure mode: someone points TEST_DATABASE_URL at the dev database and the suite runs there."""
    with pytest.raises(RuntimeError, match="refusing to run the tests"):
        resolve_test_database_url({"TEST_DATABASE_URL": DEV})


def test_the_database_url_is_the_one_in_the_environment_when_nothing_is_set():
    """Failure mode: a missing DATABASE_URL crashes instead of using the local default test database."""
    assert make_url(resolve_test_database_url({})).database == "legal_clm_test"
