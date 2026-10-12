"""AI usage page: exam-traffic split and model-aware pricing (feature
005-ai-eval-baseline, T007 — FR-14, FR-34, FR-41).

``GET /analytics/ai-usage`` now takes ``traffic=real|exam``. Exam calls carry
``a_i_call_log.eval_run_id`` and must stay out of the real figures (the default),
while ``traffic=exam`` shows them on their own. ``token_cost`` is the one price
table the exam reuses, so it is pinned per model, for cache tokens, and for the
half-price batch rows.

Each test builds its own organization and its own rows, so it only ever reads
what it wrote even though the dev database holds real ledger rows; everything
rolls back at teardown. The HTTP path runs through the real app with the DB
session and the current user swapped in, so the 422 / 403 are FastAPI's own.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy.orm import Session

from app.analytics.routes import token_cost
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.core.database import engine, new_uuid
from app.core.deps import get_current_user, get_db
from app.core.enums import UserStatus
from app.core.models import AICallLog
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization

M = 1_000_000


@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _org(db: Session) -> Organization:
    org = Organization(id=new_uuid(), name="usage", slug=f"usage-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _user(db: Session, org: Organization, *, permissions: list[str]) -> User:
    unit = OrgUnit(org_id=org.id, name="HQ", parent_id=None)
    db.add(unit)
    role = Role(org_id=org.id, name=f"role-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    for value in permissions:
        perm = db.query(Permission).filter(Permission.value == value).one_or_none()
        if perm is None:
            perm = Permission(value=value)
            db.add(perm)
        role.permissions.append(perm)
    db.add(role)
    user = User(
        org_id=org.id,
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        full_name="Usage Tester",
        hashed_password="hash",
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    db.flush()
    db.add(UserRoleGrant(user_id=user.id, role_id=role.id, org_id=org.id, org_unit_id=unit.id))
    db.flush()
    return user


def _call(db: Session, org_id: str, **kw) -> AICallLog:
    row = AICallLog(
        org_id=org_id,
        model=kw.pop("model", "claude-sonnet-4-6"),
        prompt_key=kw.pop("prompt_key", "exam_op"),
        prompt_tokens=kw.pop("prompt_tokens", 0),
        completion_tokens=kw.pop("completion_tokens", 0),
        **kw,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def admin(db):
    org = _org(db)
    return _user(db, org, permissions=["admin_panel:access"])


@pytest.fixture
def get_usage(db, client):
    """get_usage(user, **query) -> the raw response, with the test's session and
    user in place of the real login."""
    from app.main import app

    def _get(user: User, **query):
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: user
        return client.get("/api/v1/analytics/ai-usage", params=query)

    return _get


# --- token_cost: one price table, per model --------------------------------


@pytest.mark.parametrize(
    "model,in_rate,out_rate",
    [
        ("claude-sonnet-4-6", 3.0, 15.0),
        ("claude-sonnet-5-5", 2.0, 10.0),
        ("claude-haiku-4-5", 1.0, 5.0),
        ("claude-opus-5-5", 4.0, 20.0),
        # A dated snapshot must price as its own model, not fall to the family.
        ("claude-sonnet-5-5-20260915", 2.0, 10.0),
        ("claude-haiku-4-5-20251001", 1.0, 5.0),
        # Older family members keep the family price.
        ("claude-sonnet-4-5-20250929", 3.0, 15.0),
        # Unrecognized or missing model: assume Sonnet tier rather than $0.
        ("some-future-model", 3.0, 15.0),
        (None, 3.0, 15.0),
    ],
)
def test_token_cost_prices_each_model_at_its_own_rate(model, in_rate, out_rate):
    """Failure mode: one flat Sonnet rate for every model, so Sonnet 5.5 and Haiku
    runs were over-priced and the exam's dollars disagreed with the invoice."""
    assert token_cost(M, 0, model) == pytest.approx(in_rate)
    assert token_cost(0, M, model) == pytest.approx(out_rate)
    assert token_cost(M, M, model) == pytest.approx(in_rate + out_rate)


