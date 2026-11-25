import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "qa-email-migration-test-signing-key-2026-abcdef")

from app import models  # noqa: E402,F401
from app.commands.migrate_qa_admin_email import (  # noqa: E402
    _validated_email,
    migrate_qa_admin_email,
    validate_qa_database_target,
)
from app.core.database import Base  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.identity import Permission, Role, User  # noqa: E402


@pytest.fixture
def qa_session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'qa-email-migration.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_corrected_qa_email_passes_application_emailstr_validation():
    assert _validated_email("qa-admin@example.com") == "qa-admin@example.com"


@pytest.mark.parametrize(
    "database_url",
    [
        "mysql+pymysql://nexus_qa_runner:secret@qa-mysql:3306/nexus_crm",
        "mysql+pymysql://nexus_qa_runner:secret@mysql:3306/nexus_qa_test",
        "mysql+pymysql://other:secret@qa-mysql:3306/nexus_qa_test",
        "sqlite+pysqlite:///:memory:",
    ],
)
def test_wrong_database_targets_are_rejected(database_url):
    with pytest.raises(ValueError, match="isolated QA database"):
        validate_qa_database_target(database_url)


def test_missing_qa_administrator_is_rejected(qa_session):
    with pytest.raises(LookupError, match="QA administrator was not found"):
        migrate_qa_admin_email(qa_session, "missing@example.com", "qa-admin@example.com")

    assert list(qa_session.scalars(select(User))) == []


def test_conflicting_new_email_is_rejected_without_changing_either_user(qa_session):
    old_admin = User(
        email="qa-admin@example.test",
        full_name="QA Admin",
        password_hash=hash_password("Old-QA-Password-2046"),
        is_superuser=True,
    )
    existing_user = User(
        email="qa-admin@example.com",
        full_name="Existing User",
        password_hash=hash_password("Existing-Password-2046"),
        is_superuser=False,
    )
    qa_session.add_all([old_admin, existing_user])
    qa_session.commit()
    old_id = old_admin.id
    existing_id = existing_user.id

    with pytest.raises(LookupError, match="already in use"):
        migrate_qa_admin_email(qa_session, "qa-admin@example.test", "qa-admin@example.com")

    qa_session.expire_all()
    assert qa_session.get(User, old_id).email == "qa-admin@example.test"
    assert qa_session.get(User, existing_id).email == "qa-admin@example.com"


def test_non_superuser_cannot_be_migrated(qa_session):
    user = User(
        email="qa-admin@example.test",
        full_name="Not Admin",
        password_hash=hash_password("Existing-Password-2046"),
        is_superuser=False,
    )
    qa_session.add(user)
    qa_session.commit()
    user_id = user.id

    with pytest.raises(LookupError, match="not a superuser"):
        migrate_qa_admin_email(qa_session, "qa-admin@example.test", "qa-admin@example.com")

    qa_session.expire_all()
    assert qa_session.get(User, user_id).email == "qa-admin@example.test"


def test_successful_migration_preserves_identity_password_roles_and_permissions(qa_session):
    permission = Permission(resource="qa", action="read")
    role = Role(name="QA Admin", permissions=[permission])
    admin = User(
        email="qa-admin@example.test",
        full_name="QA Admin",
        password_hash=hash_password("Existing-Password-2046"),
        is_superuser=True,
        roles=[role],
    )
    qa_session.add(admin)
    qa_session.commit()
    original_id = admin.id
    original_hash = admin.password_hash
    original_role_id = role.id
    original_permission_id = permission.id

    assert migrate_qa_admin_email(qa_session, "qa-admin@example.test", "qa-admin@example.com") is True

    qa_session.expire_all()
    migrated = qa_session.get(User, original_id)
    assert migrated.email == "qa-admin@example.com"
    assert migrated.id == original_id
    assert migrated.password_hash == original_hash
    assert [assigned_role.id for assigned_role in migrated.roles] == [original_role_id]
    assert [granted.id for assigned_role in migrated.roles for granted in assigned_role.permissions] == [original_permission_id]

    assert migrate_qa_admin_email(qa_session, "qa-admin@example.test", "qa-admin@example.com") is False
    assert qa_session.scalar(select(User).where(User.email == "qa-admin@example.com")).id == original_id
