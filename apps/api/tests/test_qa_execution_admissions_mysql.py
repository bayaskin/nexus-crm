"""Guarded MySQL integration coverage for atomic QA admission capacity."""

from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import utcnow_naive
from app.models.github import GitHubPRRevision
from app.models.identity import User
from app.models.qa import QARun
from app.models.qa_execution import QAExecutionAdmission, QAExecutionCapacity
from app.schemas.github import GitHubChangedFile, GitHubPRContext
from app.schemas.qa import (
    CreateQAExecutionAdmissionRequest,
    FoundationContext,
    FoundationPlan,
    QAExecutionAdmissionState,
    QARunConfig,
    QARunResult,
    QARunStage,
    QARunState,
    QARunStatus,
    QATriggerType,
)
from app.schemas.test_plan import bind_test_plan, build_test_planning_input
from app.services.change_impact import ChangeImpactAnalyzer
from app.services.qa_admissions import QAAdmissionService
from app.services.qa_providers import MockTestPlanningProvider


@dataclass
class _OwnedRecords:
    run_ids: list[UUID] = field(default_factory=list)
    revision_ids: list[str] = field(default_factory=list)
    user_ids: list[UUID] = field(default_factory=list)


def _fail_unsafe_target() -> None:
    expected_project = "nexus-qa-admission-test"
    if (
        os.environ.get("QA_ADMISSION_TEST_PROJECT") != expected_project
        or os.environ.get("QA_ADMISSION_COMPOSE_PROJECT") != expected_project
        or os.environ.get("QA_ADMISSION_DB_STORAGE") != "tmpfs"
    ):
        pytest.fail("QA admission test refused a non-isolated Compose project", pytrace=False)
    database_url = os.environ.get("DATABASE_URL")
    test_database_url = os.environ.get("MYSQL_TEST_DATABASE_URL")
    if not database_url or not test_database_url:
        pytest.fail("QA MySQL admission test requires both guarded database URLs", pytrace=False)
    try:
        database = make_url(database_url)
        test_database = make_url(test_database_url)
        configured_database = make_url(settings.database_url)
    except Exception:
        pytest.fail("QA MySQL admission test database URL is invalid", pytrace=False)

    expected = ("mysql+pymysql", "qa-mysql", 3306, "nexus_qa_test", "nexus_qa_runner")
    parsed_urls = (database, test_database, configured_database)
    if (
        os.environ.get("APP_ENV") != "test"
        or settings.app_env != "test"
        or os.environ.get("MYSQL_TEST_DATABASE_KIND") != "disposable"
        or os.environ.get("QA_DATABASE_HOST") != "qa-mysql"
        or os.environ.get("QA_DATABASE_NAME") != "nexus_qa_test"
        or os.environ.get("QA_DATABASE_USER") != "nexus_qa_runner"
        or not os.environ.get("QA_DATABASE_PASSWORD")
        or database_url != test_database_url
        or any(
            (url.drivername, url.host, url.port, url.database, url.username) != expected
            or not url.password
            for url in parsed_urls
        )
        or len({url.password for url in parsed_urls}) != 1
        or database.password != os.environ.get("QA_DATABASE_PASSWORD")
    ):
        pytest.fail("QA MySQL admission test refused a non-disposable database target", pytrace=False)


def _make_context(index: int) -> GitHubPRContext:
    now = utcnow_naive()
    return GitHubPRContext(
        repository_id=987654321,
        repository_full_name="nexus-qa/admission-test",
        repository_owner="nexus-qa",
        repository_name="admission-test",
        pull_request_number=700000 + index,
        title="Isolated QA admission concurrency fixture",
        url=f"https://github.com/nexus-qa/admission-test/pull/{700000 + index}",
        author="qa-test-fixture",
        state="open",
        draft=False,
        source_branch=f"qa-admission-{index}",
        target_branch="main",
        base_sha="a" * 40,
        head_sha=f"{index + 1:040x}",
        changed_files=[
            GitHubChangedFile(
                path="apps/api/app/api/routes/auth.py",
                status="modified",
                additions=1,
                deletions=0,
                changes=1,
            )
        ],
        reported_changed_files=1,
        event_type="synchronize",
        delivery_id=f"qa-admission-test-{uuid4()}",
        event_updated_at=now,
    )


