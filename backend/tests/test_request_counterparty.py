"""Counterparty files on the request page (011 T003): GET /intake/requests/{id}/counterparty.

The read is worked out per viewer: a contract or earlier request the viewer may not open must not
be listed, counted or hinted at in the note, and the old org-wide dossier stored on past requests
must never leave the server. Everything runs in one rolled-back transaction in an organisation of
its own.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.auth.models import User
from app.contracts.models import Contract
from app.contracts.service import get_contract_for_user
from app.core import org_access
from app.core.database import engine, utcnow
from app.core.deps import get_current_user, get_db
from app.core.models import AuditLog, ResourceTimelineEvent
from app.intake import screening
from app.intake.models import IntakeRequest
from app.intake.service import get_request
from app.organizations.models import Organization
from app.walls.models import EthicalWall, EthicalWallPrincipal

STAFF = {"intake:create", "intake:read", "contract:read"}
EMPLOYEE = {"intake:create"}


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


class World:
    def __init__(self, db: Session, monkeypatch):
        self.db = db
        org = Organization(name="Cp Co", slug=f"cp-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
        self.org_id = org.id
        self.perms: dict[str, set[str]] = {}
        monkeypatch.setattr(
            User, "permission_values", property(lambda u: self.perms.get(u.id, set()))
        )
        monkeypatch.setattr(
            org_access, "effective_permission_values",
            lambda db, *, user, at=None: self.perms.get(user.id, set()),
        )
        self.staff = self.user("Sam Staff", STAFF)
        self.requester = self.user("Rita Requester", EMPLOYEE)

    def user(self, name: str, perms: set[str]) -> User:
        u = User(org_id=self.org_id, email=f"{uuid.uuid4().hex[:8]}@example.com",
                 full_name=name, hashed_password="hash", status="active")
        self.db.add(u)
        self.db.flush()
        self.perms[u.id] = set(perms)
        return u

    def contract(self, cp: str = "Initech Software Ltd", *, title: str | None = None,
                 stage: str = "active", ago: int = 0, owner: User | None = None, **kw) -> Contract:
        c = Contract(org_id=self.org_id, title=title or f"Deal {uuid.uuid4().hex[:5]}",
                     counterparty_name=cp, lifecycle_stage=stage,
                     owner_user_id=(owner or self.staff).id,
                     created_at=utcnow() - timedelta(days=ago), **kw)
        self.db.add(c)
        self.db.flush()
        return c

    def request(self, cp: str | None = "Initech Software Ltd", *, requester: User | None = None,
                contract: Contract | None = None, ago: int = 0, **kw) -> IntakeRequest:
        r = IntakeRequest(
            org_id=self.org_id, ref=f"REQ-{uuid.uuid4().hex[:6]}",
            requester_user_id=(requester or self.requester).id, type_label="NDA", description="d",
            parties=[{"name": cp, "role": "counterparty"}] if cp else [],
            submitted_at=utcnow() - timedelta(days=ago),
            contract_id=contract.id if contract else None, **kw)
        self.db.add(r)
        self.db.flush()
        return r

    def wall(self, contract: Contract, user: User) -> None:
        w = EthicalWall(org_id=self.org_id, name="w", scope_type="contract", scope_id=contract.id)
        self.db.add(w)
        self.db.flush()
        self.db.add(EthicalWallPrincipal(org_id=self.org_id, wall_id=w.id,
                                         principal_type="user", principal_id=user.id))
        self.db.flush()
        self.db.expire_all()


@pytest.fixture
def w(db, monkeypatch):
    return World(db, monkeypatch)


@pytest.fixture
def api(w, db, client):
    from app.main import app

    state = {"user": w.staff}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    def get(user: User, rid: str, path: str = "/counterparty"):
        """rid "" targets the list; path "" targets the request itself."""
        state["user"] = user
        return client.get(f"/api/v1/intake/requests{'/' + rid if rid else ''}{path}")

    return get


def read(w: World, user: User, r: IntakeRequest) -> dict:
    return screening.ScreeningService(w.db).visible_counterparty(user, r)


# --- the leak -----------------------------------------------------------------------------


def test_walled_contract_is_absent_from_list_counts_note_and_every_request_payload(w, api):
    """The defect: a contract the viewer is walled off from showed up in an org-wide count (and
    the note) sent to everyone. C must change nothing the walled viewer receives."""
    r = w.request()
    w.contract(title="Visible one")
    hidden = w.contract(title="Secret Initech deal")
    w.wall(hidden, w.staff)

    res = api(w.staff, r.id)

    body = res.json()
    assert res.status_code == 200
    assert [c["title"] for c in body["contracts"]] == ["Visible one"]
    assert body["summary"] == {"contracts": 1, "active": 1, "requests": 0}
    assert body["note"] == "Known counterparty: 1 prior contract(s)."
    raw = res.text + api(w.staff, r.id, "").text + api(w.staff, "", "").text
    assert hidden.id not in raw and "Secret" not in raw


def test_old_stored_dossier_never_leaves_the_server(w, api):
    """The defect: rows screened before this change still hold screening.relationship with
    org-wide counts and a total value; get, list, pool and my-work all serialize the row, so
    they must all drop it while keeping the rest of the screening result."""
    r = w.request(screening={
        "status": "done", "counterparty": "Initech Software Ltd",
        "relationship": {"prior_contracts": 9, "total_value": 123456,
                         "note": "Known counterparty: 9 prior contract(s) (~$123K)."}})

    one = api(w.staff, r.id, "").json()
    listing = api(w.staff, "", "").json()
    mine = api(w.requester, "", "/mine").json()

    assert one["screening"] == {"status": "done", "counterparty": "Initech Software Ltd"}
    for rows in (listing, mine):
        assert any(x["id"] == r.id for x in rows)
        assert "relationship" not in str(rows) and "123456" not in str(rows)


def test_pool_and_my_work_serializers_drop_the_dossier(w, db):
    """The pool and my-work payloads are built from serialize_request too; the helper must be the
    single place the stored dossier is removed, including when screening is empty."""
    from app.intake.service import _public_screening, serialize_request

    r = w.request(screening={"status": "done", "relationship": {"prior_contracts": 3}})
    assert "relationship" not in serialize_request(db, r)["screening"]
    assert _public_screening(None) is None
    assert _public_screening({"status": "pending"}) == {"status": "pending"}


def test_screening_result_and_audit_payload_carry_no_org_wide_count(w, db):
    """The defect: compute_screening stored the dossier and the audit row recorded
    prior_contracts (an org-wide number) for anyone with audit access."""
    r = w.request()
    w.contract()

    result = screening.compute_screening(db, r)
    screening.run_screening(db, r, actor_user_id=w.staff.id)

    assert "relationship" not in result and result["counterparty"] == "Initech Software Ltd"
    row = db.scalars(select(AuditLog).where(
        AuditLog.action == "intake.relationship.checked", AuditLog.resource_id == r.id)).one()
    assert row.after == {"counterparty": "Initech Software Ltd"}


# --- who sees what ------------------------------------------------------------------------


def test_every_matching_contract_hidden_reads_as_a_new_counterparty(w):
    """When the viewer can see nothing, the card must say so with zeros, not hint that files exist."""
    r = w.request()
    for _ in range(2):
        c = w.contract()
        w.wall(c, w.staff)

    out = read(w, w.staff, r)

    assert out["summary"] == {"contracts": 0, "active": 0, "requests": 0}
    assert out["contracts"] == [] and out["requests"] == []
    assert out["note"] == "New counterparty. No other contracts or requests on file."


def test_own_contract_and_other_counterparties_are_excluded_and_nda_counted(w):
    """The request's own contract, a different counterparty and the request itself are not
    'earlier files'; the NDA count in the note comes from visible titles only."""
    own = w.contract(title="Own contract")
    r = w.request(contract=own)
    w.contract(title="Mutual NDA - Initech")
    w.contract(title="MSA")
    w.contract("Globex Corp", title="Elsewhere")

    out = read(w, w.staff, r)

    assert sorted(c["title"] for c in out["contracts"]) == ["MSA", "Mutual NDA - Initech"]
    assert out["note"] == "Known counterparty: 2 prior contract(s), 1 NDA on file."
    assert out["requests"] == []


def test_requester_sees_only_their_own_earlier_requests_and_staff_sees_all(w):
    """A non-staff requester must not learn that a colleague raised a request about the same
    counterparty; staff see the whole queue."""
    other = w.user("Other Member", EMPLOYEE)
    r = w.request()
    mine = w.request(ago=3)
    theirs = w.request(requester=other, ago=2)

    assert [x["id"] for x in read(w, w.requester, r)["requests"]] == [mine.id]
    assert read(w, w.requester, r)["summary"]["requests"] == 1
    assert {x["id"] for x in read(w, w.staff, r)["requests"]} == {mine.id, theirs.id}


def test_request_tied_to_a_walled_contract_is_dropped_for_the_walled_viewer(w):
    """An earlier request is a doorway to its contract; a viewer walled off that contract must
    not see or count it, while the other viewer's data is untouched."""
    c = w.contract()
    w.wall(c, w.staff)
    r = w.request()
    w.request(contract=c, ago=2)
    free = w.request(ago=1)

    assert [x["id"] for x in read(w, w.staff, r)["requests"]] == [free.id]
    assert read(w, w.staff, r)["summary"]["requests"] == 1


