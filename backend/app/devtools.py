import argparse

from fastapi import HTTPException
from sqlalchemy import select

from app.approvals.service import ensure_default_approver_groups
from app.auth.models import Role, User
from app.auth.schemas import SetupAdminRequest
from app.auth.service import create_first_admin
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.enums import UserStatus
from app.core.security import hash_password
from app.organizations.models import Organization
import app.models  # noqa: F401


# Two demo users per role, for local RBAC / multi-user testing. Seeded the same
# way as the admin (same org, password hashing, active status) but for the
# non-admin roles. Dev/local only — gated by environment in ``seed``.
DEV_USERS: list[tuple[str, list[tuple[str, str]]]] = [
    ("admin", [("admin1@example.com", "Avery Admin"), ("admin2@example.com", "Blair Admin")]),
    ("member", [("user1@example.com", "Casey Member"), ("user2@example.com", "Dana Member")]),
    ("legal_reviewer", [("legal1@example.com", "Erin Legal"), ("legal2@example.com", "Finley Legal")]),
    ("approver", [("approver1@example.com", "Gale Approver"), ("approver2@example.com", "Harper Approver")]),
]


def ensure_dev_users(db, org) -> list[tuple[str, str]]:
    """Idempotently create the demo users per role. Existing users (matched by
    email) are left untouched, so this is safe to re-run. Returns the list of
    (email, role) actually created this run."""
    roles = {r.name: r for r in db.scalars(select(Role).where(Role.org_id == org.id))}
    admin = db.scalar(select(User).where(User.email == settings.dev_seed_admin_email))
    created: list[tuple[str, str]] = []
    for role_name, people in DEV_USERS:
        role = roles.get(role_name)
        if role is None:
            continue
        for email, full_name in people:
            if db.scalar(select(User).where(User.email == email)):
                continue
            user = User(
                email=email,
                full_name=full_name,
                hashed_password=hash_password(settings.dev_seed_admin_password),
                status=UserStatus.ACTIVE,
                org_id=org.id,
                active_role_id=role.id,
                created_by_user_id=admin.id if admin else None,
                updated_by_user_id=admin.id if admin else None,
            )
            user.roles = [role]
            db.add(user)
            created.append((email, role_name))
    if created:
        db.commit()
    return created


def seed() -> None:
    db = SessionLocal()
    try:
        try:
            create_first_admin(
                db,
                SetupAdminRequest(
                    setup_token=settings.setup_token,
                    organization_name="Local Legal CLM",
                    organization_slug="local-legal-clm",
                    allowed_domains=["example.com"],
                    email=settings.dev_seed_admin_email,
                    full_name="Local Admin",
                    password=settings.dev_seed_admin_password,
                ),
            )
            print(f"Seeded admin user {settings.dev_seed_admin_email}")
        except HTTPException as exc:
            # Seeding is a one-time bootstrap. Re-running it once the org/admin
            # exists is a no-op, not a failure — report it cleanly and continue
            # so we still top up the approver groups below (also idempotent).
            if exc.status_code != 409:
                raise
            print(
                f"Admin/org already set up — log in as {settings.dev_seed_admin_email}."
            )

        # Ensure the default approver groups exist (idempotent), so the routing
        # form has real options whether the org is new or pre-existing.
        org = db.scalar(select(Organization))
        if org is not None:
            created = ensure_default_approver_groups(db, org_id=org.id)
            db.commit()
            if created:
                print("Seeded approver groups: " + ", ".join(g.name for g in created))
            else:
                print("Approver groups already present.")

            # Demo users per role — local/dev only, never where real accounts live.
            if settings.environment in {"local", "development", "test"}:
                dev_users = ensure_dev_users(db, org)
                if dev_users:
                    print("Seeded dev users: " + ", ".join(e for e, _ in dev_users))
                else:
                    print("Dev users already present.")
    finally:
        db.close()


def reset_database() -> None:
    if settings.environment not in {"local", "development", "test"} or not settings.allow_dev_reset:
        raise SystemExit("Refusing reset. Set ENVIRONMENT=local and ALLOW_DEV_RESET=true.")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("Local database reset complete")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local backend development helpers")
    parser.add_argument("command", choices=["seed", "reset-db"])
    args = parser.parse_args()
    if args.command == "seed":
        seed()
    if args.command == "reset-db":
        reset_database()


if __name__ == "__main__":
    main()
