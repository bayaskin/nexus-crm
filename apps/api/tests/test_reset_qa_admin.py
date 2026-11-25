import os
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "qa-reset-admin-test-signing-key-2026-abcdef")

from app import models  # noqa: E402,F401
from app.core.database import Base  # noqa: E402
from app.core.security import hash_password, utcnow_naive, verify_password  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.sessions import UserSession  # noqa: E402
from app.commands.reset_qa_admin import (  # noqa: E402
    reset_qa_admin_password,
    validate_qa_database_target,
)


@pytest.fixture
def qa_session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'qa-reset-admin.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    "database_url",
    [
        "mysql+pymysql://nexus_qa_runner:secret@qa-mysql:3306/nexus_crm",
        "mysql+pymysql://nexus_qa_runner:secret@mysql:3306/nexus_qa_test",
        "mysql+pymysql://other:secret@qa-mysql:3306/nexus_qa_test",
        "sqlite+pysqlite:///:memory:",
    ],
)
def test_database_target_validation_refuses_non_qa_targets(database_url):
    with pytest.raises(ValueError, match="isolated QA database"):
        validate_qa_database_target(database_url)


def test_database_target_validation_accepts_only_the_qa_connection():
    validate_qa_database_target(
        "mysql+pymysql://nexus_qa_runner:0123456789abcdef@qa-mysql:3306/nexus_qa_test"
    )


def test_missing_qa_administrator_is_rejected_without_changes(qa_session):
    with pytest.raises(LookupError, match="QA administrator was not found"):
        reset_qa_admin_password(qa_session, "missing@example.test", "New-QA-Password-2046")

    assert list(qa_session.scalars(select(User))) == []


def test_non_superuser_target_is_rejected(qa_session):
    qa_session.add(User(
        email="not-admin@example.test",
        full_name="Not Admin",
        password_hash=hash_password("Old-QA-Password-2046"),
        is_superuser=False,
    ))
    qa_session.commit()

    with pytest.raises(LookupError, match="not a superuser"):
        reset_qa_admin_password(qa_session, "not-admin@example.test", "New-QA-Password-2046")

    user = qa_session.scalar(select(User).where(User.email == "not-admin@example.test"))
    assert verify_password("Old-QA-Password-2046", user.password_hash)


def test_successful_reset_updates_only_qa_superuser_and_revokes_active_sessions(qa_session, monkeypatch):
    now = utcnow_naive()
    admin = User(
        email="qa-admin@example.com",
        full_name="QA Admin",
        password_hash=hash_password("Old-QA-Password-2046"),
        is_superuser=True,
        must_change_password=True,
        password_expires_at=now - timedelta(days=1),
    )
    other = User(
        email="other-admin@example.test",
        full_name="Other Admin",
        password_hash=hash_password("Other-Old-Password-2046"),
        is_superuser=True,
    )
    qa_session.add_all([admin, other])
    qa_session.flush()

    active_session = UserSession(
        user_id=admin.id,
        refresh_token_hash="a" * 64,
        expires_at=now + timedelta(days=1),
    )
    expired_session = UserSession(
        user_id=admin.id,
        refresh_token_hash="b" * 64,
        expires_at=now - timedelta(days=1),
    )
    other_session = UserSession(
        user_id=other.id,
        refresh_token_hash="c" * 64,
        expires_at=now + timedelta(days=1),
    )
    qa_session.add_all([active_session, expired_session, other_session])
    qa_session.commit()
    monkeypatch.setattr("app.commands.reset_qa_admin.utcnow_naive", lambda: now)

    reset_qa_admin_password(qa_session, " QA-ADMIN@EXAMPLE.COM ", "Corrected-QA-Password-2046")

    qa_session.expire_all()
    updated_admin = qa_session.scalar(select(User).where(User.email == "qa-admin@example.com"))
    assert verify_password("Corrected-QA-Password-2046", updated_admin.password_hash)
    assert not verify_password("Old-QA-Password-2046", updated_admin.password_hash)
    assert updated_admin.must_change_password is False
    assert updated_admin.password_expires_at is None
    assert qa_session.get(UserSession, active_session.id).revoked_at == now
    assert qa_session.get(UserSession, expired_session.id).revoked_at is None
    assert qa_session.get(UserSession, other_session.id).revoked_at is None
    unchanged_other = qa_session.get(User, other.id)
    assert verify_password("Other-Old-Password-2046", unchanged_other.password_hash)
