import hashlib
import hmac
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "qa-github-test-signing-secret-2026-abcdef")

from app.api.routes.github import get_github_ingestion_service
from app.api.routes.qa import get_qa_orchestrator
from app.core.config import Settings
from app.core.database import get_db
from app.main import app
from app.models.github import GitHubPRRevision, GitHubPullRequestState, GitHubWebhookDelivery
from app.models.identity import User
from app.models.qa import QARun
from app.schemas.qa import QARunStatus
from app.services.github_client import GitHubAPIError, GitHubPullRequestClient, GitHubRateLimited
from app.services.github_ingestion import GitHubPRIngestionService, GitHubWebhookError
from app.services.qa_orchestrator import QARunOrchestrator


SECRET = "generated-webhook-test-secret-only"
TOKEN = "ghp_synthetic_test_token_not_a_credential"
REPOSITORY_ID = 987654321
PR_NUMBER = 42
BASE_SHA = "a" * 40


def build_configuration() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret_key="qa-github-test-signing-secret-2026-abcdef",
        github_token=SecretStr(TOKEN),
        github_webhook_secret=SecretStr(SECRET),
        github_allowed_repositories="acme/widget",
    )


def event_payload(*, head_sha: str = "b" * 40, base_sha: str = BASE_SHA, action: str = "opened", updated_at: str = "2026-11-20T16:00:00Z", repo_name: str = "acme/widget") -> bytes:
    owner, name = repo_name.split("/", 1)
    return json.dumps(
        {
            "action": action,
            "repository": {
                "id": REPOSITORY_ID,
                "name": name,
                "full_name": repo_name,
                "owner": {"login": owner},
            },
            "pull_request": {
                "number": PR_NUMBER,
                "updated_at": updated_at,
                "head": {"ref": "feature/change", "sha": head_sha},
                "base": {"ref": "main", "sha": base_sha},
            },
        }
    ).encode()


def signature(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def api_handler(*, head_sha: str = "b" * 40, base_sha: str = BASE_SHA, file_pages: dict[int, list[dict]] | None = None):
    pages = file_pages or {1: [{"filename": "src/app.py", "status": "modified", "additions": 3, "deletions": 1, "changes": 4}]}
    file_count = sum(len(page) for page in pages.values())
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files"):
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(200, json=pages.get(page, []))
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(
                200,
                json={
                    "number": PR_NUMBER,
                    "title": "Harden request handling",
                    "html_url": "https://github.com/acme/widget/pull/42",
                    "user": {"login": "contributor"},
                    "state": "open",
                    "draft": False,
                    "changed_files": file_count,
                    "head": {"ref": "feature/change", "sha": head_sha},
                    "base": {
                        "ref": "main",
                        "sha": base_sha,
                        "repo": {
                            "id": REPOSITORY_ID,
                            "full_name": "acme/widget",
                            "name": "widget",
                            "default_branch": "main",
                            "owner": {"login": "acme"},
                        },
                    },
                    "merge_commit_sha": None,
                },
            )
        return httpx.Response(404, json={"message": "Not Found"})
    return handler


@pytest.fixture
def runtime(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'github-qa.sqlite'}")
    User.__table__.create(engine)
    GitHubPRRevision.__table__.create(engine)
    QARun.__table__.create(engine)
    GitHubPullRequestState.__table__.create(engine)
    GitHubWebhookDelivery.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    orchestrator = QARunOrchestrator(factory)
    configuration = build_configuration()

    def make_service(handler=None):
        client = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(handler or api_handler()))
        return GitHubPRIngestionService(factory, orchestrator, configuration=configuration, client=client)

    try:
        yield factory, orchestrator, make_service
    finally:
        engine.dispose()


def ingest(service, body: bytes, *, delivery: str = "delivery-1", event: str = "pull_request"):
    return service.ingest(
        body,
        signature=signature(body),
        event_name=event,
        delivery_id=delivery,
    )