def _make_run(index: int) -> tuple[User, GitHubPRRevision, QARun]:
    now = utcnow_naive()
    context = _make_context(index)
    impact = ChangeImpactAnalyzer().analyze(context)
    planning_input = build_test_planning_input(context, impact)
    proposal = asyncio.run(MockTestPlanningProvider().create_test_plan_proposal(planning_input))
    test_plan = bind_test_plan(context, impact, proposal)

    actor = User(
        email=f"qa-admission-{uuid4()}@example.com",
        full_name="Disposable QA Admission Actor",
        password_hash="not-used-for-admission-tests",
        is_active=True,
        is_superuser=True,
        must_change_password=False,
        roles=[],
        permission_overrides=[],
    )
    actor.id = uuid4()
    run_id = uuid4()
    run_context = FoundationContext(
        note="Disposable MySQL admission test data.",
        repository_analysis_performed=False,
        github_pull_request=context,
    )
    run_plan = FoundationPlan(summary="Suggested only", next_step="No runner is started by this test.")
    stages = [QARunStage(name="test_planning", summary="Validated test plan stored.")]
    state = QARunState(
        run_id=run_id,
        trigger_type=QATriggerType.GITHUB_PULL_REQUEST,
        status=QARunStatus.COMPLETED,
        progress=100,
        config=QARunConfig(timeout_seconds=30),
        context=run_context,
        plan=run_plan,
        stages=stages,
        github_context=context,
        change_impact_analysis=impact,
        test_plan=test_plan,
    )
    result = QARunResult(
        summary="Completed admission test fixture.",
        context=run_context,
        plan=run_plan,
        stages=stages,
        change_impact_analysis=impact,
        test_plan=test_plan,
    )
    revision = GitHubPRRevision(
        id=context.revision_id,
        repository_id=context.repository_id,
        pull_request_number=context.pull_request_number,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
        observed_sequence=1,
        created_at=context.event_updated_at,
    )
    run = QARun(
        id=run_id,
        requested_by_user_id=actor.id,
        trigger_type=QATriggerType.GITHUB_PULL_REQUEST.value,
        status=QARunStatus.COMPLETED.value,
        progress=100,
        timeout_seconds=30,
        input_config={"timeout_seconds": 30},
        state_data=state.model_dump(mode="json"),
        result=result.model_dump(mode="json"),
        github_repository_id=context.repository_id,
        github_pr_number=context.pull_request_number,
        github_head_sha=context.head_sha,
        github_context=context.model_dump(mode="json"),
        github_revision_id=context.revision_id,
        completed_at=now,
    )
    return actor, revision, run


@pytest.fixture
def isolated_admission_records():
    _fail_unsafe_target()
    owned = _OwnedRecords()
    original_limit: int | None = None

    # Refuse to change a shared QA limit while any reservation is already live.
    with SessionLocal.begin() as db:
        capacity = db.scalar(
            select(QAExecutionCapacity).where(QAExecutionCapacity.id == 1).with_for_update()
        )
        if capacity is None:
            pytest.fail("QA execution capacity row is missing; apply the guarded migration first", pytrace=False)
        active_rows = db.scalar(
            select(func.count()).select_from(QAExecutionAdmission).where(
                QAExecutionAdmission.state.in_(("reserved", "running"))
            )
        )
        if capacity.active_count != 0 or active_rows != 0:
            pytest.fail("QA admission test requires an idle capacity pool", pytrace=False)
        original_limit = capacity.max_concurrent_executions
        capacity.max_concurrent_executions = 1

    try:
        yield owned
    finally:
        if original_limit is not None:
            # Delete only rows created by this test. Recompute the counter while
            # holding the same singleton lock used by admission/finish methods,
            # preserving any unrelated admission that appeared meanwhile.
            with SessionLocal.begin() as db:
                capacity = db.scalar(
                    select(QAExecutionCapacity).where(QAExecutionCapacity.id == 1).with_for_update()
                )
                if capacity is None:
                    raise RuntimeError("QA execution capacity row disappeared during test cleanup")
                if owned.run_ids:
                    db.execute(
                        delete(QAExecutionAdmission).where(
                            QAExecutionAdmission.qa_run_id.in_(owned.run_ids)
                        )
                    )
                    db.execute(delete(QARun).where(QARun.id.in_(owned.run_ids)))
                if owned.revision_ids:
                    db.execute(
                        delete(GitHubPRRevision).where(GitHubPRRevision.id.in_(owned.revision_ids))
                    )
                if owned.user_ids:
                    db.execute(delete(User).where(User.id.in_(owned.user_ids)))
                capacity.active_count = db.scalar(
                    select(func.count()).select_from(QAExecutionAdmission).where(
                        QAExecutionAdmission.state.in_(("reserved", "running"))
                    )
                )
                capacity.max_concurrent_executions = original_limit


