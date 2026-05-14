import argparse

from app.auth.schemas import SetupAdminRequest
from app.auth.service import create_first_admin
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
import app.models  # noqa: F401


def seed() -> None:
    db = SessionLocal()
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