@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize"])
def test_supported_pull_request_actions_capture_typed_immutable_context(runtime, action):
    factory, _, make_service = runtime
    body = event_payload(action=action)
    outcome = ingest(make_service(), body)

    assert outcome.created is True
    assert outcome.start_execution is True
    assert outcome.run.trigger_type == "github_pull_request"
    assert outcome.run.github_context["repository_id"] == REPOSITORY_ID
    assert outcome.run.github_context["pull_request_number"] == PR_NUMBER
    assert outcome.run.github_context["base_sha"] == BASE_SHA
    assert outcome.run.github_context["head_sha"] == "b" * 40
    assert outcome.run.github_context["event_type"] == action
    assert outcome.run.github_context["changed_files"][0]["path"] == "src/app.py"
    state = outcome.run.state_data
    assert state["github_context"]["head_sha"] == "b" * 40
    with factory() as db:
        persisted = db.get(QARun, outcome.run.id)
        assert persisted.github_context["delivery_id"] == "delivery-1"


def test_ingested_github_run_executes_change_impact_analysis(runtime):
    factory, orchestrator, make_service = runtime
    accepted = ingest(make_service(), event_payload(), delivery="impact-ingestion")
    orchestrator.execute(accepted.run.id)

    with factory() as db:
        persisted = db.get(QARun, accepted.run.id)
        analysis = persisted.state_data["change_impact_analysis"]
        assert persisted.status == QARunStatus.COMPLETED.value
        assert analysis["repository_id"] == REPOSITORY_ID
        assert analysis["base_sha"] == BASE_SHA
        assert analysis["head_sha"] == "b" * 40
        assert analysis["changed_files"][0]["path"] == "src/app.py"
        assert persisted.result["change_impact_analysis"] == analysis
        assert persisted.result["test_execution"]["status"] == "not_run"


def test_duplicate_delivery_and_duplicate_revision_are_idempotent(runtime):
    factory, _, make_service = runtime
    body = event_payload()
    first = ingest(make_service(), body, delivery="delivery-unique")
    repeated_delivery = ingest(make_service(), body, delivery="delivery-unique")
    repeated_revision = ingest(make_service(), event_payload(action="synchronize"), delivery="delivery-new")

    assert first.created is True
    assert repeated_delivery.created is False
    assert repeated_revision.created is False
    assert repeated_delivery.start_execution is True
    assert repeated_revision.start_execution is True
    assert repeated_delivery.run.id == first.run.id
    assert repeated_revision.run.id == first.run.id
    with factory() as db:
        assert db.scalar(select(func.count(GitHubWebhookDelivery.delivery_id))) == 2
        assert db.scalar(select(func.count(GitHubPRRevision.id))) == 1
    with pytest.raises(GitHubWebhookError) as reused:
        ingest(make_service(), event_payload(head_sha="c" * 40, action="opened"), delivery="delivery-unique")
    assert reused.value.status_code == 409


def test_new_revision_creates_new_run_and_supersedes_older_active_revision(runtime):
    factory, _, make_service = runtime
    first = ingest(make_service(api_handler(head_sha="b" * 40)), event_payload(head_sha="b" * 40, updated_at="2026-11-20T16:00:00Z"), delivery="d1")
    second = ingest(
        make_service(api_handler(head_sha="c" * 40)),
        event_payload(head_sha="c" * 40, updated_at="2026-11-20T16:05:00Z", action="synchronize"),
        delivery="d2",
    )

    assert second.created is True
    assert second.run.id != first.run.id
    assert second.run.github_context["head_sha"] == "c" * 40
    with factory() as db:
        old = db.get(QARun, first.run.id)
        new = db.get(QARun, second.run.id)
        assert old.status == QARunStatus.SUPERSEDED.value
        assert old.github_context["head_sha"] == "b" * 40
        assert old.error["code"] == "superseded"
        assert new.status == QARunStatus.PENDING.value
        latest = db.get(GitHubPullRequestState, (REPOSITORY_ID, PR_NUMBER))
        assert latest.latest_head_sha == "c" * 40