def test_mysql_admission_capacity_is_atomic_under_eight_concurrent_requests(isolated_admission_records):
    owned: _OwnedRecords = isolated_admission_records
    fixtures = [_make_run(index) for index in range(8)]
    actors = [item[0] for item in fixtures]
    revisions = [item[1] for item in fixtures]
    runs = [item[2] for item in fixtures]
    owned.user_ids.extend(actor.id for actor in actors)
    owned.revision_ids.extend(revision.id for revision in revisions)
    owned.run_ids.extend(run.id for run in runs)

    with SessionLocal.begin() as db:
        for actor, revision, run in fixtures:
            db.add(actor)
            db.add(revision)
            db.add(run)

    barrier = threading.Barrier(8)
    service = QAAdmissionService()

    def attempt(actor: User, run_id: UUID) -> tuple[UUID, str, str | None]:
        with SessionLocal() as db:
            barrier.wait(timeout=30)
            admission = service.admit(
                db,
                actor,
                run_id,
                CreateQAExecutionAdmissionRequest(suite_id="api-auth"),
            )
            return admission.id, admission.state, admission.reason_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(attempt, actor, run.id) for actor, run in zip(actors, runs, strict=True)]
        results = [future.result(timeout=90) for future in futures]

    winners = [item for item in results if item[1] == "reserved"]
    denials = [item for item in results if item[1] == "denied"]
    assert len(winners) == 1
    assert len(denials) == 7
    assert all(reason == "concurrency_limit" for _admission_id, _state, reason in denials)

    with SessionLocal() as db:
        active = db.scalars(
            select(QAExecutionAdmission).where(
                QAExecutionAdmission.state.in_(("reserved", "running"))
            )
        ).all()
        capacity = db.get(QAExecutionCapacity, 1)
        assert len(active) == 1
        assert active[0].id == winners[0][0]
        assert capacity is not None and capacity.active_count == 1

    winner_id = winners[0][0]
    with SessionLocal() as db:
        assert service.finish(db, winner_id, QAExecutionAdmissionState.COMPLETED)

    with SessionLocal() as db:
        assert db.get(QAExecutionCapacity, 1).active_count == 0

    denied_index = next(index for index, result in enumerate(results) if result[1] == "denied")
    with SessionLocal() as db:
        retry = service.admit(
            db,
            actors[denied_index],
            runs[denied_index].id,
            CreateQAExecutionAdmissionRequest(suite_id="api-auth"),
        )
        assert retry.state == "reserved"
        assert retry.reason_code is None
        assert service.finish(db, retry.id, QAExecutionAdmissionState.COMPLETED)

    with SessionLocal() as db:
        capacity = db.get(QAExecutionCapacity, 1)
        assert capacity is not None and capacity.active_count == 0


def test_mysql_same_run_suite_is_idempotent_after_authentication_snapshot(isolated_admission_records):
    owned: _OwnedRecords = isolated_admission_records
    actor, revision, run = _make_run(0)
    owned.user_ids.append(actor.id)
    owned.revision_ids.append(revision.id)
    owned.run_ids.append(run.id)
    with SessionLocal.begin() as db:
        db.add(actor)
        db.add(revision)
        db.add(run)

    barrier = threading.Barrier(8)
    service = QAAdmissionService()

    def attempt() -> tuple[UUID, str, str | None]:
        with SessionLocal() as db:
            # Match the route's pre-admission identity and permission reads.
            # These establish the REPEATABLE READ snapshot before the workers
            # contend on the capacity row.
            authenticated_actor = db.scalar(select(User).where(User.id == actor.id))
            assert authenticated_actor is not None
            list(authenticated_actor.roles)
            list(authenticated_actor.permission_overrides)
            barrier.wait(timeout=30)
            admission = service.admit(
                db,
                authenticated_actor,
                run.id,
                CreateQAExecutionAdmissionRequest(suite_id="api-auth"),
            )
            return admission.id, admission.state, admission.reason_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(attempt) for _ in range(8)]
        results = [future.result(timeout=90) for future in futures]

    ids = {item[0] for item in results}
    assert len(ids) == 1
    assert all(state == "reserved" for _admission_id, state, _reason in results)
    assert all(reason is None for _admission_id, _state, reason in results)

    with SessionLocal() as db:
        active = db.scalars(
            select(QAExecutionAdmission).where(
                QAExecutionAdmission.qa_run_id == run.id,
                QAExecutionAdmission.suite_id == "api-auth",
                QAExecutionAdmission.state.in_(["reserved", "running"]),
            )
        ).all()
        capacity = db.get(QAExecutionCapacity, 1)
        assert len(active) == 1
        assert active[0].id == next(iter(ids))
        assert capacity is not None and capacity.active_count == 1
        assert sum(item.state in ("reserved", "running") for item in db.scalars(
            select(QAExecutionAdmission).where(QAExecutionAdmission.qa_run_id == run.id)
        ).all()) == 1