@pytest.mark.parametrize(
    "model,in_rate",
    [("claude-sonnet-4-6", 3.0), ("claude-sonnet-5-5", 2.0), ("claude-haiku-4-5", 1.0),
     ("claude-opus-5-5", 4.0)],
)
def test_token_cost_cache_tokens_scale_off_the_models_input_rate(model, in_rate):
    """Failure mode: cache multipliers applied to a fixed $3 instead of the model's
    own input rate."""
    assert token_cost(0, 0, model, cache_read=M) == pytest.approx(in_rate * 0.1)
    assert token_cost(0, 0, model, cache_write=M) == pytest.approx(in_rate * 1.25)


@pytest.mark.parametrize(
    "model", ["claude-sonnet-4-6", "claude-sonnet-5-5", "claude-haiku-4-5", "claude-opus-5-5"]
)
def test_token_cost_batched_halves_every_token_type(model):
    """Failure mode: the 50% batch discount applied to input/output only, or not at
    all, so batch exam runs were priced as if they were live (FR-41)."""
    kwargs = {"cache_read": 3 * M, "cache_write": 2 * M}
    live = token_cost(M, M, model, **kwargs)
    assert token_cost(M, M, model, **kwargs, batched=True) == pytest.approx(live / 2)
    assert token_cost(0, 0, model, cache_read=M, batched=True) == pytest.approx(
        token_cost(0, 0, model, cache_read=M) / 2
    )
    assert token_cost(0, 0, model, cache_write=M, batched=True) == pytest.approx(
        token_cost(0, 0, model, cache_write=M) / 2
    )


def test_token_cost_is_not_batched_unless_asked():
    """Failure mode: batched defaulting on, silently halving every live cost."""
    assert inspect.signature(token_cost).parameters["batched"].default is False
    assert token_cost(M, 0, "claude-sonnet-4-6", batched=False) == pytest.approx(3.0)


# --- the traffic filter ------------------------------------------------------


def test_default_view_leaves_exam_rows_out(db, admin, get_usage):
    """Failure mode: exam spend leaking into the real usage figures everyone reads
    (FR-34) — the default must be real traffic only."""
    _call(db, admin.org_id, model="claude-sonnet-4-6", prompt_tokens=M)
    _call(db, admin.org_id, model="claude-sonnet-5-5", prompt_tokens=M, eval_run_id="run-1")

    for query in ({}, {"traffic": "real"}):
        r = get_usage(admin, **query)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["traffic"] == "real"
        assert body["totals"]["calls"] == 1
        assert body["totals"]["cost"] == pytest.approx(3.0)
        assert [o["model"] for o in body["by_operation"]] == ["claude-sonnet-4-6"]


def test_exam_view_shows_only_exam_rows(db, admin, get_usage):
    """Failure mode: the exam toggle mixing real calls back in, so exam cost cannot
    be read on its own."""
    _call(db, admin.org_id, model="claude-sonnet-4-6", prompt_tokens=M)
    _call(db, admin.org_id, model="claude-sonnet-5-5", prompt_tokens=M, eval_run_id="run-1")

    r = get_usage(admin, traffic="exam")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["traffic"] == "exam"
    assert body["totals"]["calls"] == 1
    assert body["totals"]["cost"] == pytest.approx(2.0)
    assert [o["model"] for o in body["by_operation"]] == ["claude-sonnet-5-5"]
    assert sum(d["calls"] for d in body["daily"]) == 1
    assert sum(c["calls"] for c in body["by_category"]) == 1


def test_traffic_filter_keeps_the_org_scope(db, admin, get_usage):
    """Failure mode: the new filter replacing the org filter, so one org's usage page
    would show another org's rows."""
    other = _org(db)
    _call(db, other.id, prompt_tokens=M)
    _call(db, other.id, prompt_tokens=M, eval_run_id="run-other")
    _call(db, admin.org_id, prompt_tokens=M)

    assert get_usage(admin).json()["totals"]["calls"] == 1
    assert get_usage(admin, traffic="exam").json()["totals"]["calls"] == 0