def test_out_of_order_older_revision_is_ignored_and_does_not_replace_latest(runtime):
    factory, _, _ = runtime
    newer_sha, older_sha = "c" * 40, "b" * 40
    configuration = build_configuration()
    orchestrator = QARunOrchestrator(runtime[0])

    def service_for(sha):
        client = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(api_handler(head_sha=sha)))
        return GitHubPRIngestionService(factory, orchestrator, configuration=configuration, client=client)

    newest = ingest(
        service_for(newer_sha),
        event_payload(head_sha=newer_sha, updated_at="2026-11-20T16:05:00Z"),
        delivery="newest",
    )
    # GitHub's current PR endpoint reports the newer head for the delayed event.
    older = ingest(
        service_for(newer_sha),
        event_payload(head_sha=older_sha, updated_at="2026-11-20T16:00:00Z", action="synchronize"),
        delivery="older",
    )

    assert older.ignored is True
    with factory() as db:
        assert db.get(QARun, newest.run.id).status == QARunStatus.PENDING.value
        assert db.get(GitHubPullRequestState, (REPOSITORY_ID, PR_NUMBER)).latest_head_sha == newer_sha


def test_a_to_b_to_a_creates_a_new_revision_occurrence(runtime):
    factory, _, make_service = runtime
    first = ingest(make_service(api_handler(head_sha="b" * 40)), event_payload(head_sha="b" * 40), delivery="aba-a1")
    middle = ingest(make_service(api_handler(head_sha="c" * 40)), event_payload(head_sha="c" * 40, action="synchronize"), delivery="aba-b")
    returned = ingest(make_service(api_handler(head_sha="b" * 40)), event_payload(head_sha="b" * 40, action="synchronize"), delivery="aba-a2")
    assert len({first.run.id, middle.run.id, returned.run.id}) == 3
    with factory() as db:
        assert db.get(GitHubPullRequestState, (REPOSITORY_ID, PR_NUMBER)).latest_head_sha == "b" * 40
        assert db.scalar(select(func.count(GitHubPRRevision.id))) == 3
        original_a = db.get(QARun, first.run.id)
        revision_b = db.get(QARun, middle.run.id)
        new_a = db.get(QARun, returned.run.id)
        assert original_a.github_context["head_sha"] == new_a.github_context["head_sha"] == "b" * 40
        assert original_a.github_context["delivery_id"] == "aba-a1"
        assert new_a.github_context["delivery_id"] == "aba-a2"
        assert original_a.status == QARunStatus.SUPERSEDED.value
        assert revision_b.status == QARunStatus.SUPERSEDED.value
        assert new_a.status == QARunStatus.PENDING.value


def test_same_head_with_updated_base_creates_fresh_snapshot_occurrence(runtime):
    factory, _, make_service = runtime
    old_files = {1: [{"filename": "before-base.py", "status": "modified", "additions": 1, "deletions": 0, "changes": 1}]}
    new_files = {1: [{"filename": "after-base.py", "status": "modified", "additions": 2, "deletions": 1, "changes": 3}]}
    first = ingest(make_service(api_handler(head_sha="b" * 40, base_sha=BASE_SHA, file_pages=old_files)),
        event_payload(head_sha="b" * 40, base_sha=BASE_SHA), delivery="base-old")
    changed_base = "d" * 40
    updated = ingest(make_service(api_handler(head_sha="b" * 40, base_sha=changed_base, file_pages=new_files)),
        event_payload(head_sha="b" * 40, base_sha=changed_base, action="synchronize"), delivery="base-new")
    duplicate = ingest(make_service(api_handler(head_sha="b" * 40, base_sha=changed_base, file_pages=new_files)),
        event_payload(head_sha="b" * 40, base_sha=changed_base, action="synchronize"), delivery="base-new")
    assert updated.run.id != first.run.id
    assert duplicate.run.id == updated.run.id
    assert updated.run.github_context["head_sha"] == first.run.github_context["head_sha"]
    assert updated.run.github_context["base_sha"] == changed_base
    assert updated.run.github_context["event_base_sha"] == changed_base
    assert updated.run.github_context["changed_files"][0]["path"] == "after-base.py"
    with factory() as db:
        latest = db.get(GitHubPullRequestState, (REPOSITORY_ID, PR_NUMBER))
        assert latest.latest_head_sha == "b" * 40
        assert latest.latest_base_sha == changed_base
        assert db.scalar(select(func.count(GitHubPRRevision.id))) == 2


