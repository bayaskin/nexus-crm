"""MySQL-only reliability checks. Requires a disposable nexus_qa_test database."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import ArgumentError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("MYSQL_TEST_DATABASE_URL")
ALEMBIC_DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("MYSQL_TEST_DATABASE_URL is not set; MySQL reliability checks were not requested", allow_module_level=True)


def _parse_mysql_url(value: str) -> tuple[str, int, str, str, str]:
    try:
        url = make_url(value)
        backend = url.get_backend_name()
        target = (
            url.host or "",
            url.port if url.port is not None else 3306,
            url.database or "",
            url.username or "",
            url.password or "",
        )
    except (ArgumentError, ValueError):
        # Do not include the URL in the exception: it contains credentials.
        raise RuntimeError("Refusing MySQL tests: a database URL is invalid") from None
    if backend != "mysql":
        raise RuntimeError("Refusing MySQL tests: database URLs must use MySQL")
    return target


def _validate_mysql_test_target(mysql_test_url: str | None, alembic_url: str | None, kind: str | None) -> None:
    if not mysql_test_url or not alembic_url:
        raise RuntimeError("Refusing MySQL tests: both database URLs must be configured")
    if kind != "disposable":
        raise RuntimeError("Refusing MySQL tests: MYSQL_TEST_DATABASE_KIND must equal disposable")
    test_target = _parse_mysql_url(mysql_test_url)
    migration_target = _parse_mysql_url(alembic_url)
    if test_target[0] != "qa-mysql" or test_target[2] != "nexus_qa_test":
        raise RuntimeError("Refusing MySQL tests: MYSQL_TEST_DATABASE_URL must target disposable nexus_qa_test on qa-mysql")
    if not test_target[3] or not test_target[4]:
        raise RuntimeError("Refusing MySQL tests: database credentials must be configured")
    if test_target != migration_target:
        raise RuntimeError("Refusing MySQL tests: MYSQL_TEST_DATABASE_URL and DATABASE_URL must match in host, port, database, and credentials")


_validate_mysql_test_target(DATABASE_URL, ALEMBIC_DATABASE_URL, os.getenv("MYSQL_TEST_DATABASE_KIND"))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET_KEY", "mysql-test-signing-secret-long-enough-2026-abc")

from app.core.config import Settings
from app.models.github import GitHubPRRevision, GitHubPullRequestState, GitHubWebhookDelivery
from app.models.identity import User
from app.models.qa import QARun
from app.services.github_client import GitHubPullRequestClient
from app.services.github_ingestion import GitHubPRIngestionService
from app.services.qa_orchestrator import QARunOrchestrator


REPO_ID = 987654328
PR_NUMBER = 942
BASE_SHA = "a" * 40
SECRET = "mysql-disposable-webhook-secret"
TOKEN = "synthetic-mysql-test-token"
SAFE_TEST_URL = "mysql+pymysql://test-user:test-password@qa-mysql:3306/nexus_qa_test"


@pytest.mark.parametrize("test_url,alembic_url", [
    (SAFE_TEST_URL, "mysql+pymysql://test-user:test-password@other-host:3306/nexus_qa_test"),
    (SAFE_TEST_URL, "mysql+pymysql://test-user:test-password@qa-mysql:3307/nexus_qa_test"),
    (SAFE_TEST_URL, "mysql+pymysql://test-user:test-password@qa-mysql:3306/other_database"),
    (SAFE_TEST_URL, "mysql+pymysql://other-user:test-password@qa-mysql:3306/nexus_qa_test"),
    (SAFE_TEST_URL, "mysql+pymysql://test-user:other-password@qa-mysql:3306/nexus_qa_test"),
])
def test_mysql_test_target_validation_rejects_mismatched_urls(test_url, alembic_url):
    with pytest.raises(RuntimeError) as error:
        _validate_mysql_test_target(test_url, alembic_url, "disposable")
    assert test_url not in str(error.value)
    assert alembic_url not in str(error.value)
    assert "test-password" not in str(error.value)
    assert "other-password" not in str(error.value)


def _body(head: str, delivery: str) -> bytes:
    return _body_snapshot(head, BASE_SHA, delivery)


def _body_snapshot(head: str, base_sha: str, delivery: str) -> bytes:
    return json.dumps({"action": "synchronize", "repository": {"id": REPO_ID, "name": "widget",
        "full_name": "acme/widget", "owner": {"login": "acme"}}, "pull_request": {
        "number": PR_NUMBER, "updated_at": "2026-11-20T16:00:00Z",
        "head": {"ref": "feature/test", "sha": head}, "base": {"ref": "main", "sha": base_sha}}}).encode()


def _signature(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _handler(head: str, base_sha: str = BASE_SHA, barrier: threading.Barrier | None = None):
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json=[{"filename": "src/app.py", "status": "modified",
                "additions": 1, "deletions": 0, "changes": 1}])
        if request.url.path.endswith("/pulls/942"):
            if barrier is not None and request.url.params.get("_unused") is None:
                # Each worker meets at its initial metadata read; only the first two
                # calls use this transport in the concurrent duplicate test.
                barrier.wait(timeout=15)
            return httpx.Response(200, json={"number": PR_NUMBER, "title": "Concurrency", "changed_files": 1,
                "html_url": f"https://github.com/acme/widget/pull/{PR_NUMBER}", "user": {"login": "tester"},
                "state": "open", "draft": False, "head": {"ref": "feature/test", "sha": head},
                "base": {"ref": "main", "sha": base_sha, "repo": {"id": REPO_ID,
                    "full_name": "acme/widget", "name": "widget", "default_branch": "main", "owner": {"login": "acme"}}}})
        return httpx.Response(404)
    return respond


def _connect():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _clear(factory):
    with factory.begin() as db:
        run_ids = select(QARun.id).where(QARun.github_repository_id == REPO_ID)
        db.execute(text("DELETE FROM github_webhook_deliveries WHERE repository_id=:rid"), {"rid": REPO_ID})
        db.execute(text("DELETE FROM qa_runs WHERE github_repository_id=:rid"), {"rid": REPO_ID})
        db.execute(text("DELETE FROM github_pr_revisions WHERE repository_id=:rid"), {"rid": REPO_ID})
        db.execute(text("DELETE FROM github_pull_request_states WHERE repository_id=:rid"), {"rid": REPO_ID})


def _service(factory, handler):
    configuration = Settings(_env_file=None, app_env="test", database_url=DATABASE_URL,
        jwt_secret_key="mysql-test-signing-secret-long-enough-2026-abc", github_token=SecretStr(TOKEN),
        github_webhook_secret=SecretStr(SECRET), github_allowed_repositories="acme/widget")
    client = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(handler), deadline_seconds=20)
    return GitHubPRIngestionService(factory, QARunOrchestrator(factory), configuration=configuration, client=client)


def _ingest(service, head: str, delivery: str):
    body = _body(head, delivery)
    return service.ingest(body, signature=_signature(body), event_name="pull_request", delivery_id=delivery)


def _ingest_snapshot(service, head: str, base_sha: str, delivery: str):
    body = _body_snapshot(head, base_sha, delivery)
    return service.ingest(body, signature=_signature(body), event_name="pull_request", delivery_id=delivery)


def test_mysql_migration_round_trip_preserves_users_and_refuses_data_loss():
    engine, factory = _connect()
    try:
        _clear(factory)
        # Make the migration test repeatable if an earlier assertion stopped it
        # after the disposable schema had been upgraded.
        with factory.begin() as db:
            db.execute(text("DELETE FROM qa_runs WHERE trigger_type='manual' AND JSON_UNQUOTE(JSON_EXTRACT(result, '$.summary'))='kept through migration'"))
        user_id = uuid4()
        with factory.begin() as db:
            db.add(User(id=user_id, email=f"migration-{user_id}@example.invalid", full_name="Migration Test",
                        password_hash="not-a-real-password-hash", is_active=True, is_superuser=False,
                        must_change_password=False))
        config = Config("alembic.ini")
        command.downgrade(config, "c39a8b17d2f4")
        with factory() as db:
            assert db.get(User, user_id) is not None
            assert db.execute(text("SELECT COUNT(*) FROM qa_runs")).scalar_one() == 0
            assert db.execute(text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='github_pr_revisions'")).scalar_one() == 0

        manual_id = uuid4().hex
        manual_result = json.dumps({"assessment": "manual-preserved", "summary": "kept through migration"})
        manual_state = json.dumps({"status": "completed", "progress": 100})
        with factory.begin() as db:
            db.execute(text("""
                INSERT INTO qa_runs
                    (id, requested_by_user_id, trigger_type, status, progress, timeout_seconds,
                     input_config, state_data, result, error, cancel_requested, created_at,
                     updated_at, started_at, completed_at, duration_ms)
                VALUES
                    (:id, NULL, 'manual', 'completed', 100, 300,
                     JSON_OBJECT('timeout_seconds', 300), :state_data, :result, NULL, 0,
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 42)
            """), {"id": manual_id, "state_data": manual_state, "result": manual_result})
        with factory() as db:
            before = dict(db.execute(text("SELECT created_at, updated_at, started_at, completed_at, cancel_requested, error FROM qa_runs WHERE id=:id"), {"id": manual_id}).mappings().one())
        command.upgrade(config, "head")
        with factory() as db:
            manual = db.execute(text("SELECT trigger_type, status, state_data, result, github_context, duration_ms, created_at, updated_at, started_at, completed_at, cancel_requested, error FROM qa_runs WHERE id=:id"), {"id": manual_id}).mappings().one()
            assert manual["trigger_type"] == "manual"
            assert manual["status"] == "completed"
            assert json.loads(manual["state_data"]) == json.loads(manual_state)
            assert json.loads(manual["result"]) == json.loads(manual_result)
            assert manual["github_context"] is None
            assert manual["duration_ms"] == 42
            assert {key: manual[key] for key in before} == before

        schema = inspect(engine)
        revision_columns = {column["name"]: column["type"] for column in schema.get_columns("github_pr_revisions")}
        delivery_columns = {column["name"]: column["type"] for column in schema.get_columns("github_webhook_deliveries")}
        state_columns = {column["name"]: column["type"] for column in schema.get_columns("github_pull_request_states")}
        qa_columns = {column["name"]: column["type"] for column in schema.get_columns("qa_runs")}
        assert revision_columns["id"].length == 36
        assert delivery_columns["delivery_id"].length == 128
        assert delivery_columns["qa_run_id"].length == 32  # MySQL UUIDs use CHAR(32), matching qa_runs.id.
        assert state_columns["repository_id"].__class__.__name__ == "BIGINT"
        assert state_columns["latest_base_sha"].length == 40
        assert qa_columns["github_revision_id"].length == 36
        assert schema.get_pk_constraint("github_pull_request_states")["constrained_columns"] == [
            "repository_id", "pull_request_number"
        ]
        assert any(fk["referred_table"] == "github_pr_revisions" for fk in schema.get_foreign_keys("qa_runs"))
        # A GitHub-originated row must prevent a destructive downgrade.
        state = QARunOrchestrator(factory)
        run = None
        with factory.begin() as db:
            from app.schemas.qa import CreateQARunRequest, QATriggerType
            from app.schemas.github import GitHubPRContext
            from app.models.github import GitHubPRRevision
            from datetime import datetime
            context = GitHubPRContext(repository_id=REPO_ID, repository_full_name="acme/widget", repository_owner="acme",
                repository_name="widget", pull_request_number=PR_NUMBER, title="test",
                url=f"https://github.com/acme/widget/pull/{PR_NUMBER}", author="tester", state="open", draft=False,
                source_branch="feature/test", target_branch="main", base_sha=BASE_SHA, head_sha="b"*40,
                event_base_sha=BASE_SHA,
                changed_files=[], reported_changed_files=0, event_type="opened", delivery_id="mig-run",
                event_updated_at=datetime.now())
            db.add(GitHubPRRevision(id=context.revision_id, repository_id=REPO_ID,
                pull_request_number=PR_NUMBER, base_sha=BASE_SHA, head_sha="b"*40,
                observed_sequence=1, created_at=datetime.now()))
            run = state.create(db, CreateQARunRequest(), None, github_context=context,
                               trigger_type=QATriggerType.GITHUB_PULL_REQUEST, commit=False)
        with pytest.raises(RuntimeError, match="Cannot downgrade GitHub PR ingestion"):
            command.downgrade(config, "c39a8b17d2f4")
        with factory.begin() as db:
            db.delete(db.get(QARun, run.id))
            db.delete(db.get(GitHubPRRevision, context.revision_id))
        command.downgrade(config, "c39a8b17d2f4")
        with factory() as db:
            manual = db.execute(text("SELECT trigger_type, status, state_data, result, duration_ms, created_at, updated_at, started_at, completed_at, cancel_requested, error FROM qa_runs WHERE id=:id"), {"id": manual_id}).mappings().one()
            assert manual["trigger_type"] == "manual"
            assert manual["status"] == "completed"
            assert json.loads(manual["state_data"]) == json.loads(manual_state)
            assert json.loads(manual["result"]) == json.loads(manual_result)
            assert manual["duration_ms"] == 42
            assert {key: manual[key] for key in before} == before
        command.upgrade(config, "head")
        with factory() as db:
            assert db.get(User, user_id) is not None
            manual = db.execute(text("SELECT trigger_type, status, state_data, result, github_context, duration_ms, created_at, updated_at, started_at, completed_at, cancel_requested, error FROM qa_runs WHERE id=:id"), {"id": manual_id}).mappings().one()
            assert manual["trigger_type"] == "manual"
            assert manual["status"] == "completed"
            assert json.loads(manual["state_data"]) == json.loads(manual_state)
            assert json.loads(manual["result"]) == json.loads(manual_result)
            assert manual["github_context"] is None
            assert manual["duration_ms"] == 42
            assert {key: manual[key] for key in before} == before
        with factory.begin() as db:
            db.execute(text("DELETE FROM qa_runs WHERE id=:id"), {"id": manual_id})
            db.delete(db.get(User, user_id))
    finally:
        engine.dispose()


def test_mysql_downgrade_refuses_stale_delivery_without_run_and_preserves_history():
    engine, factory = _connect()
    delivery_id = "mysql-stale-only"
    try:
        _clear(factory)
        config = Config("alembic.ini")
        with factory.begin() as db:
            from datetime import datetime
            db.add(GitHubWebhookDelivery(delivery_id=delivery_id, repository_id=REPO_ID,
                pull_request_number=PR_NUMBER, base_sha=BASE_SHA, head_sha="d"*40,
                event_type="synchronize", qa_run_id=None, disposition="ignored_stale",
                received_at=datetime.now()))
        with pytest.raises(RuntimeError, match="delivery.*revision.*history"):
            command.downgrade(config, "c39a8b17d2f4")
        with factory() as db:
            retained = db.get(GitHubWebhookDelivery, delivery_id)
            assert retained is not None
            assert retained.qa_run_id is None
            assert retained.disposition == "ignored_stale"
            assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "d620a8e4f19b"
    finally:
        _clear(factory)
        engine.dispose()


def test_mysql_deleting_run_preserves_delivery_and_redelivery_is_idempotent():
    engine, factory = _connect()
    delivery_id = "mysql-retained-delivery"
    try:
        _clear(factory)
        service = _service(factory, _handler("b" * 40))
        accepted = _ingest(service, "b" * 40, delivery_id)
        assert accepted.created is True
        assert accepted.run is not None

        with factory() as db:
            delivery = db.get(GitHubWebhookDelivery, delivery_id)
            assert delivery is not None
            assert delivery.qa_run_id == accepted.run.id

        with factory.begin() as db:
            db.delete(db.get(QARun, accepted.run.id))

        with factory() as db:
            delivery = db.get(GitHubWebhookDelivery, delivery_id)
            assert delivery is not None
            assert delivery.qa_run_id is None

        duplicate = _ingest(service, "b" * 40, delivery_id)
        assert duplicate.created is False
        assert duplicate.ignored is True
        assert duplicate.run is None
        with factory() as db:
            assert db.get(GitHubWebhookDelivery, delivery_id) is not None
            assert db.scalar(select(func.count(QARun.id)).where(
                QARun.github_repository_id == REPO_ID
            )) == 0
    finally:
        _clear(factory)
        engine.dispose()


@pytest.mark.parametrize("deliveries,expected_deliveries", [
    (["mysql-same", "mysql-same"], 1),
    (["mysql-c1", "mysql-c2"], 2),
])
def test_mysql_concurrent_same_revision_creates_one_run_and_revision(deliveries, expected_deliveries):
    engine, factory = _connect()
    try:
        _clear(factory)
        barrier = threading.Barrier(2)
        service = _service(factory, _handler("b" * 40, barrier=barrier))
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda delivery: _ingest(service, "b" * 40, delivery), deliveries))
        assert outcomes[0].run.id == outcomes[1].run.id
        with factory() as db:
            assert db.scalar(select(func.count(QARun.id)).where(QARun.github_repository_id == REPO_ID)) == 1
            assert db.scalar(select(func.count(GitHubPRRevision.id)).where(GitHubPRRevision.repository_id == REPO_ID)) == 1
            assert db.scalar(select(func.count(GitHubWebhookDelivery.delivery_id)).where(GitHubWebhookDelivery.repository_id == REPO_ID)) == expected_deliveries
    finally:
        _clear(factory)
        engine.dispose()


def test_mysql_a_to_b_to_a_keeps_three_immutable_occurrences():
    engine, factory = _connect()
    try:
        _clear(factory)
        first = _ingest(_service(factory, _handler("b" * 40)), "b" * 40, "mysql-aba-1")
        middle = _ingest(_service(factory, _handler("c" * 40)), "c" * 40, "mysql-aba-2")
        returned = _ingest(_service(factory, _handler("b" * 40)), "b" * 40, "mysql-aba-3")
        assert len({first.run.id, middle.run.id, returned.run.id}) == 3
        with factory() as db:
            assert db.get(GitHubPullRequestState, (REPO_ID, PR_NUMBER)).latest_head_sha == "b" * 40
            assert db.scalar(select(func.count(GitHubPRRevision.id)).where(GitHubPRRevision.repository_id == REPO_ID)) == 3
            original_a = db.get(QARun, first.run.id)
            revision_b = db.get(QARun, middle.run.id)
            new_a = db.get(QARun, returned.run.id)
            assert original_a.github_context["delivery_id"] == "mysql-aba-1"
            assert new_a.github_context["delivery_id"] == "mysql-aba-3"
            assert original_a.status == revision_b.status == "superseded"
            assert new_a.status == "pending"
    finally:
        _clear(factory)
        engine.dispose()


def test_mysql_concurrent_base_and_head_updates_keep_newest_snapshot_latest():
    engine, factory = _connect()
    try:
        _clear(factory)
        old_fetch_done = threading.Event()
        allow_old_to_persist = threading.Event()
        class DelayAfterSnapshot:
            @staticmethod
            def validate_repository_name(name):
                return GitHubPullRequestClient.validate_repository_name(name)

            def fetch_pull_request(self, repository, number):
                snapshot = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(
                    _handler("b" * 40, BASE_SHA))).fetch_pull_request(repository, number)
                old_fetch_done.set()
                assert allow_old_to_persist.wait(timeout=15)
                return snapshot

        old_service = GitHubPRIngestionService(factory, QARunOrchestrator(factory),
            configuration=Settings(_env_file=None, app_env="test", database_url=DATABASE_URL,
                jwt_secret_key="mysql-test-signing-secret-long-enough-2026-abc",
                github_token=SecretStr(TOKEN), github_webhook_secret=SecretStr(SECRET),
                github_allowed_repositories="acme/widget"), client=DelayAfterSnapshot())
        updated_base = "d" * 40
        base_service = _service(factory, _handler("b" * 40, updated_base))
        head_service = _service(factory, _handler("c" * 40, updated_base))
        with ThreadPoolExecutor(max_workers=1) as pool:
            old_request = pool.submit(_ingest_snapshot, old_service, "b" * 40, BASE_SHA, "mysql-race-old")
            assert old_fetch_done.wait(timeout=15)
            base_result = _ingest_snapshot(base_service, "b" * 40, updated_base, "mysql-race-base")
            new_result = _ingest_snapshot(head_service, "c" * 40, updated_base, "mysql-race-new")
            allow_old_to_persist.set()
            old_result = old_request.result(timeout=15)
        assert base_result.run is not None
        assert new_result.run is not None
        assert old_result.ignored is True
        with factory() as db:
            assert db.get(GitHubPullRequestState, (REPO_ID, PR_NUMBER)).latest_head_sha == "c" * 40
            assert db.get(GitHubPullRequestState, (REPO_ID, PR_NUMBER)).latest_base_sha == updated_base
            assert db.scalar(select(func.count(QARun.id)).where(QARun.github_repository_id == REPO_ID)) == 2
            assert db.get(GitHubWebhookDelivery, "mysql-race-old").disposition == "ignored_stale"
    finally:
        _clear(factory)
        engine.dispose()
