import asyncio
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "qa-foundation-test-signing-key-2026-abcdef")

from app.models.qa import QARun  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.schemas.qa import CreateQARunRequest, FoundationContext, FoundationPlan, QARunError, QARunState, QARunStatus  # noqa: E402
from app.services.qa_orchestrator import QARunCannotBeCancelled, QARunOrchestrator  # noqa: E402
from app.services.qa_workflow import ContextStage, FoundationWorkflow  # noqa: E402


@pytest.fixture
def qa_store(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'qa-runs.sqlite'}", connect_args={"check_same_thread": False})
    QARun.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def actor():
    user = User(email="qa-actor@example.com", full_name="QA Actor", password_hash="unused", is_active=True)
    user.id = uuid4()
    return user


def create_run(factory, orchestrator, *, timeout=5):
    db = factory()
    try:
        return orchestrator.create(db, CreateQARunRequest(config={"timeout_seconds": timeout}), actor())
    finally:
        db.close()


def load_run(factory, run_id):
    db = factory()
    try:
        run = db.get(QARun, run_id)
        db.expunge(run)
        return run
    finally:
        db.close()


def test_successful_run_persists_progress_state_result_and_duration(qa_store):
    orchestrator = QARunOrchestrator(qa_store)
    created = create_run(qa_store, orchestrator)
    assert created.status == "pending"
    orchestrator.execute(created.id)

    persisted = load_run(qa_store, created.id)
    assert persisted.status == QARunStatus.COMPLETED.value
    assert persisted.progress == 100
    assert persisted.started_at is not None and persisted.completed_at is not None
    assert persisted.duration_ms is not None and persisted.duration_ms >= 0
    assert persisted.result["mode"] == "foundation_mock"
    assert persisted.result["test_execution"]["status"] == "not_run"
    assert persisted.result["assessment"] == "not_assessed"
    assert len(persisted.result["stages"]) == 3
    assert QARunStatus(persisted.state_data["status"]) == QARunStatus.COMPLETED


def test_multiple_runs_are_isolated_and_terminal_run_cannot_be_cancelled(qa_store):
    orchestrator = QARunOrchestrator(qa_store)
    first = create_run(qa_store, orchestrator)
    second = create_run(qa_store, orchestrator)
    orchestrator.execute(first.id)

    assert load_run(qa_store, first.id).status == "completed"
    assert load_run(qa_store, second.id).status == "pending"
    db = qa_store()
    try:
        with pytest.raises(QARunCannotBeCancelled):
            orchestrator.request_cancel(db, first.id)
    finally:
        db.close()


def test_pending_run_can_be_cancelled_and_does_not_execute(qa_store):
    orchestrator = QARunOrchestrator(qa_store)
    run = create_run(qa_store, orchestrator)
    db = qa_store()
    try:
        cancelled = orchestrator.request_cancel(db, run.id)
        assert cancelled.status == "cancelled"
    finally:
        db.close()

    orchestrator.execute(run.id)
    persisted = load_run(qa_store, run.id)
    assert persisted.status == "cancelled"
    assert persisted.error["code"] == "cancelled"
    assert persisted.result is None


class SlowContextStage(ContextStage):
    def __init__(self, entered: threading.Event, delay: float = 0.15):
        self.entered = entered
        self.delay = delay

    async def execute(self, state, provider):
        self.entered.set()
        await asyncio.sleep(self.delay)
        return await super().execute(state, provider)


def test_active_run_cancellation_is_observed_between_stages(qa_store):
    entered = threading.Event()
    workflow = FoundationWorkflow(stages=(SlowContextStage(entered),))
    orchestrator = QARunOrchestrator(qa_store, workflow=workflow)
    run = create_run(qa_store, orchestrator)
    worker = threading.Thread(target=orchestrator.execute, args=(run.id,))
    worker.start()
    assert entered.wait(timeout=2)

    db = qa_store()
    try:
        cancelling = orchestrator.request_cancel(db, run.id)
        assert cancelling.cancel_requested is True
    finally:
        db.close()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert load_run(qa_store, run.id).status == "cancelled"


class CancelImmediatelyBeforeFinalizationWorkflow:
    def __init__(self, factory, run_id):
        self.workflow = FoundationWorkflow()
        self.factory = factory
        self.run_id = run_id

    async def execute(self, state, provider, on_progress):
        result = await self.workflow.execute(state, provider, on_progress)
        # FoundationWorkflow has completed its final progress callback. Request
        # cancellation before returning control to execute() for _finish().
        db = self.factory()
        try:
            orchestrator = QARunOrchestrator(self.factory)
            orchestrator.request_cancel(db, self.run_id)
        finally:
            db.close()
        return result