def test_same_head_with_outdated_event_base_uses_current_api_snapshot_and_is_idempotent(runtime):
    factory, _, make_service = runtime
    current_base = "e" * 40
    calls = []
    handler = api_handler(head_sha="b" * 40, base_sha=current_base)
    def counted(request):
        calls.append(request.url.path)
        return handler(request)
    body = event_payload(head_sha="b" * 40, base_sha=BASE_SHA, action="synchronize")
    service = make_service(counted)
    outcome = ingest(service, body, delivery="old-base-event")
    before_duplicate = len(calls)
    duplicate = ingest(service, body, delivery="old-base-event")
    assert outcome.ignored is False
    assert outcome.run.github_context["base_sha"] == current_base
    assert outcome.run.github_context["event_base_sha"] == BASE_SHA
    assert duplicate.run.id == outcome.run.id
    assert len(calls) == before_duplicate
    with factory() as db:
        delivery = db.get(GitHubWebhookDelivery, "old-base-event")
        assert delivery.base_sha == BASE_SHA
        assert db.get(GitHubPullRequestState, (REPOSITORY_ID, PR_NUMBER)).latest_base_sha == current_base


def test_late_api_response_cannot_replace_newer_same_head_observation(runtime):
    factory, orchestrator, make_service = runtime
    first = ingest(make_service(api_handler(head_sha="b" * 40)), event_payload(head_sha="b" * 40), delivery="order-base")
    entered = threading.Event()
    release = threading.Event()

    class DelayedClient:
        @staticmethod
        def validate_repository_name(name):
            return GitHubPullRequestClient.validate_repository_name(name)

        def fetch_pull_request(self, repository, number):
            entered.set()
            assert release.wait(timeout=5)
            return GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(api_handler(head_sha="c" * 40))).fetch_pull_request(repository, number)

    delayed = GitHubPRIngestionService(factory, orchestrator, configuration=build_configuration(), client=DelayedClient())
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(lambda: ingest(delayed, event_payload(head_sha="c" * 40), delivery="order-old"))
        assert entered.wait(timeout=5)
        newer_current = ingest(make_service(api_handler(head_sha="b" * 40)), event_payload(head_sha="b" * 40, action="synchronize"), delivery="order-new")
        release.set()
        older_completion = old.result(timeout=5)
    assert newer_current.run.id == first.run.id
    assert older_completion.ignored is True
    with factory() as db:
        assert db.get(GitHubPullRequestState, (REPOSITORY_ID, PR_NUMBER)).latest_head_sha == "b" * 40


