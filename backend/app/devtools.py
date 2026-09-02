import argparse

from fastapi import HTTPException
from sqlalchemy import select

import app.models  # noqa: F401
from app.approvals.service import ensure_default_approver_groups
from app.auth.models import Role, User
from app.auth.schemas import SetupAdminRequest
from app.auth.service import create_first_admin
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.enums import UserStatus
from app.core.security import hash_password
from app.organizations.models import Organization

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

                # Legal intake demo data (request types, KB, sample requests).
                from app.auth.models import Role as _Role
                from app.auth.models import User as _User
                from app.intake.seed import seed_intake

                # Any user holding the admin role (the dev_seed_admin_email is a
                # placeholder in this env; the real admin comes from dev users).
                admin_user = db.scalar(
                    select(_User)
                    .join(_User.roles)
                    .where(_User.org_id == org.id, _Role.name == "admin")
                    .limit(1)
                )
                if admin_user is not None:
                    if seed_intake(db, org, admin_user):
                        db.commit()
                        print("Seeded legal intake demo data.")
                    else:
                        print("Intake demo data already present.")
    finally:
        db.close()


def backfill_contract_brain() -> None:
    """Queue extraction + knowledge-graph ingestion for any contract missing
    clauses or a graph slice, so Contract Brain covers the whole portfolio and
    not just the contracts that happened to flow through a job path. Idempotent
    — re-running skips contracts already covered."""
    from app.auth.models import Role, User
    from app.contract_brain.models import ClauseExtraction, KnowledgeNode
    from app.contract_files.models import ContractTextSnapshot, ContractVersion
    from app.contract_files.service import _queue_initial_contract_jobs
    from app.contracts.models import Contract
    from app.jobs.service import create_job, dispatch_job

    db = SessionLocal()
    try:
        contracts = db.scalars(
            select(Contract).where(Contract.current_authoritative_version_id.isnot(None))
        ).all()
        admins: dict[str, User | None] = {}
        queued = 0
        for c in contracts:
            version = db.get(ContractVersion, c.current_authoritative_version_id)
            if version is None or not version.text_snapshot_id:
                continue
            snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id)
            has_clauses = db.scalar(
                select(ClauseExtraction.id).where(
                    ClauseExtraction.contract_id == c.id,
                    ClauseExtraction.is_stale.is_(False),
                ).limit(1)
            )
            has_graph = db.scalar(
                select(KnowledgeNode.id).where(
                    KnowledgeNode.contract_id == c.id,
                    KnowledgeNode.is_stale.is_(False),
                ).limit(1)
            )
            if has_clauses and has_graph:
                continue
            if c.org_id not in admins:
                admins[c.org_id] = db.scalar(
                    select(User).join(User.roles).where(
                        User.org_id == c.org_id, Role.name == "admin"
                    ).limit(1)
                ) or db.scalar(select(User).where(User.org_id == c.org_id).limit(1))
            admin = admins[c.org_id]
            jobs = []
            if not has_clauses:
                # Re-extract (metadata + clauses + embeddings); a successful
                # clause extraction chains graph ingestion on its own.
                jobs += _queue_initial_contract_jobs(
                    db, user=admin, contract=c, version=version, snapshot=snapshot
                )
            else:
                # Clauses exist, only the graph is missing → ingest directly.
                jobs.append(
                    create_job(
                        db,
                        org_id=c.org_id,
                        job_type="contract_brain_ingestion",
                        resource_type="contract",
                        resource_id=c.id,
                        created_by_user_id=admin.id if admin else None,
                        idempotency_key=f"contract_brain_ingestion:{version.id}:{snapshot.id}:backfill",
                        metadata={
                            "contract_version_id": version.id,
                            "text_snapshot_id": snapshot.id,
                        },
                    )
                )
            db.flush()
            for j in jobs:
                dispatch_job(db, job=j)
            db.commit()
            queued += len(jobs)
            print(f"  {(c.title or c.id)[:50]}: queued {len(jobs)} job(s)")
        print(f"Backfill done — {queued} job(s) queued across {len(contracts)} contracts.")
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
    parser.add_argument("command", choices=["seed", "reset-db", "backfill-brain"])
    args = parser.parse_args()
    if args.command == "seed":
        seed()
    if args.command == "reset-db":
        reset_database()
    if args.command == "backfill-brain":
        backfill_contract_brain()


if __name__ == "__main__":
    main()
