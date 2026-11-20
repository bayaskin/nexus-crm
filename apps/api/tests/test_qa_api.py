import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "qa-foundation-test-signing-key-2026-abcdef")

from app.api.deps import get_current_user  # noqa: E402
from app.api.routes.qa import get_qa_orchestrator  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.identity import Permission, Role, User  # noqa: E402
from app.models.qa import QARun  # noqa: E402
from app.schemas.qa import CreateQARunRequest  # noqa: E402
from app.services.qa_orchestrator import QARunOrchestrator  # noqa: E402


def principal(permission_names: tuple[str, ...], *, superuser: bool = False) -> User:
    permissions = []
    for permission_name in permission_names:
        resource, action = permission_name.split(":", 1)
        permissions.append(Permission(resource=resource, action=action))
    role = Role(name="QA test role", permissions=permissions)
    user = User(
        email="qa-api-actor@example.com", full_name="QA API Actor", password_hash="unused",
        is_active=True, is_superuser=superuser, roles=[role],
    )
    user.id = uuid4()
    return user


@pytest.fixture
def client(tmp_path: Path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'qa-api.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    QARun.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_qa_orchestrator] = lambda: QARunOrchestrator(factory)
    try:
        with TestClient(app) as test_client:
            yield test_client, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def set_permissions(test_client: TestClient, *permissions: str) -> None:
    test_client.app.dependency_overrides[get_current_user] = lambda: principal(permissions)


def test_qa_api_requires_authentication_and_correct_permissions(client):
    test_client, _factory = client
    assert test_client.get("/v1/qa/runs").status_code == 401

    set_permissions(test_client, "qa:read")
    assert test_client.get("/v1/qa/runs").status_code == 200
    assert test_client.post("/v1/qa/runs", json={}).status_code == 403

    set_permissions(test_client, "qa:execute")
    assert test_client.get("/v1/qa/runs").status_code == 403
    created = test_client.post("/v1/qa/runs", json={"config": {"timeout_seconds": 10}})
    assert created.status_code == 202, created.text
    assert created.json()["status"] == "pending"
    assert created.json()["trigger_type"] == "manual"

    test_client.app.dependency_overrides[get_current_user] = lambda: principal((), superuser=True)
    assert test_client.get("/v1/qa/runs").status_code == 200
    assert test_client.post("/v1/qa/runs", json={}).status_code == 202


def test_qa_api_create_get_list_filter_and_validate_input_and_id(client):
    test_client, _factory = client
    set_permissions(test_client, "qa:execute", "qa:read")
    created = test_client.post("/v1/qa/runs", json={})
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]

    detail = test_client.get(f"/v1/qa/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    assert detail.json()["result"]["mode"] == "foundation_mock"
    assert detail.json()["result"]["test_execution"]["status"] == "not_run"
    assert detail.json()["result"]["assessment"] == "not_assessed"

    listed = test_client.get("/v1/qa/runs?status=completed&skip=0&limit=10")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == run_id
    assert test_client.get("/v1/qa/runs?status=invalid").status_code == 422
    assert test_client.get("/v1/qa/runs/not-a-uuid").status_code == 422
    assert test_client.get(f"/v1/qa/runs/{uuid4()}").status_code == 404
    assert test_client.post("/v1/qa/runs", json={"config": {"timeout_seconds": 0}}).status_code == 422
    assert test_client.post("/v1/qa/runs", json={"source_repository": "not-implemented"}).status_code == 422


def test_qa_api_cancellation_requires_execute_and_returns_conflict_for_terminal_runs(client):
    test_client, factory = client
    set_permissions(test_client, "qa:read")
    orchestrator = QARunOrchestrator(factory)
    db = factory()
    try:
        run = orchestrator.create(db, CreateQARunRequest(), principal(("qa:execute",)))
    finally:
        db.close()

    assert test_client.post(f"/v1/qa/runs/{run.id}/cancel").status_code == 403
    set_permissions(test_client, "qa:read", "qa:execute")
    cancelled = test_client.post(f"/v1/qa/runs/{run.id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    again = test_client.post(f"/v1/qa/runs/{run.id}/cancel")
    assert again.status_code == 409