def test_stale_delivery_with_different_event_and_snapshot_base_is_idempotent(runtime):
    factory, orchestrator, make_service = runtime
    event_base = "a" * 40
    snapshot_base = "d" * 40
    latest_base = "e" * 40
    entered = threading.Event()
    release = threading.Event()

    class DelayedSnapshotClient:
        @staticmethod
        def validate_repository_name(name):
            return GitHubPullRequestClient.validate_repository_name(name)

        def fetch_pull_request(self, repository, number):
            entered.set()
            assert release.wait(timeout=5)
            return GitHubPullRequestClient(
                TOKEN,
                transport=httpx.MockTransport(api_handler(head_sha="c" * 40, base_sha=snapshot_base)),
            ).fetch_pull_request(repository, number)

    older_body = event_payload(head_sha="c" * 40, base_sha=event_base, action="synchronize")
    older_service = GitHubPRIngestionService(
        factory, orchestrator, configuration=build_configuration(), client=DelayedSnapshotClient()
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        older_request = pool.submit(lambda: ingest(older_service, older_body, delivery="stale-base-delivery"))
        assert entered.wait(timeout=5)
        newer = ingest(
            make_service(api_handler(head_sha="b" * 40, base_sha=latest_base)),
            event_payload(head_sha="b" * 40, base_sha=latest_base, action="synchronize"),
            delivery="newer-current-delivery",
        )
        release.set()
        stale = older_request.result(timeout=5)

    assert newer.created is True
    assert stale.ignored is True
    assert stale.run is None
    duplicate = ingest(older_service, older_body, delivery="stale-base-delivery")
    assert duplicate.ignored is True
    assert duplicate.created is False
    assert duplicate.run is None
    with factory() as db:
        delivery = db.get(GitHubWebhookDelivery, "stale-base-delivery")
        assert delivery.disposition == "ignored_stale"
        assert delivery.base_sha == event_base
        assert db.scalar(select(func.count(QARun.id))) == 1
        latest = db.get(GitHubPullRequestState, (REPOSITORY_ID, PR_NUMBER))
        assert latest.latest_head_sha == "b" * 40
        assert latest.latest_base_sha == latest_base


def test_deleting_run_preserves_delivery_and_redelivery_is_idempotent(runtime):
    factory, _, make_service = runtime
    engine = factory.kw["bind"]

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    # Ensure the listener applies to a fresh SQLite connection, since SQLite
    # foreign-key enforcement is connection-scoped.
    engine.dispose()
    service = make_service()
    body = event_payload()
    accepted = ingest(service, body, delivery="retained-delivery")
    assert accepted.created is True

    with factory.begin() as db:
        db.delete(db.get(QARun, accepted.run.id))

    with factory() as db:
        delivery = db.get(GitHubWebhookDelivery, "retained-delivery")
        assert delivery is not None
        assert delivery.qa_run_id is None

    duplicate = ingest(service, body, delivery="retained-delivery")
    assert duplicate.created is False
    assert duplicate.ignored is True
    assert duplicate.run is None
    with factory() as db:
        assert db.get(GitHubWebhookDelivery, "retained-delivery") is not None
        assert db.scalar(select(func.count(QARun.id))) == 0


def test_signature_malformed_payload_and_unsupported_event_handling(runtime):
    _, _, make_service = runtime
    service = make_service()
    with pytest.raises(GitHubWebhookError) as invalid:
        service.ingest(b"{}", signature="sha256=" + "0" * 64, event_name="pull_request", delivery_id="d1")
    assert invalid.value.status_code == 401
    with pytest.raises(GitHubWebhookError) as missing:
        service.ingest(b"{}", signature=None, event_name="pull_request", delivery_id="d1")
    assert missing.value.status_code == 401
    malformed = b"{not-json"
    with pytest.raises(GitHubWebhookError) as bad_json:
        service.ingest(malformed, signature=signature(malformed), event_name="pull_request", delivery_id="d2")
    assert bad_json.value.status_code == 400
    too_large = b"x" * (1_048_577)
    with pytest.raises(GitHubWebhookError) as oversized:
        service.ingest(too_large, signature=signature(too_large), event_name="pull_request", delivery_id="d-size")
    assert oversized.value.status_code == 413
    ignored = ingest(service, b"{}", delivery="d3", event="issues")
    assert ignored.ignored is True
    assert ignored.run is None


def test_repository_allowlist_prevents_untrusted_github_api_requests(runtime):
    _, _, make_service = runtime
    calls = []
    service = make_service(lambda request: calls.append(request) or httpx.Response(200, json={}))
    body = event_payload(repo_name="attacker/repository")
    with pytest.raises(GitHubWebhookError) as rejected:
        ingest(service, body)
    assert rejected.value.status_code == 403
    assert calls == []


def test_webhook_is_independent_of_user_auth_and_qa_api_keeps_permission_checks(runtime):
    factory, orchestrator, make_service = runtime
    app.dependency_overrides[get_qa_orchestrator] = lambda: orchestrator
    app.dependency_overrides[get_github_ingestion_service] = lambda: make_service()
    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            body = event_payload()
            response = client.post(
                "/v1/integrations/github/webhook",
                content=body,
                headers={
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "http-delivery",
                    "X-Hub-Signature-256": signature(body),
                },
            )
            assert response.status_code == 202, response.text
            assert response.json()["status"] == "accepted"
            assert response.json()["run_id"]
            assert TOKEN not in response.text
            assert client.get("/v1/qa/runs").status_code == 401
        with factory() as db:
            run = db.scalar(select(QARun).where(QARun.github_delivery_id == "http-delivery"))
            assert run.status == QARunStatus.COMPLETED.value
            assert run.result["assessment"] == "not_assessed"
            assert run.result["test_execution"]["status"] == "not_run"
            assert run.result["context"]["github_pull_request"]["head_sha"] == "b" * 40
            assert "pull request metadata was captured" in run.result["plan"]["summary"].lower()
    finally:
        app.dependency_overrides.clear()


def test_changed_file_pagination_and_rate_limit_errors():
    first_page = [
        {"filename": f"src/file-{index}.py", "status": "modified", "additions": 1, "deletions": 0, "changes": 1}
        for index in range(100)
    ]
    client = GitHubPullRequestClient(
        TOKEN,
        transport=httpx.MockTransport(api_handler(file_pages={1: first_page, 2: [{"filename": "last.py", "status": "added", "additions": 2, "deletions": 0, "changes": 2}]})),
    )
    pull_request, files = client.fetch_pull_request("acme/widget", PR_NUMBER)
    assert pull_request["number"] == PR_NUMBER
    assert len(files) == 101

    rate_limited = GitHubPullRequestClient(
        TOKEN,
        transport=httpx.MockTransport(lambda _request: httpx.Response(403, headers={"X-RateLimit-Remaining": "0"})),
    )
    with pytest.raises(GitHubRateLimited):
        rate_limited.fetch_pull_request("acme/widget", PR_NUMBER)

    secondary_rate_limited = GitHubPullRequestClient(
        TOKEN,
        transport=httpx.MockTransport(lambda _request: httpx.Response(403, headers={"Retry-After": "30"})),
    )
    with pytest.raises(GitHubRateLimited):
        secondary_rate_limited.fetch_pull_request("acme/widget", PR_NUMBER)

    missing = GitHubPullRequestClient(
        TOKEN,
        transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
    )
    with pytest.raises(GitHubAPIError) as not_found:
        missing.fetch_pull_request("acme/widget", PR_NUMBER)
    assert not_found.value.status_code == 404


def test_changed_file_count_metadata_and_three_thousand_file_boundary():
    def response_for_count(count):
        base = api_handler()
        def handler(request):
            if request.url.path.endswith("/files"):
                page = int(request.url.params.get("page", "1"))
                files = [{"filename": f"src/{page}-{index}.py", "status": "modified",
                    "additions": 1, "deletions": 0, "changes": 1} for index in range(100)]
                return httpx.Response(200, json=files if page <= 30 else [])
            response = base(request)
            if request.url.path.endswith("/pulls/42"):
                value = response.json()
                value["changed_files"] = count
                return httpx.Response(200, json=value)
            return response
        return handler

    exact_limit = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(response_for_count(3000)))
    _pr, files = exact_limit.fetch_pull_request("acme/widget", PR_NUMBER)
    assert len(files) == 3000

    over_limit = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(response_for_count(3001)))
    with pytest.raises(GitHubAPIError, match="invalid changed-file count"):
        over_limit.fetch_pull_request("acme/widget", PR_NUMBER)

    base = api_handler()
    def missing_count(request):
        response = base(request)
        if request.url.path.endswith("/pulls/42"):
            body = response.json()
            body.pop("changed_files", None)
            return httpx.Response(200, json=body)
        return response
    missing_metadata = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(missing_count))
    with pytest.raises(GitHubAPIError, match="invalid changed-file count"):
        missing_metadata.fetch_pull_request("acme/widget", PR_NUMBER)