def test_cancel_after_final_progress_wins_over_successful_finalization(qa_store):
    run = create_run(qa_store, QARunOrchestrator(qa_store))
    workflow = CancelImmediatelyBeforeFinalizationWorkflow(qa_store, run.id)
    orchestrator = QARunOrchestrator(qa_store, workflow=workflow)

    orchestrator.execute(run.id)

    persisted = load_run(qa_store, run.id)
    assert persisted.status == QARunStatus.CANCELLED.value
    assert persisted.cancel_requested is True
    assert persisted.error == {
        "code": "cancelled",
        "message": "Run cancelled by request.",
    }
    assert persisted.result is None
    assert persisted.state_data["status"] == QARunStatus.CANCELLED.value
    assert persisted.state_data["cancellation_requested"] is True


def test_terminal_run_is_not_overwritten_by_late_finalization(qa_store):
    orchestrator = QARunOrchestrator(qa_store)
    run = create_run(qa_store, orchestrator)
    orchestrator.execute(run.id)
    completed = load_run(qa_store, run.id)

    failed_state = QARunState.model_validate(completed.state_data)
    failed_state.status = QARunStatus.FAILED
    orchestrator._finish(
        run.id,
        failed_state,
        error=QARunError(code="workflow_failed", message="Must not replace completed run."),
    )

    persisted = load_run(qa_store, run.id)
    assert persisted.status == QARunStatus.COMPLETED.value
    assert persisted.result == completed.result
    assert persisted.error is None


class SlowProvider:
    async def create_foundation_plan(self, _context: FoundationContext) -> FoundationPlan:
        await asyncio.sleep(0.2)
        return FoundationPlan(summary="unused", next_step="unused")


def test_run_timeout_is_persisted_as_timed_out(qa_store):
    orchestrator = QARunOrchestrator(qa_store, provider=SlowProvider())
    run = create_run(qa_store, orchestrator, timeout=1)
    # Use a deterministic monotonic clock so the timeout test stays fast.
    clock_values = iter((0.0, 2.0, 2.0))
    orchestrator.monotonic_clock = lambda: next(clock_values, 2.0)
    orchestrator.execute(run.id)
    persisted = load_run(qa_store, run.id)
    assert persisted.status == "timed_out"
    assert persisted.error["code"] == "timed_out"
    assert persisted.error["message"] == "The QA foundation workflow exceeded its time limit."


class FailingProvider:
    async def create_foundation_plan(self, _context: FoundationContext) -> FoundationPlan:
        raise RuntimeError("provider details must not be exposed")


def test_provider_failure_marks_run_failed_without_exposing_exception(qa_store):
    orchestrator = QARunOrchestrator(qa_store, provider=FailingProvider())
    run = create_run(qa_store, orchestrator)
    orchestrator.execute(run.id)
    persisted = load_run(qa_store, run.id)
    assert persisted.status == "failed"
    assert persisted.error == {
        "code": "workflow_failed",
        "message": "The QA foundation workflow could not be completed.",
    }
    assert "provider details" not in str(persisted.error)


class BrokenStage:
    name = "broken"
    status = QARunStatus.ANALYZING
    progress = 25

    async def execute(self, _state, _provider):
        raise RuntimeError("broken stage")


def test_stage_failure_cannot_leave_run_active(qa_store):
    orchestrator = QARunOrchestrator(qa_store, workflow=FoundationWorkflow(stages=(BrokenStage(),)))
    run = create_run(qa_store, orchestrator)
    orchestrator.execute(run.id)
    assert load_run(qa_store, run.id).status == "failed"


def test_initialization_failure_is_persisted_as_failed(qa_store):
    orchestrator = QARunOrchestrator(qa_store)
    run = create_run(qa_store, orchestrator)
    db = qa_store()
    try:
        persisted = db.get(QARun, run.id)
        persisted.state_data = {"invalid": "state"}
        db.commit()
    finally:
        db.close()

    orchestrator.execute(run.id)
    failed = load_run(qa_store, run.id)
    assert failed.status == "failed"
    assert failed.error["message"] == "The QA foundation workflow could not be initialized."


def test_stale_active_runs_are_marked_timed_out_when_read(qa_store):
    orchestrator = QARunOrchestrator(qa_store)
    run = create_run(qa_store, orchestrator, timeout=1)
    db = qa_store()
    try:
        persisted = db.get(QARun, run.id)
        persisted.status = QARunStatus.PLANNING.value
        persisted.started_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)
        persisted.updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)
        state = persisted.state_data
        state["status"] = QARunStatus.PLANNING.value
        persisted.state_data = state
        db.commit()
    finally:
        db.close()

    db = qa_store()
    try:
        recovered = orchestrator.get(db, run.id)
        assert recovered.status == QARunStatus.TIMED_OUT.value
        assert recovered.error["code"] == "timed_out"
        assert recovered.completed_at is not None
    finally:
        db.close()
