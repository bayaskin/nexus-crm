import os
import sys
from getpass import getpass

from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password, utcnow_naive
from app.models.identity import User
from app.models.sessions import UserSession

MINIMUM_PASSWORD_LENGTH = 12


def validate_qa_database_target(database_url: str) -> None:
    """Refuse to run the reset command unless connected to the QA database."""

    try:
        url = make_url(database_url)
    except Exception:
        raise ValueError("Refusing QA administrator reset: invalid database target") from None

    if (
        url.drivername != "mysql+pymysql"
        or url.host != "qa-mysql"
        or url.port != 3306
        or url.database != "nexus_qa_test"
        or url.username != "nexus_qa_runner"
        or not url.password
    ):
        raise ValueError("Refusing QA administrator reset: database target is not the isolated QA database")


def reset_qa_admin_password(db: Session, email: str, new_password: str) -> None:
    """Reset the password and revoke active sessions for an existing QA superuser."""

    if len(new_password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError("Administrator password must contain at least 12 characters")

    normalized_email = email.strip().lower()
    if not normalized_email:
        raise ValueError("QA_ADMIN_EMAIL is required")

    try:
        user = db.scalar(select(User).where(User.email == normalized_email).with_for_update())
        if user is None:
            raise LookupError("QA administrator was not found")
        if not user.is_superuser:
            raise LookupError("QA administrator account is not a superuser")

        now = utcnow_naive()
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        user.password_expires_at = None
        db.execute(
            update(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .values(revoked_at=now)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("This command does not accept command-line arguments")

    try:
        validate_qa_database_target(settings.database_url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    email = os.environ.get("QA_ADMIN_EMAIL", "").strip().lower()
    if not email:
        raise SystemExit("QA_ADMIN_EMAIL is required")

    password = getpass("Corrected QA administrator password (minimum 12 characters): ")
    confirmation = getpass("Confirm corrected password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise SystemExit("Administrator password must contain at least 12 characters")

    db = SessionLocal()
    try:
        reset_qa_admin_password(db, email, password)
    except LookupError as exc:
        raise SystemExit(str(exc)) from None
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    except Exception:
        raise SystemExit("QA administrator reset failed; no password was reported") from None
    finally:
        db.close()

    print(f"QA administrator password updated for {email}.")


if __name__ == "__main__":
    main()
