"""Rolling the 27 Oct standard out to organisations that already exist (spec 014, FR-8, FR-9, FR-53 to FR-55).

Failures this guards:
* "Use the standard rules" open to anyone who may only view or edit templates (it publishes a
  playbook, so it needs ``playbook:publish``), or reaching another organisation's playbook;
* the update overwriting rules an organisation edited, or wording it saved itself, or being applied
  twice (a second marker, a second playbook version);
* a new playbook version whose rules are in another order (the review numbers rules by creation time)
  or that leaves the playbook still pointing at the old version;
* the status page not saying a template or playbook predates the standard, or saying so after the
  organisation adopted it;
* a deliberate AI sample making "results vary" appear; (see test_template_status) and the Customer
  Agreement Playbook, which this feature leaves alone, no longer hashing to its reviewed baseline.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.main
import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.core.database import engine, new_uuid
from app.core.deps import get_current_user, get_db
from app.core.enums import PlaybookStatus, UserStatus
from app.core.models import AuditLog
from app.drafting_templates import checks, standard
from app.drafting_templates.models import DraftingTemplateType, DraftingTemplateVersion
from app.drafting_templates.service import TEMPLATES, current_wording, get_type, reviewing_playbook
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization
from app.playbooks import library
from app.playbooks.models import Playbook, PlaybookRule, PlaybookVersion
from app.playbooks.service import rules_in_review_order

API = "/api/v1/drafting-templates"
PUBLISHER = ["playbook:read", "playbook:publish"]
MSA_PLAYBOOK = "Master Services Agreement Playbook"
_OLD = [{"clause_type": "payment_terms", "preferred_position": "Pay in arrears.", "rationale": "Cash flow."},
        {"clause_type": "insurance", "preferred_position": "Keep cover.", "rationale": "Stand behind indemnities."}]
_EDITED = [{"clause_type": "payment_terms", "preferred_position": "Pay in 10 days.", "rationale": "Ours."}]


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


@pytest.fixture
def api(db):
    from fastapi.testclient import TestClient

    from app.core.rate_limit import limiter
    from app.main import app

    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    previous = limiter.enabled
    limiter.enabled = False
    try:
        with TestClient(app) as client:
            yield client
    finally:
        limiter.enabled = previous
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def requested(monkeypatch):
    """Template checks are recorded, not run (they are paid AI reviews, tested elsewhere)."""
    calls: list[dict] = []

    def fake(db, *, org_id, key, trigger, actor_user_id, unsaved_body=None, trigger_detail=None):
        calls.append({"org_id": org_id, "key": key, "trigger": trigger, "detail": trigger_detail})

    monkeypatch.setattr(checks, "request_check", fake)
    return calls


def _as(user: User) -> None:
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user


def _user(db: Session, permissions: list[str]) -> User:
    org = Organization(id=new_uuid(), name="std", slug=f"std-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
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
    user = User(org_id=org.id, email=f"u-{uuid.uuid4().hex[:8]}@example.com", full_name="Publisher",
                hashed_password="hash", status=UserStatus.ACTIVE)
    db.add(user)
    db.flush()
    db.add(UserRoleGrant(user_id=user.id, role_id=role.id, org_id=org.id, org_unit_id=unit.id))
    db.flush()
    return user


def _playbook(db: Session, user: User, rules=_OLD, name: str = MSA_PLAYBOOK, *, old: bool = False):
    """A published playbook (name-matched to the MSA template) holding ``rules``. ``old`` dates its
    version before the standard."""
    pb = Playbook(org_id=user.org_id, name=name, status=PlaybookStatus.PUBLISHED,
                  created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(pb)
    db.flush()
    v = PlaybookVersion(org_id=user.org_id, playbook_id=pb.id, version_number=1, status=PlaybookStatus.PUBLISHED,
                        created_by_user_id=user.id, updated_by_user_id=user.id)
    if old:
        v.created_at = standard.EFFECTIVE - timedelta(days=30)
    db.add(v)
    db.flush()
    pb.current_version_id = v.id
    for i, r in enumerate(rules):
        db.add(PlaybookRule(org_id=user.org_id, playbook_version_id=v.id, rule_type="standard_position",
                            risk_level="medium", created_at=v.created_at + timedelta(seconds=i),
                            created_by_user_id=user.id, updated_by_user_id=user.id, **r))
    db.flush()
    return pb, v


def _audit(db: Session, org_id: str, action: str) -> list[AuditLog]:
    return list(db.scalars(select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == action)
                           .order_by(AuditLog.created_at, AuditLog.id)))


def _versions(db: Session, pb: Playbook) -> list[PlaybookVersion]:
    return list(db.scalars(select(PlaybookVersion).where(PlaybookVersion.playbook_id == pb.id)
                           .order_by(PlaybookVersion.version_number)))


def _old_baseline(monkeypatch, rules=_OLD) -> None:
    """Pretend the reviewed baseline of the MSA playbook is ``rules`` (the real one is a hash of the
    dev database's rules, which a test cannot build rows from)."""
    old = standard.fingerprint([{**r, "rule_type": "standard_position", "risk_level": "medium"} for r in rules])
    monkeypatch.setitem(standard.BASELINE_FINGERPRINTS, "msa", frozenset({old}))


# --- The standard itself ------------------------------------------------------------------------


def test_the_customer_agreement_playbook_still_hashes_to_the_reviewed_baseline():
    """AC-6 / FR-7: this feature leaves the Customer Agreement Playbook as it was reviewed. If its
    library rules change, the update would be rolling out something nobody reviewed."""
    (spec,) = [s for s in library.PLAYBOOK_LIBRARY if s["name"] == "Customer Agreement Playbook"]
    assert standard.fingerprint(spec["rules"]) == standard.CUSTOMER_BASELINE


def test_every_shipped_template_has_rules_a_note_and_a_change_list():
    """A shipped type missing from any table would get no update and no explanation; a note over 300
    characters would be cut mid-word by the version note column."""
    assert set(standard.STANDARD_RULES) == set(standard.NOTES) == set(standard.CHANGES) == set(TEMPLATES)
    assert all(len(n) <= 300 and n.startswith("Standard updated:") for n in standard.NOTES.values())
    for key, rules in standard.STANDARD_RULES.items():
        assert len(standard.CHANGES[key]) >= len(rules) and key in standard.BASELINE_FINGERPRINTS
        assert standard.STANDARD_FINGERPRINT[key] not in standard.BASELINE_FINGERPRINTS[key], (
            f"{key}: the standard equals the old baseline, so the update would change nothing")


def test_a_fingerprint_ignores_order_and_spacing_but_not_wording():
    a = {"clause_type": "x", "preferred_position": "Pay  in\n45 days"}
    b = {"clause_type": "y", "preferred_position": "z"}
    assert standard.fingerprint([a, b]) == standard.fingerprint([b, {**a, "preferred_position": "Pay in 45 days"}])
    assert standard.fingerprint([a, b]) != standard.fingerprint([a, {**b, "preferred_position": "w"}])


# --- Adopt: the endpoint ------------------------------------------------------------------------


@pytest.mark.parametrize("permissions", [["playbook:read", "playbook:update", "playbook:run"], ["contract:read"]],
                         ids=["editor without publish", "no playbook permission"])
def test_adopting_the_standard_needs_the_permission_to_publish_and_writes_nothing(api, db, requested, permissions):
    """It publishes a playbook version, which is what playbook:publish guards everywhere else."""
    user = _user(db, permissions)
    pb, _ = _playbook(db, user)
    _as(user)

    r = api.post(f"{API}/msa/playbook/standard", json={})

    assert r.status_code == 403 and r.json()["detail"] == "Missing permission: playbook:publish"
    assert len(_versions(db, pb)) == 1 and requested == []


def test_adopting_publishes_the_standard_rules_audits_and_starts_a_check(api, db, requested):
    """The happy path: matching rules become the playbook's newest published version, in the
    standard's order, with both audit rows, and only then is the template checked."""
    user = _user(db, PUBLISHER)
    pb, first = _playbook(db, user, old=True)
    _as(user)

    r = api.post(f"{API}/msa/playbook/standard", json={"note": "  Adopting the 27 Oct standard "})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True
    versions = _versions(db, pb)
    assert [v.version_number for v in versions] == [1, 2] and versions[1].status == PlaybookStatus.PUBLISHED
    db.refresh(pb)
    assert pb.current_version_id == versions[1].id
    rules = rules_in_review_order(db, org_id=user.org_id, playbook_version_id=versions[1].id)
    assert [r.clause_type for r in rules] == [r["clause_type"] for r in standard.STANDARD_RULES["msa"]]
    assert versions[1].source_metadata["source_version_id"] == first.id
    assert versions[1].source_metadata["standard"] == "014"
    [adopted] = _audit(db, user.org_id, "playbook.standard_adopted")
    assert adopted.actor_user_id == user.id and adopted.before["version_number"] == 1
    assert adopted.after["version_number"] == 2 and adopted.after["note"] == "Adopting the 27 Oct standard"
    assert adopted.after["template_key"] == "msa" and adopted.after["revision"] == "014"
    assert {c["clause_type"] for c in adopted.after["changes"]} >= {"payment_terms"}
    assert len(_audit(db, user.org_id, "playbook.published")) == 1
    assert [c["trigger"] for c in requested] == ["playbook_published"]
    assert body["template"]["standard"]["playbook_behind"] is False


def test_adopting_twice_changes_nothing_the_second_time(api, db, requested):
    """A double click must not stack identical versions or start a second paid check."""
    user = _user(db, PUBLISHER)
    pb, _ = _playbook(db, user)
    _as(user)
    api.post(f"{API}/msa/playbook/standard", json={})
    requested.clear()

    r = api.post(f"{API}/msa/playbook/standard", json={})

    assert r.status_code == 200 and r.json()["changed"] is False and r.json()["check"] is None
    assert len(_versions(db, pb)) == 2 and requested == []


def test_adopting_refuses_unknown_templates_missing_playbooks_and_long_notes(api, db):
    """404 for a key that is nobody's, 409 with the plan's words when no playbook reviews it or the
    type has no standard, 422 for a note over 300 characters."""
    user = _user(db, PUBLISHER)
    _as(user)
    assert api.post(f"{API}/nope/playbook/standard", json={}).status_code == 404

    r = api.post(f"{API}/msa/playbook/standard", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == "No playbook reviews this template, so there are no rules to update."

    db.add(DraftingTemplateType(org_id=user.org_id, key="custom", name="Custom", label="Custom",
                                form_key="new_agreement", agreement_kind="Custom kind", shipped=False,
                                status="active"))
    db.flush()
    r = api.post(f"{API}/custom/playbook/standard", json={})
    assert r.status_code == 409 and r.json()["detail"] == "This template has no standard rules."

    _playbook(db, user)
    assert api.post(f"{API}/msa/playbook/standard", json={"note": "x" * 301}).status_code == 422


def test_another_organisations_playbook_is_never_adopted_into(api, db, requested):
    """Org scoping: a template pinned to a playbook of another organisation reviews nothing here,
    so the adopt answers 409 and the other organisation's playbook is untouched."""
    theirs = _user(db, PUBLISHER)
    their_pb, _ = _playbook(db, theirs)
    mine = _user(db, PUBLISHER)
    db.add(DraftingTemplateType(org_id=mine.org_id, key="msa", name="MSA", label="MSA", form_key="new_agreement",
                                agreement_kind="Services (MSA)", shipped=True, status="active",
                                playbook_id=their_pb.id))
    db.flush()
    _as(mine)

    r = api.post(f"{API}/msa/playbook/standard", json={})

    assert r.status_code == 409 and len(_versions(db, their_pb)) == 1 and requested == []


# --- Status -------------------------------------------------------------------------------------


def test_the_summary_says_when_wording_and_playbook_are_older_than_the_standard(api, db):
    """FR-53/54: an organisation that saved its own wording, or whose playbook was last published
    before the standard, is told so; the shape is exactly what the page's TemplateStandard reads."""
    user = _user(db, PUBLISHER)
    _playbook(db, user, _EDITED, old=True)
    own = DraftingTemplateVersion(org_id=user.org_id, key="msa", version=1, body="{company} and {counterparty}.",
                                  created_by_user_id=user.id, updated_by_user_id=user.id)
    own.created_at = standard.EFFECTIVE - timedelta(days=3)
    db.add(own)
    db.flush()
    _as(user)

    got = api.get(f"{API}/msa").json()["standard"]

    assert got == {"revision": "014", "updated_on": "2026-10-27", "wording_behind": True, "playbook_behind": True}
    listed = {t["key"]: t["standard"] for t in api.get(API).json()}
    assert set(listed) == set(TEMPLATES) and listed["msa"] == got


def test_shipped_wording_is_never_behind_and_a_type_of_our_own_has_no_standard(api, db):
    user = _user(db, PUBLISHER)
    db.add(DraftingTemplateType(org_id=user.org_id, key="custom", name="Custom", label="Custom",
                                form_key="new_agreement", agreement_kind="Custom kind", shipped=False,
                                status="active"))
    db.add(DraftingTemplateVersion(org_id=user.org_id, key="custom", version=1, body="{company} {counterparty}",
                                   created_by_user_id=user.id, updated_by_user_id=user.id))
    db.flush()
    _as(user)

    assert api.get(f"{API}/msa").json()["standard"]["wording_behind"] is False
    assert api.get(f"{API}/custom").json()["standard"] is None


# --- The migration body -------------------------------------------------------------------------


def test_the_update_publishes_matching_rules_marks_the_wording_and_is_safe_to_replay(db, monkeypatch):
    """FR-53: rules equal to the reviewed baseline get the standard; a shipped-wording template gets
    a body-less marker with the note and a system audit row; running it twice adds nothing."""
    _old_baseline(monkeypatch)
    user = _user(db, PUBLISHER)
    pb, _ = _playbook(db, user, old=True)

    standard.apply_standard_update(db)
    standard.apply_standard_update(db)

    assert [v.version_number for v in _versions(db, pb)] == [1, 2]
    [marker] = [m for m in db.scalars(select(DraftingTemplateVersion).where(
        DraftingTemplateVersion.org_id == user.org_id, DraftingTemplateVersion.key == "msa"))]
    assert marker.body is None and marker.note == standard.NOTES["msa"] and marker.created_by_user_id is None
    [updated] = _audit(db, user.org_id, "playbook.standard_updated")
    assert updated.actor_user_id is None and updated.metadata_json["actor"] == "system"
    [wording] = [a for a in _audit(db, user.org_id, "drafting_template.standard_updated")
                 if a.after["key"] == "msa"]
    assert wording.after["default"] is True and wording.resource_id == marker.id
    assert len(_audit(db, user.org_id, "playbook.published")) == 1


def test_the_update_leaves_edited_rules_and_own_wording_alone_and_says_so(db, monkeypatch):
    """FR-54: rules the organisation edited are skipped and audited, not overwritten; wording it saved
    itself gets no marker; nothing of this organisation's changes."""
    _old_baseline(monkeypatch)
    user = _user(db, PUBLISHER)
    pb, _ = _playbook(db, user, _EDITED, old=True)
    db.add(DraftingTemplateVersion(org_id=user.org_id, key="msa", version=1, body="{company} {counterparty}",
                                   created_by_user_id=user.id, updated_by_user_id=user.id,
                                   created_at=standard.EFFECTIVE - timedelta(days=3)))
    db.flush()

    standard.apply_standard_update(db)

    assert len(_versions(db, pb)) == 1
    [skipped] = _audit(db, user.org_id, "playbook.standard_skipped")
    assert skipped.after == {"template_key": "msa", "reason": "edited by the organisation"}
    assert db.scalar(select(DraftingTemplateVersion).where(
        DraftingTemplateVersion.org_id == user.org_id, DraftingTemplateVersion.version == 2)) is None
    ttype = get_type(db, user.org_id, "msa")
    wording = current_wording(db, user.org_id, ttype)
    assert standard.status_for(db, user.org_id, ttype, wording) == {
        "revision": "014", "updated_on": standard.UPDATED_ON, "wording_behind": True, "playbook_behind": True}


def test_the_update_does_not_touch_the_customer_agreement_playbook(db, monkeypatch):
    """AC-6: the Customer Agreement Playbook has no template, so no template key points at it."""
    _old_baseline(monkeypatch)
    user = _user(db, PUBLISHER)
    pb, _ = _playbook(db, user, name="Customer Agreement Playbook", old=True)

    standard.apply_standard_update(db)

    assert len(_versions(db, pb)) == 1 and _audit(db, user.org_id, "playbook.standard_skipped") == []


def test_reverting_archives_the_standard_versions_and_points_back_at_the_old_one(db, monkeypatch):
    """The migration's downgrade: nothing deleted, the old version is the playbook's current one
    again, and the review of it is audited."""
    _old_baseline(monkeypatch)
    user = _user(db, PUBLISHER)
    pb, first = _playbook(db, user, old=True)
    standard.apply_standard_update(db)
    db.refresh(pb)
    assert pb.current_version_id != first.id

    standard.revert_standard_update(db)

    db.refresh(pb)
    versions = _versions(db, pb)
    assert pb.current_version_id == first.id and versions[1].status == PlaybookStatus.ARCHIVED
    assert len(versions) == 2 and len(_audit(db, user.org_id, "playbook.standard_reverted")) == 1
    assert reviewing_playbook(db, user.org_id, get_type(db, user.org_id, "msa")).version.id == first.id


def test_the_update_only_reads_organisation_ids_first(db, monkeypatch):
    """CI and a fresh test database have no organisation: the body must return before touching any
    model, so a later model change can never break replaying the migration."""
    class _NoOrgs:
        def scalars(self):
            return self

        def all(self):
            return []

    monkeypatch.setattr(db, "execute", lambda *a, **k: _NoOrgs())
    monkeypatch.setattr(db, "scalars", lambda *a, **k: pytest.fail("touched a model with no organisation"))

    standard.apply_standard_update(db)