def test_no_contract_read_means_no_contracts_and_no_count(w):
    """Without contract:read the contract page refuses, so listing (or counting) contracts would
    be a dead link and a hint; earlier requests still show."""
    viewer = w.user("Req Viewer", {"intake:create", "intake:read"})
    r = w.request()
    w.contract(owner=viewer)
    earlier = w.request(ago=1)

    out = read(w, viewer, r)

    assert out["contracts"] == [] and out["summary"] == {"contracts": 0, "active": 0, "requests": 1}
    assert [x["id"] for x in out["requests"]] == [earlier.id]
    assert out["note"] == "Known counterparty: 1 prior request(s)."


# --- matching, ordering, caps ---------------------------------------------------------------


@pytest.mark.parametrize("ours,theirs,hit", [
    ("Umbrella Corp", "Umbrella Corporation", True),
    ("Umbrella Pharma", "Acme Pharma", False),
    ("Acme, Inc.", "ACME Limited", True),
    ("Société Générale SA", "Société Générale", True),
    ("Initech-Software Ltd", "Initech Software", True),
    ("AB Co", "Cab Ltd", False),  # ILIKE '%ab%' pulls it in; _name_match must still refuse
])
def test_name_match_is_unchanged_and_the_sql_prefilter_never_drops_a_hit(w, ours, theirs, hit):
    """The ILIKE prefilter is only an optimisation: it must never remove a pair the unchanged
    _name_match accepts (accents, punctuation, suffixes, short tokens)."""
    r = w.request(ours)
    c = w.contract(theirs)

    got = [x["id"] for x in read(w, w.staff, r)["contracts"]]

    assert (got == [c.id]) is hit and (not hit or got == [c.id])
    assert screening._name_match(ours, theirs) is hit