def test_incomplete_changed_file_count_and_oversized_response_are_rejected():
    base_handler = api_handler(file_pages={1: []})
    def incomplete_handler(request):
        response = base_handler(request)
        if request.url.path.endswith("/pulls/42"):
            body = response.json()
            body["changed_files"] = 1
            return httpx.Response(200, json=body)
        return response
    incomplete = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(incomplete_handler))
    with pytest.raises(GitHubAPIError, match="snapshot is incomplete"):
        incomplete.fetch_pull_request("acme/widget", PR_NUMBER)

    oversized = GitHubPullRequestClient(
        TOKEN,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"{" + b"x" * 100 + b"}")),
        max_response_bytes=32,
    )
    with pytest.raises(GitHubAPIError, match="exceeded the permitted size"):
        oversized.fetch_pull_request("acme/widget", PR_NUMBER)


def test_transient_github_failure_retries_with_bounded_backoff():
    calls = 0
    delays = []
    delegate = api_handler()
    def handler(request):
        nonlocal calls
        if request.url.path.endswith("/pulls/42") and calls == 0:
            calls += 1
            return httpx.Response(503)
        calls += 1
        return delegate(request)
    client = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(handler), sleep=delays.append)
    _pull_request, files = client.fetch_pull_request("acme/widget", PR_NUMBER)
    assert files
    assert calls == 4
    assert delays == [0.25]