def test_exam_view_with_no_exam_rows_is_empty_not_an_error(admin, get_usage):
    """Failure mode: a division or empty-aggregate error before any exam has run."""
    body = get_usage(admin, traffic="exam").json()
    assert body["totals"]["calls"] == 0
    assert body["totals"]["cost"] == 0.0
    assert body["totals"]["cost_per_call"] == 0.0
    assert body["by_operation"] == [] and body["daily"] == []


def test_a_batched_and_a_live_row_fold_into_one_entry_with_the_right_total(
    db, admin, get_usage
):
    """Failure mode: grouping by batched splitting one operation into two table rows
    (shape change for the frontend), or pricing the folded group at one flag so the
    batch half-price is lost or applied to the live call (FR-41)."""
    org = admin.org_id
    # Live: 1M in + 100k out = 2.00 + 1.00 at Sonnet 5.5 list price.
    _call(db, org, model="claude-sonnet-5-5", prompt_tokens=M, completion_tokens=100_000,
          eval_run_id="run-1", latency_ms=1200.0)
    # Batched: (1M in + 100k out + 1M cache read) = (2.00 + 1.00 + 0.20) at half price.
    _call(db, org, model="claude-sonnet-5-5", prompt_tokens=M, completion_tokens=100_000,
          cache_read_input_tokens=M, eval_run_id="run-1", batched=True)

    body = get_usage(admin, traffic="exam").json()

    assert len(body["by_operation"]) == 1
    op = body["by_operation"][0]
    assert op["calls"] == 2
    assert op["prompt_tokens"] == 2 * M
    assert op["completion_tokens"] == 200_000
    assert op["cost"] == pytest.approx(3.0 + 1.6)
    assert op["cost_per_call"] == pytest.approx((3.0 + 1.6) / 2, abs=1e-4)
    # The batched row has no latency; it must not drag the live average to zero.
    assert op["avg_latency_ms"] == 1200.0
    assert body["totals"]["cost"] == pytest.approx(4.6)
    assert body["totals"]["cache_read_tokens"] == M
    assert [c["cost"] for c in body["by_category"]] == [pytest.approx(4.6)]
    assert len(body["daily"]) == 1
    assert body["daily"][0]["calls"] == 2
    assert body["daily"][0]["cost"] == pytest.approx(4.6)


def test_rates_list_model_rows_ahead_of_family_fallbacks(admin, get_usage):
    """Failure mode: the family fallback listed (and matched) before the model row, so
    Sonnet 5.5 resolves to the older Sonnet price."""
    rates = get_usage(admin).json()["rates"]
    families = [r["model_family"] for r in rates]
    assert families.index("claude-sonnet-5-5") < families.index("claude-sonnet")
    assert families.index("claude-opus-5-5") < families.index("claude-opus")
    assert {"model_family": "claude-sonnet-5-5", "input_per_m": 2.0, "output_per_m": 10.0} in rates


# --- gate and validation -----------------------------------------------------


def test_unknown_traffic_value_is_422(admin, get_usage):
    """Failure mode: a typo'd toggle silently falling back to some default view."""
    assert get_usage(admin, traffic="everything").status_code == 422


def test_days_bounds_are_still_enforced(admin, get_usage):
    """Failure mode: the new parameter disturbing the existing 1..365 window check."""
    assert get_usage(admin, days=0).status_code == 422
    assert get_usage(admin, days=366).status_code == 422


def test_user_without_admin_panel_access_gets_403(db, get_usage, monkeypatch):
    """Failure mode: the usage figures (exam spend included) readable without
    admin_panel:access (AC-34)."""
    denials = []
    # The real denial writes an audit row on its own session; keep the dev DB clean.
    monkeypatch.setattr("app.core.authz.record_decision", lambda **kw: denials.append(kw))
    plain = _user(db, _org(db), permissions=["contract:read"])

    for query in ({}, {"traffic": "exam"}):
        r = get_usage(plain, **query)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "Missing permission: admin_panel:access"
    assert {d["action"] for d in denials} == {"admin_panel:access"}


def test_unauthenticated_request_gets_401(client):
    """Failure mode: the endpoint reachable with no login."""
    assert client.get("/api/v1/analytics/ai-usage").status_code == 401