def test_ordering_live_first_then_ended_newest_first_and_active_counts_only_live(w):
    """Live files must come before closed, archived and expired ones, newest first within each;
    'active' counts only stage active and not ended."""
    r = w.request()
    old_live = w.contract(title="live-old", ago=30)
    new_live = w.contract(title="live-new", ago=1)
    closed = w.contract(title="closed", stage="closed", ago=0)
    expired = w.contract(title="expired", ago=5, expiration_date=utcnow().date() - timedelta(days=1))
    archived = w.contract(title="archived", ago=9, archived=True)

    out = read(w, w.staff, r)

    assert [c["title"] for c in out["contracts"]] == [
        "live-new", "live-old", "closed", "expired", "archived"]
    assert {c["title"]: c["ended"] for c in out["contracts"]} == {
        "live-new": False, "live-old": False, "closed": True, "expired": True, "archived": True}
    assert out["summary"] == {"contracts": 5, "active": 2, "requests": 0}
    assert {old_live.id, new_live.id, closed.id, expired.id, archived.id} == {
        c["id"] for c in out["contracts"]}


def test_lists_are_capped_at_100_but_counts_cover_the_whole_visible_set(w):
    """The page shows 'show more' over at most 100 rows; the summary must still be the true total."""
    r = w.request()
    for i in range(103):
        w.db.add(Contract(org_id=w.org_id, title=f"c{i}", counterparty_name="Initech Software Ltd",
                          lifecycle_stage="active", owner_user_id=w.staff.id))
    for _ in range(101):
        w.request(ago=1)
    w.db.flush()

    out = read(w, w.staff, r)

    assert len(out["contracts"]) == 100 and out["summary"]["contracts"] == 103
    assert len(out["requests"]) == 100 and out["summary"]["requests"] == 101


