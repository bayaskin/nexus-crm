from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "qa-admissions-test-signing-key-2026-long-enough")

from app.api.deps import get_current_user
from app.api.routes.qa import get_qa_admission_service
from app.core.database import Base, get_db
from app.main import app
from app.models.github import GitHubPRRevision
from app.models.identity import User
from app.models.qa import QARun
from app.models.qa_execution import QAExecutionAdmission, QAExecutionCapacity
from app.schemas.github import GitHubChangedFile, GitHubPRContext
from app.schemas.qa import (
    CreateQAExecutionAdmissionRequest,
    QAExecutionAdmissionState,
    FoundationContext,
    FoundationPlan,
    QARunConfig,
    QARunResult,
    QARunStage,
    QARunState,
    QARunStatus,
    QATriggerType,
)
from app.schemas.test_plan import TestPlan, bind_test_plan, build_test_planning_input
from app.schemas.qa_execution import TrustedRunnerContract
from app.services.change_impact import ChangeImpactAnalyzer
from app.services.permissions import has_permission
from app.services.qa_admissions import (
    ExecutionClaimRejected,
    InternalExecutionCaller,
    QAAdmissionService,
    map_plan_evidence_to_suite_ids,
    trusted_qa_execution_environment,
)
from app.services.qa_providers import MockTestPlanningProvider
from app.services.qa_suite_registry import (
    DEFAULT_SUITE_REGISTRY,
    ExecutionEnvironment,
    SuiteDefinition,
    SuiteRegistry,
    SuiteRunner,
)


def _context(path: str = "apps/api/app/api/routes/auth.py") -> GitHubPRContext:
    return GitHubPRContext(
        repository_id=21345,
        repository_full_name="example/nexus",
        repository_owner="example",
        repository_name="nexus",
        pull_request_number=41,
        title="Authentication update",
        url="https://github.com/example/nexus/pull/41",
        author="contributor",
        state="open",
        draft=False,
        source_branch="feature/auth",
        target_branch="main",
        base_sha="a" * 40,
        head_sha="b" * 40,
        changed_files=[GitHubChangedFile(path=path, status="modified", additions=3, deletions=1, changes=4)],
        reported_changed_files=1,
        event_type="synchronize",
        delivery_id=f"admission-{uuid4()}",
        event_updated_at=datetime(2026, 11, 25),
    )


def _make_plan(context: GitHubPRContext):
    impact = ChangeImpactAnalyzer().analyze(context)
    planning_input = build_test_planning_input(context, impact)
    proposal = asyncio.run(MockTestPlanningProvider().create_test_plan_proposal(planning_input))
    return impact, bind_test_plan(context, impact, proposal)


def _environment(runner: SuiteRunner) -> ExecutionEnvironment:
    common = {
        "app_env": "test",
        "qa_mode": True,
        "compose_project": "nexus-qa",
        "network_internal": True,
        "development_database_access": False,
        "development_service_access": False,
    }
    if runner == SuiteRunner.BACKEND_UNIT:
        values = {**common, "network_profile": "none"}
    elif runner == SuiteRunner.API:
        values = {
            **common,
            "network_profile": "qa_app_data",
            "database_host": "qa-mysql",
            "database_name": "nexus_qa_test",
            "service_base_url": "http://api:8000",
        }
    else:
        values = {**common, "network_profile": "qa_app", "service_base_url": "http://proxy"}
    return ExecutionEnvironment.model_validate(values)