def test_overall_ingestion_deadline_bounds_multiple_requests():
    now = [0.0]
    delegate = api_handler()
    def slow_response(request):
        response = delegate(request)
        if request.url.path.endswith("/pulls/42"):
            now[0] += 2.0
        return response
    client = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(slow_response),
        deadline_seconds=1.0, monotonic=lambda: now[0], sleep=lambda _delay: None)
    with pytest.raises(GitHubAPIError) as timeout:
        client.fetch_pull_request("acme/widget", PR_NUMBER)
    assert timeout.value.status_code == 504
    assert "deadline" in str(timeout.value).lower()


def test_deadline_expires_during_stream_and_closes_response():
    now = [0.0]
    stream_state = {"closed": False}
    class SlowChunks(httpx.SyncByteStream):
        def __iter__(self):
            for chunk in (b"{", b"\"ok\"", b":true", b"}"):
                now[0] += 0.4
                yield chunk

        def close(self):
            stream_state["closed"] = True

    client = GitHubPullRequestClient(TOKEN,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=SlowChunks())),
        timeout=8, deadline_seconds=1, monotonic=lambda: now[0], sleep=lambda _delay: None)
    with pytest.raises(GitHubAPIError, match="deadline") as timeout:
        client.fetch_pull_request("acme/widget", PR_NUMBER)
    assert timeout.value.status_code == 504
    assert stream_state["closed"] is True


def test_base_change_during_file_retrieval_restarts_snapshot():
    old_base, current_base, head = "a" * 40, "d" * 40, "b" * 40
    read_number = 0
    def handler(request):
        nonlocal read_number
        if request.url.path.endswith("/pulls/42"):
            read_number += 1
            base_sha = old_base if read_number == 1 else current_base
            response = api_handler(head_sha=head, base_sha=base_sha)(request)
            return response
        if request.url.path.endswith("/files"):
            page = int(request.url.params.get("page", "1"))
            if page > 1:
                return httpx.Response(200, json=[])
            filename = "old-base.py" if read_number == 1 else "current-base.py"
            return httpx.Response(200, json=[{"filename": filename, "status": "modified", "additions": 1, "deletions": 0, "changes": 1}])
        return httpx.Response(404)
    client = GitHubPullRequestClient(TOKEN, transport=httpx.MockTransport(handler))
    pull_request, files = client.fetch_pull_request("acme/widget", PR_NUMBER)
    assert pull_request["base"]["sha"] == current_base
    assert files[0]["filename"] == "current-base.py"
    assert read_number == 4  # First metadata, recheck, retry metadata, final recheck.

    inaccessible = GitHubPullRequestClient(
        TOKEN,
        transport=httpx.MockTransport(lambda _request: httpx.Response(401)),
    )
    with pytest.raises(GitHubAPIError) as unauthorized:
        inaccessible.fetch_pull_request("acme/widget", PR_NUMBER)
    assert unauthorized.value.status_code == 502


def test_revision_mismatch_is_recorded_as_stale_without_creating_run(runtime):
    factory, _, make_service = runtime
    service = make_service(api_handler(head_sha="d" * 40))
    body = event_payload(head_sha="b" * 40)
    outcome = ingest(service, body)
    assert outcome.ignored is True
    with factory() as db:
        assert db.scalar(select(func.count(QARun.id))) == 0
        delivery = db.get(GitHubWebhookDelivery, "delivery-1")
        assert delivery.disposition == "ignored_stale"


def test_revision_changing_during_file_retrieval_is_recorded_as_stale(runtime):
    factory, _, make_service = runtime
    reads = 0
    base_handler = api_handler(head_sha="b" * 40)
    def changed_mid_read(request):
        nonlocal reads
        if request.url.path.endswith("/pulls/42"):
            reads += 1
            if reads == 2:
                response = base_handler(request)
                pr = response.json()
                pr["head"]["sha"] = "c" * 40
                return httpx.Response(200, json=pr)
        return base_handler(request)
    outcome = ingest(make_service(changed_mid_read), event_payload(), delivery="mid-read-stale")
    assert outcome.ignored is True
    with factory() as db:
        assert db.scalar(select(func.count(QARun.id))) == 0
        assert db.get(GitHubWebhookDelivery, "mid-read-stale").disposition == "ignored_stale"