def test_money_is_per_contract_and_no_total_exists(w):
    """Values are in mixed currencies, so each contract carries its own and nothing sums them."""
    r = w.request()
    w.contract(value_amount=1000, currency="INR")
    w.contract(value_amount=5, currency="USD")

    out = read(w, w.staff, r)

    assert sorted((c["value"], c["currency"]) for c in out["contracts"]) == [
        (5.0, "USD"), (1000.0, "INR")]
    assert "total" not in str(out).lower() and "~$" not in out["note"]


def test_every_listed_item_opens_for_the_same_viewer(w):
    """A listed file that then 404s is a broken link; the lists must be a subset of what the
    contract and request pages allow."""
    r = w.request()
    ok = w.contract()
    hidden = w.contract()
    w.wall(hidden, w.staff)
    w.request(ago=1)

    out = read(w, w.staff, r)

    for c in out["contracts"]:
        assert get_contract_for_user(w.db, contract_id=c["id"], user=w.staff).id == ok.id
    for x in out["requests"]:
        assert get_request(w.db, user=w.staff, request_id=x["id"]).id == x["id"]


# --- the route ----------------------------------------------------------------------------


def test_route_shape_no_party_and_nothing_written(w, api, db):
    """The page codes against the frozen keys; a request with no party gets name null and an
    empty card; reading writes no audit or timeline row."""
    r = w.request(None)
    w.request()
    before = (db.scalar(select(func.count()).select_from(AuditLog)),
              db.scalar(select(func.count()).select_from(ResourceTimelineEvent)))

    res = api(w.staff, r.id)
    full = api(w.staff, w.request().id)

    assert res.status_code == 200
    assert res.json() == {"name": None, "summary": {"contracts": 0, "active": 0, "requests": 0},
                          "note": None, "contracts": [], "requests": []}
    assert set(full.json()) == {"name", "summary", "note", "contracts", "requests"}
    assert set(full.json()["requests"][0]) == {"id", "ref", "type_label", "status", "date"}
    assert before == (db.scalar(select(func.count()).select_from(AuditLog)),
                      db.scalar(select(func.count()).select_from(ResourceTimelineEvent)))


def test_route_contract_item_keys_are_the_frozen_ones(w, api):
    """The frontend type lists exactly these contract keys."""
    r = w.request()
    w.contract(value_amount=10, currency="INR")

    [item] = api(w.staff, r.id).json()["contracts"]

    assert set(item) == {"id", "title", "contract_type", "stage", "value", "currency",
                         "created_at", "ended"}


def test_route_404_for_other_members_request_and_other_org_and_403_without_permission(w, api, db):
    """Same row gate as the request itself, and the same permission as the rest of the page."""
    other = w.user("Other Member", EMPLOYEE)
    mine = w.request(requester=w.requester)
    assert api(other, mine.id).status_code == 404
    assert api(w.staff, str(uuid.uuid4())).status_code == 404
    org2 = Organization(name="Other Co", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(org2)
    db.flush()
    foreign = User(org_id=org2.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="F",
                   hashed_password="hash", status="active")
    db.add(foreign)
    db.flush()
    w.perms[foreign.id] = STAFF
    assert api(foreign, mine.id).status_code == 404
    assert api(w.user("Nobody", set()), mine.id).status_code == 403
