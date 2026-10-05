"""A fresh install's organisation gets the access the migrations only gave old ones.

The failure this guards: on a new VM the migrations ran on an empty database,
then first-admin setup created the organisation — which got no root org unit,
no role grant for the admin (``User.roles`` is view-only, so assigning it
stored nothing) and no screen access for any role. Legal Intake, Contracts and
every other guarded page were hidden from everyone.
"""

import uuid

import pytest
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User, UserRoleGrant
from app.auth.service import bootstrap_roles
from app.core.database import SessionLocal
from app.core.screen_access import resolve_screen_access
from app.menu_security.bootstrap import ensure_org_access
from app.organizations.models import Organization


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()  # nothing here is ever committed
        s.close()


def _fresh_org(db):
    tag = uuid.uuid4().hex[:8]
    org = Organization(name=f"fresh-install-{tag}", slug=f"fresh-install-{tag}", setup_complete=True)
    db.add(org)
    db.flush()
    roles = bootstrap_roles(db, org.id)
    db.flush()  # role ids are assigned on flush
    admin = User(org_id=org.id, email=f"admin-{uuid.uuid4().hex[:8]}@example.com", full_name="Fresh Admin",
                 hashed_password="x", status="active", active_role_id=roles["admin"].id)
    db.add(admin)
    db.flush()
    return org, admin


def test_a_fresh_orgs_admin_can_open_legal_intake_after_bootstrap(db):
    org, admin = _fresh_org(db)
    assert resolve_screen_access(db, user=admin, screen_code="intake").reason == "no_grant"

    added = ensure_org_access(db, org_id=org.id, actor_user_id=admin.id)

    assert added["role_grants_added"] == 1 and added["screen_access_added"] > 0
    for screen in ("intake", "contracts", "approvals", "templates", "screen_access"):
        access = resolve_screen_access(db, user=admin, screen_code=screen)
        assert access.reason == "granted", screen
    assert resolve_screen_access(db, user=admin, screen_code="intake").level == "DELETE"


def test_running_it_again_changes_nothing(db):
    org, admin = _fresh_org(db)
    ensure_org_access(db, org_id=org.id)
    again = ensure_org_access(db, org_id=org.id)
    assert again["role_grants_added"] == 0 and again["screen_access_added"] == 0
    grants = db.scalars(select(UserRoleGrant).where(UserRoleGrant.user_id == admin.id)).all()
    assert len(grants) == 1
