"""Move the existing QA superuser to the corrected QA email address."""

import argparse
import os
import re

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.identity import User

_EMAIL_ADAPTER = TypeAdapter(EmailStr)
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_qa_database_target(database_url: str) -> None:
    """Allow this maintenance operation only on the isolated QA database."""

    try:
        url = make_url(database_url)
    except Exception:
        raise ValueError("Refusing QA email migration: invalid database target") from None

    if (
        url.drivername != "mysql+pymysql"
        or url.host != "qa-mysql"
        or url.port != 3306
        or url.database != "nexus_qa_test"
        or url.username != "nexus_qa_runner"
        or not url.password
    ):
        raise ValueError("Refusing QA email migration: database target is not the isolated QA database")


def _validated_email(value: str) -> str:
    if not isinstance(value, str) or not _EMAIL_PATTERN.fullmatch(value):
        raise ValueError("Both email values must be valid email addresses")
    try:
        validated = str(_EMAIL_ADAPTER.validate_python(value))
    except (ValidationError, ValueError):
        raise ValueError("Both email values must be valid email addresses") from None
    if value != validated:
        raise ValueError("Email values must use their canonical validated form")
    return validated


def _validated_existing_email(value: str) -> str:
    """Validate the exact legacy address shape without applying new-address policy."""

    if not isinstance(value, str) or len(value) > 320 or not _EMAIL_PATTERN.fullmatch(value):
        raise ValueError("The old email must be the exact existing account email")
    return value


def migrate_qa_admin_email(db: Session, old_email: str, new_email: str) -> bool:
    """Change only the QA superuser email; return False for an idempotent rerun."""

    old_email = _validated_existing_email(old_email)
    new_email = _validated_email(new_email)

    try:
        old_user = db.scalar(select(User).where(User.email == old_email).with_for_update())
        if old_user is None:
            already_migrated = db.scalar(select(User).where(User.email == new_email).with_for_update())
            if already_migrated is not None:
                if already_migrated.email == new_email and already_migrated.is_superuser:
                    db.commit()
                    return False
                raise LookupError("The new QA administrator email is already in use")
            raise LookupError("QA administrator was not found")
        if old_user.email != old_email:
            raise LookupError("QA administrator was not found")
        if not old_user.is_superuser:
            raise LookupError("QA administrator account is not a superuser")

        if old_email == new_email:
            db.commit()
            return False

        conflicting_user = db.scalar(select(User).where(User.email == new_email).with_for_update())
        if conflicting_user is not None:
            raise LookupError("The new QA administrator email is already in use")

        old_user.email = new_email
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the isolated QA administrator email")
    parser.add_argument("--old-email", required=True)
    parser.add_argument("--new-email", required=True)
    args = parser.parse_args()

    if settings.app_env != "test":
        raise SystemExit("Refusing QA email migration outside the test environment")
    try:
        validate_qa_database_target(settings.database_url)
        old_email = _validated_existing_email(args.old_email)
        new_email = _validated_email(args.new_email)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    if os.environ.get("QA_ADMIN_EMAIL", "") != new_email:
        raise SystemExit("New email must match QA_ADMIN_EMAIL")

    db = SessionLocal()
    try:
        migrated = migrate_qa_admin_email(db, old_email, new_email)
    except LookupError as exc:
        raise SystemExit(str(exc)) from None
    except Exception:
        raise SystemExit("QA administrator email migration failed") from None
    finally:
        db.close()

    if migrated:
        print(f"QA administrator email updated from {old_email} to {new_email}.")
    else:
        print(f"QA administrator email is already {new_email}; no change was needed.")


if __name__ == "__main__":
    main()