@pytest.fixture
def admission_db(tmp_path: Path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'admissions.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 20},
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(QAExecutionCapacity(id=1, active_count=0, max_concurrent_executions=2))
    try:
        yield engine, factory
    finally:
        engine.dispose()


def _seed_run(factory, *, context: GitHubPRContext | None = None) -> tuple[User, QARun, TestPlan]:
    context = context or _context()
    impact, test_plan = _make_plan(context)
    actor = User(
        email=f"admission-{uuid4()}@example.com",
        full_name="QA Admission Actor",
        password_hash="not-used",
        is_active=True,
        is_superuser=True,
        must_change_password=False,
        roles=[],
        permission_overrides=[],
    )
    actor.id = uuid4()
    run_id = uuid4()
    foundation_context = FoundationContext(
        note="Stored revision evidence.",
        repository_analysis_performed=False,
        github_pull_request=context,
    )
    foundation_plan = FoundationPlan(summary="Stored", next_step="No execution yet.")
    stages = [QARunStage(name="test_planning", summary="Suggested plan.")]
    state = QARunState(
        run_id=run_id,
        trigger_type=QATriggerType.GITHUB_PULL_REQUEST,
        status=QARunStatus.COMPLETED,
        progress=100,
        config=QARunConfig(timeout_seconds=30),
        context=foundation_context,
        plan=foundation_plan,
        stages=stages,
        github_context=context,
        change_impact_analysis=impact,
        test_plan=test_plan,
    )
    result = QARunResult(
        summary="Suggested only.",
        context=foundation_context,
        plan=foundation_plan,
        stages=stages,
        change_impact_analysis=impact,
        test_plan=test_plan,
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
        completed_at=datetime(2026, 11, 25),
    )
    with factory.begin() as db:
        db.add(actor)
        db.add(GitHubPRRevision(
            id=context.revision_id,
            repository_id=context.repository_id,
            pull_request_number=context.pull_request_number,
            base_sha=context.base_sha,
            head_sha=context.head_sha,
            observed_sequence=1,
            created_at=context.event_updated_at,
        ))
        db.add(run)
    return actor, run, test_plan


def _service(**kwargs) -> QAAdmissionService:
    return QAAdmissionService(environment_factory=_environment, **kwargs)


def _request(suite_id: str = "unit-backend", **kwargs) -> CreateQAExecutionAdmissionRequest:
    return CreateQAExecutionAdmissionRequest(suite_id=suite_id, **kwargs)


def test_authorized_actor_can_reserve_a_suite_without_exposing_runner_details(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    assert has_permission(actor, "qa:execute")
    db = factory()
    try:
        admission = _service().admit(db, actor, run.id, _request())
    finally:
        db.close()
    assert admission.state == "reserved"
    assert admission.suite_id == "unit-backend"
    assert admission.requested_by_user_id == actor.id
    assert admission.qa_run_id == run.id
    assert admission.reserved_until > admission.created_at
    assert "selectors" not in admission.__dict__
    with factory() as db:
        assert db.get(QAExecutionCapacity, 1).active_count == 1


def test_repeated_admission_for_same_run_and_suite_returns_existing_reservation(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    service = _service()
    db = factory()
    try:
        first = service.admit(db, actor, run.id, _request())
        second = service.admit(db, actor, run.id, _request())
    finally:
        db.close()
    assert first.id == second.id
    with factory() as db:
        assert db.get(QAExecutionCapacity, 1).active_count == 1
        assert len(db.scalars(select(QAExecutionAdmission)).all()) == 1


def test_database_unique_active_slot_rejects_duplicate_but_keeps_terminal_history(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    db = factory()
    try:
        first = _service().admit(db, actor, run.id, _request())
        assert first.state == "reserved"
        db.add(
            QAExecutionAdmission(
                suite_id=first.suite_id,
                requested_by_user_id=actor.id,
                qa_run_id=run.id,
                state="reserved",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add_all(
            [
                QAExecutionAdmission(
                    suite_id=first.suite_id,
                    requested_by_user_id=actor.id,
                    qa_run_id=run.id,
                    state="completed",
                ),
                QAExecutionAdmission(
                    suite_id=first.suite_id,
                    requested_by_user_id=actor.id,
                    qa_run_id=run.id,
                    state="failed",
                ),
            ]
        )
        db.commit()
    finally:
        db.close()
    with factory() as db:
        rows = db.scalars(select(QAExecutionAdmission)).all()
        assert sum(row.state in {"reserved", "running"} for row in rows) == 1
        assert sum(row.state in {"completed", "failed"} for row in rows) == 2
        assert db.get(QAExecutionCapacity, 1).active_count == 1


def test_missing_revision_row_fails_closed(admission_db):
    _engine, factory = admission_db
    _actor, run, _plan = _seed_run(factory)
    run.github_revision_id = str(uuid4())
    with factory() as db:
        assert _service()._load_validated_plan(db, run) is None


def test_forged_persisted_impact_analysis_fails_admission_closed(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    forged_state = dict(run.state_data)
    forged_result = dict(run.result)
    forged_impact = dict(forged_state["change_impact_analysis"])
    forged_gaps = list(forged_impact["coverage_gaps"])
    forged_gaps.append({"code": "fabricated-gap", "message": "Invented coverage evidence."})
    forged_impact["coverage_gaps"] = forged_gaps
    forged_state["change_impact_analysis"] = forged_impact
    forged_result["change_impact_analysis"] = forged_impact
    with factory.begin() as db:
        stored_run = db.get(QARun, run.id)
        stored_run.state_data = forged_state
        stored_run.result = forged_result
    with factory() as db:
        denied = _service().admit(db, actor, run.id, _request())
        assert denied.state == "denied"
        assert denied.reason_code == "plan_unavailable"
        assert db.get(QAExecutionCapacity, 1).active_count == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("head_sha", "c" * 40),
        ("base_sha", "d" * 40),
        ("pull_request_number", 42),
    ],
)
def test_revision_row_mismatch_fails_admission_closed(admission_db, field, value):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    with factory.begin() as db:
        revision = db.get(GitHubPRRevision, run.github_revision_id)
        setattr(revision, field, value)
    with factory() as db:
        denied = _service().admit(db, actor, run.id, _request())
        assert denied.state == "denied"
        assert denied.reason_code == "plan_unavailable"
        assert db.get(QAExecutionCapacity, 1).active_count == 0


def test_unauthorized_unknown_and_disabled_requests_are_denied_without_consuming_capacity(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    actor.is_superuser = False
    # The actor is not attached to any role granting qa:execute.
    unknown_registry = DEFAULT_SUITE_REGISTRY
    cases = [
        (actor, _service(registry=unknown_registry), _request(), "unauthorized"),
        (
            _seed_run(factory)[0], _service(), _request("unknown-suite"), "unknown_suite",
        ),
    ]
    disabled = SuiteRegistry([
        SuiteDefinition(
            suite_id="unit-backend",
            description="Disabled test suite.",
            runner_service=SuiteRunner.BACKEND_UNIT,
            selector_id="backend-unit-test-v1",
            selectors=("tests/test_auth_login.py",),
            timeout_limit_seconds=300,
            enabled=False,
        ),
    ])
    cases.append((_seed_run(factory)[0], _service(registry=disabled), _request(), "suite_disabled"))
    for case_actor, service, request, expected_reason in cases:
        db = factory()
        try:
            denied = service.admit(db, case_actor, run.id, request)
        finally:
            db.close()
        assert denied.state == "denied"
        assert denied.reason_code == expected_reason
    with factory() as db:
        assert db.get(QAExecutionCapacity, 1).active_count == 0


def test_override_attempts_are_rejected_by_strict_request_schema(admission_db):
    with pytest.raises(ValidationError):
        CreateQAExecutionAdmissionRequest.model_validate({
            "suite_id": "unit-backend",
            "command": "pytest tests/attacker.py",
            "selector": "../../nexus_crm",
            "environment": {"DATABASE_URL": "secret"},
            "docker_args": ["--network=host"],
        })


def test_http_admission_preserves_qa_execute_permission_and_hides_runner_details(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    actor.is_superuser = False
    app.dependency_overrides[get_current_user] = lambda: actor

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_qa_admission_service] = lambda: _service()
    try:
        with TestClient(app) as client:
            denied = client.post(
                f"/v1/qa/runs/{run.id}/admissions", json={"suite_id": "unit-backend"}
            )
            assert denied.status_code == 403

            actor.is_superuser = True
            injected = client.post(
                f"/v1/qa/runs/{run.id}/admissions",
                json={"suite_id": "unit-backend", "docker_args": ["--network=host"]},
            )
            assert injected.status_code == 422

            admitted = client.post(
                f"/v1/qa/runs/{run.id}/admissions", json={"suite_id": "unit-backend"}
            )
            assert admitted.status_code == 202
            response = admitted.json()
            assert response["state"] == "reserved"
            assert response["suite_id"] == "unit-backend"
            assert "runner_service" not in response
            assert "selectors" not in response
    finally:
        app.dependency_overrides.clear()


def test_unsafe_server_environment_denies_admission(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    unsafe = {**_environment(SuiteRunner.API).model_dump(mode="python"), "database_name": "nexus_crm"}
    db = factory()
    try:
        denied = QAAdmissionService(environment_factory=lambda _runner: unsafe).admit(
            db, actor, run.id, _request("api-auth")
        )
    finally:
        db.close()
    assert denied.state == "denied"
    assert denied.reason_code == "unsafe_environment"
    with factory() as db:
        assert db.get(QAExecutionCapacity, 1).active_count == 0


def test_suite_not_supported_by_plan_and_timeout_denials_do_not_reserve_capacity(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    service = _service()
    db = factory()
    try:
        unsupported = service.admit(db, actor, run.id, _request("ui-regression"))
        assert unsupported.state == "denied"
        assert unsupported.reason_code == "suite_not_suggested"
        too_long = service.admit(
            db, actor, run.id, _request("api-auth", requested_timeout_seconds=601)
        )
        assert too_long.state == "denied"
        assert too_long.reason_code == "timeout_exceeded"
    finally:
        db.close()
    with factory() as db:
        assert db.get(QAExecutionCapacity, 1).active_count == 0


def test_trusted_environment_derivation_rejects_development_database_targets():
    assert trusted_qa_execution_environment(
        SuiteRunner.API,
        app_env="test",
        database_url="mysql+pymysql://nexus_qa_runner:synthetic@qa-mysql:3306/nexus_qa_test",
    ) is not None
    assert trusted_qa_execution_environment(
        SuiteRunner.API,
        app_env="test",
        database_url="mysql+pymysql://user:synthetic@mysql:3306/nexus_crm",
    ) is None


def test_mapping_uses_only_rebound_candidate_suite_evidence(admission_db):
    context = _context()
    impact, plan = _make_plan(context)
    assert "apps/api/tests/test_permission_resolution.py" in {
        suite.suite_id for suite in impact.candidate_test_suites
    }
    mapped = map_plan_evidence_to_suite_ids(context, impact, plan)
    assert "unit-backend" in mapped
    assert "api-auth" in mapped
    assert "api-regression" in mapped
    assert "ui-regression" not in mapped

    forged_revision = plan.revision.model_copy(update={"head_sha": "c" * 40})
    forged = plan.model_copy(update={"revision": forged_revision})
    with pytest.raises(ValueError):
        map_plan_evidence_to_suite_ids(context, impact, forged)


def test_capacity_exhaustion_denies_without_incrementing_beyond_limit(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    with factory.begin() as db:
        capacity = db.get(QAExecutionCapacity, 1)
        capacity.max_concurrent_executions = 1
    service = _service()
    first_db = factory()
    second_db = factory()
    try:
        first = service.admit(first_db, actor, run.id, _request())
        second = service.admit(second_db, actor, run.id, _request("api-auth"))
    finally:
        first_db.close()
        second_db.close()
    assert first.state == "reserved"
    assert second.state == "denied"
    assert second.reason_code == "concurrency_limit"
    with factory() as db:
        assert db.get(QAExecutionCapacity, 1).active_count == 1


def test_concurrent_reservations_cannot_claim_more_than_final_slot(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    barrier = threading.Barrier(3)
    service = _service()
    suites = ("unit-backend", "api-auth", "api-regression")

    def reserve(index):
        db = factory()
        try:
            barrier.wait(timeout=10)
            return service.admit(db, actor, run.id, _request(suites[index]))
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        admissions = list(pool.map(reserve, range(3)))
    assert sum(item.state == "reserved" for item in admissions) == 2
    assert sum(item.state == "denied" for item in admissions) == 1
    with factory() as db:
        assert db.get(QAExecutionCapacity, 1).active_count == 2
        assert len(db.scalars(select(QAExecutionAdmission)).all()) == 3


def test_transaction_failure_rolls_back_admission_and_capacity(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    db = factory()

    def fail_commit(_session):
        raise RuntimeError("synthetic transaction failure")

    event.listen(db, "before_commit", fail_commit, once=True)
    try:
        with pytest.raises(RuntimeError, match="synthetic transaction failure"):
            _service().admit(db, actor, run.id, _request())
    finally:
        db.close()
    with factory() as db:
        assert db.get(QAExecutionCapacity, 1).active_count == 0
        assert db.scalars(select(QAExecutionAdmission)).all() == []


def test_expired_reservation_recovery_releases_capacity_but_running_work_is_retained(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    now = datetime(2026, 11, 25, 12, 0, 0)
    clock_value = [now]
    service = _service(clock=lambda: clock_value[0])
    db = factory()
    try:
        expired_candidate = service.admit(db, actor, run.id, _request())
    finally:
        db.close()
    clock_value[0] = now + timedelta(minutes=5, seconds=1)
    db = factory()
    try:
        assert service.recover_expired_reservations(db) == 1
    finally:
        db.close()
    with factory() as db:
        expired = db.get(QAExecutionAdmission, expired_candidate.id)
        assert expired.state == "expired"
        assert expired.reason_code == "reservation_expired"
        assert db.get(QAExecutionCapacity, 1).active_count == 0

    db = factory()
    try:
        running = service.admit(db, actor, run.id, _request())
        contract = service.claim_execution(
            db, running.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR
        )
        assert contract.admission_id == running.id
    finally:
        db.close()
    clock_value[0] += timedelta(days=1)
    db = factory()
    try:
        assert service.recover_expired_reservations(db) == 0
    finally:
        db.close()
    with factory() as db:
        assert db.get(QAExecutionAdmission, running.id).state == "running"
        assert db.get(QAExecutionCapacity, 1).active_count == 1


def test_finish_releases_capacity_once_and_expired_reservation_cannot_start(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    service = _service()
    db = factory()
    try:
        admission = service.admit(db, actor, run.id, _request())
        assert not service.finish(
            db,
            admission.id,
            state=QAExecutionAdmissionState.COMPLETED,
            execution_attempt_id=uuid4(),
            caller=InternalExecutionCaller.QA_EXECUTION_COORDINATOR,
        )
        contract = service.claim_execution(
            db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR
        )
        assert contract.execution_attempt_id is not None
        with pytest.raises(ExecutionClaimRejected, match="claim_already_owned"):
            service.claim_execution(db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR)
        assert not service.finish(
            db,
            admission.id,
            state=QAExecutionAdmissionState.COMPLETED,
            execution_attempt_id=uuid4(),
            caller=InternalExecutionCaller.QA_EXECUTION_COORDINATOR,
        )
        assert service.finish(
            db,
            admission.id,
            state=QAExecutionAdmissionState.COMPLETED,
            execution_attempt_id=contract.execution_attempt_id,
            caller=InternalExecutionCaller.QA_EXECUTION_COORDINATOR,
        )
        assert not service.finish(
            db,
            admission.id,
            state=QAExecutionAdmissionState.COMPLETED,
            execution_attempt_id=contract.execution_attempt_id,
            caller=InternalExecutionCaller.QA_EXECUTION_COORDINATOR,
        )
    finally:
        db.close()
    with factory() as db:
        assert db.get(QAExecutionCapacity, 1).active_count == 0


def test_running_admission_can_fail_and_release_capacity_once(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    service = _service()
    with factory() as db:
        admission = service.admit(db, actor, run.id, _request())
        admission_id = admission.id
        contract = service.claim_execution(
            db, admission_id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR
        )
        execution_attempt_id = contract.execution_attempt_id
        assert execution_attempt_id is not None
        assert not service.finish(
            db,
            admission_id,
            QAExecutionAdmissionState.FAILED,
            execution_attempt_id=uuid4(),
            caller=InternalExecutionCaller.QA_EXECUTION_COORDINATOR,
        )
        assert service.finish(
            db,
            admission_id,
            QAExecutionAdmissionState.FAILED,
            execution_attempt_id=execution_attempt_id,
            caller=InternalExecutionCaller.QA_EXECUTION_COORDINATOR,
        )
        assert not service.finish(
            db,
            admission_id,
            QAExecutionAdmissionState.FAILED,
            execution_attempt_id=execution_attempt_id,
            caller=InternalExecutionCaller.QA_EXECUTION_COORDINATOR,
        )
    with factory() as db:
        stored = db.get(QAExecutionAdmission, admission_id)
        assert stored is not None
        assert stored.state == QAExecutionAdmissionState.FAILED.value
        assert stored.execution_attempt_id == execution_attempt_id
        assert stored.worker_id == InternalExecutionCaller.QA_EXECUTION_COORDINATOR.value
        assert db.get(QAExecutionCapacity, 1).active_count == 0


def test_claim_requires_internal_caller_and_known_reserved_admission(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    service = _service()
    with factory() as db:
        with pytest.raises(ExecutionClaimRejected, match="unauthorized_internal_caller"):
            service.claim_execution(db, uuid4(), "qa-execution-coordinator")
        with pytest.raises(ExecutionClaimRejected, match="admission_not_found"):
            service.claim_execution(
                db, uuid4(), InternalExecutionCaller.QA_EXECUTION_COORDINATOR
            )
    with factory() as db:
        admission = service.admit(db, actor, run.id, _request())
        assert admission.state == "reserved"
        contract = service.claim_execution(db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR)
        assert service.finish(
            db,
            admission.id,
            QAExecutionAdmissionState.COMPLETED,
            execution_attempt_id=contract.execution_attempt_id,
            caller=InternalExecutionCaller.QA_EXECUTION_COORDINATOR,
        )
        with pytest.raises(ExecutionClaimRejected, match="admission_terminal"):
            service.claim_execution(db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR)


def test_claim_persists_trusted_contract_and_does_not_increment_capacity(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    service = _service()
    with factory() as db:
        admission = service.admit(db, actor, run.id, _request("api-auth", requested_timeout_seconds=120))
        before = db.get(QAExecutionCapacity, 1).active_count
        contract = service.claim_execution(
            db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR
        )
        assert contract.admission_id == admission.id
        assert contract.qa_run_id == run.id
        assert contract.suite_id == "api-auth"
        assert contract.runner_service.value == "api-tests"
        assert contract.selector_id == "api-auth-contract-v1"
        assert contract.effective_timeout_seconds == 120
        assert contract.environment_identity == "nexus-qa"
        assert "command" not in contract.model_dump()
        assert "selectors" not in contract.model_dump()
        with pytest.raises(ValidationError):
            TrustedRunnerContract.model_validate(
                {**contract.model_dump(mode="python"), "command": "pytest tests/evil.py"}
            )
        claimed = db.get(QAExecutionAdmission, admission.id)
        assert claimed.state == "running"
        assert claimed.execution_attempt_id == contract.execution_attempt_id
        assert claimed.worker_id == InternalExecutionCaller.QA_EXECUTION_COORDINATOR.value
        assert claimed.started_at is not None
        assert db.get(QAExecutionCapacity, 1).active_count == before == 1


def test_expired_claim_transitions_and_releases_capacity_once(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    now = datetime(2026, 11, 25, 12, 0, 0)
    clock_value = [now]
    service = _service(clock=lambda: clock_value[0])
    with factory() as db:
        admission = service.admit(db, actor, run.id, _request())
    clock_value[0] += timedelta(minutes=5, seconds=1)
    with factory() as db:
        with pytest.raises(ExecutionClaimRejected, match="reservation_expired"):
            service.claim_execution(
                db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR
            )
    with factory() as db:
        expired = db.get(QAExecutionAdmission, admission.id)
        assert expired.state == "expired"
        assert expired.execution_attempt_id is None
        assert expired.worker_id is None
        assert db.get(QAExecutionCapacity, 1).active_count == 0


def test_claim_rejects_suite_disabled_after_reservation(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    normal = _service()
    with factory() as db:
        admission = normal.admit(db, actor, run.id, _request("api-auth"))
    disabled = SuiteRegistry([
        DEFAULT_SUITE_REGISTRY.get("api-auth").model_copy(update={"enabled": False})
    ])
    with factory() as db:
        with pytest.raises(ExecutionClaimRejected, match="suite_disabled"):
            _service(registry=disabled).claim_execution(
                db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR
            )
        assert db.get(QAExecutionCapacity, 1).active_count == 1


def test_claim_rejects_unsafe_environment_after_reservation(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    with factory() as db:
        admission = _service().admit(db, actor, run.id, _request("api-auth"))
    with factory() as db:
        with pytest.raises(ExecutionClaimRejected, match="unsafe_environment"):
            QAAdmissionService(environment_factory=lambda _runner: None).claim_execution(
                db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR
            )
        assert db.get(QAExecutionCapacity, 1).active_count == 1


def test_claim_rejects_timeout_that_no_longer_meets_policy(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    normal = _service()
    with factory() as db:
        admission = normal.admit(db, actor, run.id, _request("api-auth", requested_timeout_seconds=600))
    short_api_suite = DEFAULT_SUITE_REGISTRY.get("api-auth").model_copy(
        update={"timeout_limit_seconds": 300}
    )
    shortened_registry = SuiteRegistry([short_api_suite])
    with factory() as db:
        with pytest.raises(ExecutionClaimRejected, match="timeout_exceeded"):
            _service(registry=shortened_registry).claim_execution(
                db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR
            )
        assert db.get(QAExecutionCapacity, 1).active_count == 1


def test_claim_rejects_invalidated_stored_evidence(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    service = _service()
    with factory() as db:
        admission = service.admit(db, actor, run.id, _request("api-auth"))
    with factory.begin() as db:
        stored = db.get(QARun, run.id)
        stored.github_head_sha = "c" * 40
    with factory() as db:
        with pytest.raises(ExecutionClaimRejected, match="evidence_invalid"):
            service.claim_execution(db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR)
        stored = db.get(QAExecutionAdmission, admission.id)
        assert stored.state == "reserved"
        assert stored.execution_attempt_id is None
        assert db.get(QAExecutionCapacity, 1).active_count == 1


def test_claim_commit_failure_rolls_back_attempt_and_state(admission_db):
    _engine, factory = admission_db
    actor, run, _plan = _seed_run(factory)
    with factory() as db:
        admission = _service().admit(db, actor, run.id, _request())
    db = factory()

    def fail_commit(_session):
        raise RuntimeError("synthetic claim commit failure")

    event.listen(db, "before_commit", fail_commit, once=True)
    try:
        with pytest.raises(RuntimeError, match="synthetic claim commit failure"):
            _service().claim_execution(
                db, admission.id, InternalExecutionCaller.QA_EXECUTION_COORDINATOR
            )
    finally:
        db.close()
    with factory() as db:
        stored = db.get(QAExecutionAdmission, admission.id)
        assert stored.state == "reserved"
        assert stored.execution_attempt_id is None
        assert stored.worker_id is None
        assert stored.started_at is None
        assert db.get(QAExecutionCapacity, 1).active_count == 1
