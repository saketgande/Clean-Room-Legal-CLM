"""Request activity read (011 T001): GET /intake/requests/{id}/activity.

The endpoint turns timeline events into plain sentences on the server so no event code, id or
JSON ever reaches the page, and it must not widen who can see what: contract events obey the
contract page's rule, comments obey the run's rule, and reading writes nothing. Everything runs
in one rolled-back transaction in an organisation of its own.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.auth.models import User
from app.contracts.models import Contract
from app.core import org_access
from app.core.database import engine, utcnow
from app.core.deps import get_current_user, get_db
from app.core.models import AuditLog, ResourceTimelineEvent
from app.intake import activity
from app.intake.models import IntakeHandoff, IntakeRequest
from app.organizations.models import Organization
from app.walls.models import EthicalWall, EthicalWallPrincipal
from app.workflows.models import WorkflowRun

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
        org = Organization(name="Activity Co", slug=f"activity-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
        self.org_id = org.id
        self.perms: dict[str, set[str]] = {}
        # Users here carry no roles; hand them permissions the way a role would.
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

    def contract(self) -> Contract:
        c = Contract(org_id=self.org_id, title="Initech MSA", owner_user_id=self.staff.id)
        self.db.add(c)
        self.db.flush()
        return c

    def request(self, *, requester: User | None = None, contract: Contract | None = None):
        r = IntakeRequest(
            org_id=self.org_id, ref=f"REQ-{uuid.uuid4().hex[:6]}", requester_user_id=(
                requester or self.requester).id, type_label="NDA", description="d",
            submitted_at=utcnow(), contract_id=contract.id if contract else None)
        self.db.add(r)
        self.db.flush()
        return r

    def event(self, rtype: str, rid: str, etype: str, title: str = "T", *, ago: int = 0,
              actor: User | None = None, **details) -> ResourceTimelineEvent:
        e = ResourceTimelineEvent(
            org_id=self.org_id, resource_type=rtype, resource_id=rid, event_type=etype,
            title=title, details=details or None, created_by_user_id=actor.id if actor else None,
            created_at=utcnow() - timedelta(seconds=ago))
        self.db.add(e)
        self.db.flush()
        return e

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


def read(w: World, user: User, r: IntakeRequest) -> dict:
    return activity.request_activity(w.db, user=user, request=r)


def sentences(out: dict) -> list[str]:
    return [i["sentence"] for i in out["items"]]


# --- wording ------------------------------------------------------------------------------


def test_happy_path_sentences_order_sources_and_who(w):
    """The page must show the table's wording, newest first, with a person's name (not an id)
    for people's actions and 'AI' for automated work; a wrong sentence, flag or order here
    is what the user reads on the request page."""
    c = w.contract()
    r = w.request(contract=c)
    w.event("intake_request", r.id, "intake.created", "Request filed", ago=100, actor=w.requester)
    w.event("intake_request", r.id, "intake.triaged", "triage", ago=90)
    w.event("intake_request", r.id, "flow.started", "Started workflow “NDA”", ago=80, actor=w.staff)
    w.event("contract", c.id, "contract.lifecycle_changed", "Stage changed to review", ago=70,
            actor=w.staff, to_stage="review")
    w.event("contract", c.id, "playbook.run_completed", "Playbook review completed", ago=60,
            deviation_count=1)
    w.event("contract", c.id, "contract.issue_fixes_drafted", "Drafted fixes for 1 issue", ago=50)
    w.event("contract", c.id, "ai.skill_started", "AI skill started: playbook_review", ago=40,
            skill_name="playbook_review")
    w.db.add(IntakeHandoff(org_id=w.org_id, request_id=r.id, to_holder="human",
                           to_user_id=w.staff.id, reason="please look", actor_type="user",
                           created_by_user_id=w.requester.id, created_at=utcnow() - timedelta(seconds=30)))
    w.db.add(WorkflowRun(org_id=w.org_id, request_id=r.id, flow_name="NDA", status="waiting",
                         contract_id=c.id, context={"comments": [
                             {"kind": "comment", "text": "hello", "actor_name": "Sam Staff",
                              "at": (utcnow() - timedelta(seconds=10)).isoformat()}]}))
    w.db.flush()

    out = read(w, w.staff, r)
    got = {i["sentence"]: i for i in out["items"]}

    assert sentences(out) == [
        "Commented: “hello”",
        "Handed to Sam Staff: please look",
        "AI started: Playbook review",
        "Drafted fixes for 1 issue",
        "AI review complete · 1 issue found",
        "Stage changed to Review",
        "Started workflow “NDA”",
        "AI triage complete",
        "Request filed",
    ]
    assert out["truncated"] is False
    assert got["AI started: Playbook review"]["system"] is True
    assert got["AI review complete · 1 issue found"]["system"] is False
    assert got["AI review complete · 1 issue found"]["source"] == "contract"
    assert got["Started workflow “NDA”"]["source"] == "request"
    assert got["AI triage complete"]["who"] == "AI"
    assert got["Started workflow “NDA”"]["who"] == "Sam Staff"
    assert got["Request filed"]["who"] == "Rita Requester"
    assert got["Handed to Sam Staff: please look"]["who"] == "Rita Requester"
    assert got["Stage changed to Review"]["tone"] == "stage"
    assert set(out["items"][0]) == {"id", "at", "sentence", "who", "source", "tone", "system"}


def test_no_sentence_ever_shows_a_code_an_id_or_json(w):
    """Every table row, an unknown type and a title that equals its own code must read as
    prose; a leaked 'contract.something_new' or UUID is the bug this feature exists to remove."""
    r = w.request()
    uid = str(uuid.uuid4())
    for t in EVENTS_KEYS:
        w.event("intake_request", r.id, t, t)  # title == code: worst case for "use the title"
    w.event("intake_request", r.id, "intake.stage_advanced", "intake.stage_advanced")
    w.event("intake_request", r.id, "ai.skill_started", "x", skill_name=uid)

    out = read(w, w.staff, r)

    for i in out["items"]:
        s = i["sentence"]
        assert s
        assert not re.search(r"\b[a-z0-9_]+(\.[a-z0-9_]+)+\b", s), s
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", s) and "{" not in s, s

    r2 = w.request()
    w.event("intake_request", r2.id, "contract.something_new", f"{uid} {{raw}}")
    [item] = read(w, w.staff, r2)["items"]
    assert (item["sentence"], item["system"]) == ("Request updated", True)


def test_unknown_contract_event_reads_contract_updated_and_is_system(w):
    """A new event type nobody mapped must degrade to a hidden, plain sentence, not a code."""
    c = w.contract()
    r = w.request(contract=c)
    w.event("contract", c.id, "contract.something_new", "contract.something_new")

    [item] = [i for i in read(w, w.staff, r)["items"] if i["source"] == "contract"]

    assert (item["sentence"], item["system"], item["tone"]) == ("Contract updated", True, "plain")


def test_rule_based_sentences(w):
    """approval/review/obligation/risk/renewal rows are built from whitelisted details only."""
    c = w.contract()
    r = w.request(contract=c)
    w.event("contract", c.id, "approval.decided", "Approval rejected (step 2)", ago=5)
    w.event("contract", c.id, "playbook.run_completed", "x", ago=4, status="failed")
    w.event("contract", c.id, "playbook.run_completed", "x", ago=3, deviation_count=0)
    w.event("contract", c.id, "contract.obligations_extracted", "x", ago=2, obligation_count=9)
    w.event("contract", c.id, "contract.risk_recomputed", "x", ago=1, band="evil<script>")
    w.event("contract", c.id, "renewal.decided", "x", ago=0, decision="auto_renew")

    out = read(w, w.staff, r)

    assert sentences(out) == [
        "Renewal decision: auto renew", "Risk recalculated", "AI found 9 obligations",
        "AI review complete · no issues found", "AI review did not finish", "Rejected (step 2)",
    ]
    assert out["items"][-1]["tone"] == "stage"


EVENTS_KEYS = sorted(activity.EVENTS)


def test_every_event_type_written_in_the_code_has_a_sentence():
    """Fails when someone adds a timeline event without a row in EVENTS: it would otherwise
    show as the generic fallback forever, unnoticed."""
    prefixes = ("intake.", "flow.", "contract.", "playbook.", "assistant.", "approval.", "ai.",
                "signature.", "renewal.", "workflow.")
    root = Path(activity.__file__).resolve().parents[1]
    found: set[str] = {"approval.delegate", "approval.escalate"}  # written via f"approval.{kind}"
    pattern = re.compile(r'event_type\s*=\s*"([^"]+)"|audit_action[^=\n]*=\s*"([^"]+)"')
    for path in root.rglob("*.py"):
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            found.add(m.group(1) or m.group(2))
    missing = sorted(t for t in found if t.startswith(prefixes) and t not in activity.EVENTS)
    assert missing == []
    assert "contract.text_manually_edited" in found  # the audit_action default is scanned too


# --- access -------------------------------------------------------------------------------


def test_walled_viewer_gets_request_events_but_no_contract_events_and_no_denied_row(w):
    """A user walled off a contract must not read its history through the request; the check
    must be silent, since access.denied rows on every page open would flood the audit chain."""
    c = w.contract()
    r = w.request(contract=c)
    w.event("intake_request", r.id, "intake.created", "Request filed")
    w.event("contract", c.id, "contract.version_uploaded", "x")
    w.wall(c, w.staff)
    before = w.db.scalar(select(func.count()).select_from(AuditLog))

    out = read(w, w.staff, r)

    assert sentences(out) == ["Request filed"]
    assert w.db.scalar(select(func.count()).select_from(AuditLog)) == before


def test_viewer_without_contract_read_sees_no_contract_events(w):
    """contract:read gates contract history exactly as it gates the contract page."""
    c = w.contract()
    r = w.request(contract=c)
    w.event("intake_request", r.id, "intake.created", "Request filed")
    w.event("contract", c.id, "contract.version_uploaded", "x")
    w.perms[w.staff.id] = {"intake:create", "intake:read"}

    assert sentences(read(w, w.staff, r)) == ["Request filed"]


def test_requester_sees_own_events_and_handoffs_but_not_run_comments(w):
    """Requesters cannot read the run today, so its comments must not reach them; their own
    request events and hand-offs still do."""
    r = w.request()
    w.event("intake_request", r.id, "intake.created", "Request filed")
    w.db.add(IntakeHandoff(org_id=w.org_id, request_id=r.id, to_holder="queue", actor_type="system"))
    w.db.add(WorkflowRun(org_id=w.org_id, request_id=r.id, flow_name="NDA", context={"comments": [
        {"kind": "comment", "text": "internal", "actor_name": "Sam", "at": utcnow().isoformat()}]}))
    w.db.flush()

    out = read(w, w.requester, r)

    assert sorted(sentences(out)) == ["Request filed", "Returned to the queue"]
    assert [i["who"] for i in out["items"] if i["sentence"] == "Returned to the queue"] == ["Aegis"]


def test_capped_at_500_newest_and_truncated(w):
    """A long history must not make the page unbounded; the newest 500 stay and the response
    says older ones were dropped."""
    r = w.request()
    for n in range(503):
        w.db.add(ResourceTimelineEvent(
            org_id=w.org_id, resource_type="intake_request", resource_id=r.id,
            event_type="intake.created", title=f"E{n}", created_at=utcnow() - timedelta(seconds=600 - n)))
    w.db.flush()

    out = read(w, w.staff, r)

    assert len(out["items"]) == 500 and out["truncated"] is True
    assert out["items"][0]["sentence"] == "E502" and out["items"][-1]["sentence"] == "E3"


def test_reading_writes_no_audit_and_no_timeline_rows(w):
    """The page opens this on every visit; a write would flood the audit chain and the
    very timeline it displays."""
    c = w.contract()
    r = w.request(contract=c)
    w.event("contract", c.id, "contract.version_uploaded", "x")
    w.db.flush()
    def counts():
        return (
            w.db.scalar(select(func.count()).select_from(AuditLog)),
            w.db.scalar(select(func.count()).select_from(ResourceTimelineEvent)),
        )

    before = counts()

    read(w, w.staff, r)

    assert counts() == before


# --- the route ----------------------------------------------------------------------------


@pytest.fixture
def api(w, db, client):
    from app.main import app

    state = {"user": w.staff}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    def get(user: User, rid: str):
        state["user"] = user
        return client.get(f"/api/v1/intake/requests/{rid}/activity")

    return get


def test_route_returns_the_frozen_shape(w, api):
    """The frontend codes against exactly {items:[{id,at,sentence,who,source,tone,system}],truncated}."""
    r = w.request()
    w.event("intake_request", r.id, "intake.created", "Request filed", actor=w.requester)

    res = api(w.staff, r.id)

    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"items", "truncated"}
    assert set(body["items"][0]) == {"id", "at", "sentence", "who", "source", "tone", "system"}
    assert body["items"][0]["id"].startswith("t:")


def test_route_is_404_for_another_members_request_and_another_org(w, api, db):
    """The request row gate is the access rule: a non-staff member must not read someone
    else's request, and nobody reads another organisation's."""
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


def test_route_is_403_without_intake_create(w, api):
    """The route is gated by the same permission as /handoffs."""
    nobody = w.user("Nobody", set())
    r = w.request()

    assert api(nobody, r.id).status_code == 403
