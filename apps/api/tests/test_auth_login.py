import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "qa-auth-login-test-signing-key-2026-abcdef")

from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app import models  # noqa: E402,F401
from app.core.database import Base  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.sessions import UserSession  # noqa: E402
from app.services.auth import login  # noqa: E402


def _login_request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/auth/login",
            "raw_path": b"/v1/auth/login",
            "query_string": b"",
            "headers": [(b"user-agent", b"auth-login-unit-test")],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        }
    )


def test_login_updates_user_before_inserting_session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'auth-login.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda _conn, _cursor, statement, *_: statements.append(statement.lower()))

    try:
        with factory() as db:
            user = User(
                email="qa-admin@example.com",
                full_name="QA Admin",
                password_hash="test-hash",
                is_active=True,
            )
            db.add(user)
            db.commit()
            user_id = user.id

            monkeypatch.setattr("app.services.auth.verify_password", lambda _password, _encoded: True)
            result = login(db, _login_request(), "qa-admin@example.com", "test-password")

            update_index = next(
                index for index, statement in enumerate(statements)
                if statement.lstrip().startswith("update users")
            )
            session_insert_index = next(
                index for index, statement in enumerate(statements)
                if statement.lstrip().startswith("insert into user_sessions")
            )
            assert update_index < session_insert_index
            assert db.scalar(select(UserSession).where(UserSession.user_id == user_id)) is not None
            assert result["access_token"]
            assert result["refresh_token"]
    finally:
        engine.dispose()
