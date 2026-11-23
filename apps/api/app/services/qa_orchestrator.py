import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from time import monotonic
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.security import utcnow_naive
from app.models.qa import QARun
from app.models.identity import User
from app.schemas.qa import (
    CreateQARunRequest,
    QARunError,
    QARunResult,
    QARunState,
    QARunStatus,
    QATriggerType,
)
from app.schemas.github import GitHubPRContext
from app.services.qa_providers import AIProvider, MockAIProvider
from app.services.qa_workflow import FoundationWorkflow, QAWorkflow


class InvalidQARunTransition(ValueError):
    pass


class QARunNotFound(LookupError):
    pass


class QARunCannotBeCancelled(ValueError):
    pass


class QARunCancelled(Exception):
    pass


TERMINAL_STATUSES = {
    QARunStatus.COMPLETED,
    QARunStatus.FAILED,
    QARunStatus.CANCELLED,
    QARunStatus.TIMED_OUT,
    QARunStatus.SUPERSEDED,
}
ACTIVE_STATUSES = {
    QARunStatus.PENDING,
    QARunStatus.INITIALIZING,
    QARunStatus.ANALYZING,
    QARunStatus.PLANNING,
    QARunStatus.RUNNING_CHECKS,
    QARunStatus.GENERATING_REPORT,
}

ALLOWED_TRANSITIONS = {
    QARunStatus.PENDING: {QARunStatus.INITIALIZING, QARunStatus.CANCELLED, QARunStatus.FAILED, QARunStatus.TIMED_OUT},
    QARunStatus.INITIALIZING: {QARunStatus.ANALYZING, QARunStatus.CANCELLED, QARunStatus.FAILED, QARunStatus.TIMED_OUT},
    QARunStatus.ANALYZING: {QARunStatus.PLANNING, QARunStatus.CANCELLED, QARunStatus.FAILED, QARunStatus.TIMED_OUT},
    QARunStatus.PLANNING: {QARunStatus.RUNNING_CHECKS, QARunStatus.GENERATING_REPORT, QARunStatus.CANCELLED, QARunStatus.FAILED, QARunStatus.TIMED_OUT},
    QARunStatus.RUNNING_CHECKS: {QARunStatus.GENERATING_REPORT, QARunStatus.CANCELLED, QARunStatus.FAILED, QARunStatus.TIMED_OUT},
    QARunStatus.GENERATING_REPORT: {QARunStatus.COMPLETED, QARunStatus.CANCELLED, QARunStatus.FAILED, QARunStatus.TIMED_OUT},
}


def transition_status(current: QARunStatus, target: QARunStatus) -> QARunStatus:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidQARunTransition(f"Cannot transition QA run from {current.value} to {target.value}")
    return target


class QARunOrchestrator:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        workflow: QAWorkflow | None = None,
        provider: AIProvider | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self.session_factory = session_factory
        self.workflow = workflow or FoundationWorkflow()
        self.provider = provider or MockAIProvider()
        self.clock = clock or utcnow_naive
        self.monotonic_clock = monotonic_clock

    def create(
        self,
        db: Session,
        request: CreateQARunRequest,
        actor: User | None,
        *,
        github_context: GitHubPRContext | None = None,
        trigger_type: QATriggerType | None = None,
        commit: bool = True,
    ) -> QARun:
        selected_trigger = trigger_type or request.trigger_type
        run = QARun(
            requested_by_user_id=actor.id if actor else None,
            trigger_type=selected_trigger.value,
            status=QARunStatus.PENDING.value,
            progress=0,
            timeout_seconds=request.config.timeout_seconds,
            input_config=request.config.model_dump(mode="json"),
            state_data=None,
            github_repository_id=github_context.repository_id if github_context else None,
            github_pr_number=github_context.pull_request_number if github_context else None,
            github_head_sha=github_context.head_sha if github_context else None,
            github_delivery_id=github_context.delivery_id if github_context else None,
            github_context=github_context.model_dump(mode="json") if github_context else None,
            github_revision_id=getattr(github_context, "revision_id", None),
        )
        db.add(run)
        db.flush()
        initial_state = QARunState(
            run_id=run.id,
            trigger_type=selected_trigger,
            status=QARunStatus.PENDING,
            progress=0,
            config=request.config,
            github_context=github_context,
        )
        run.state_data = initial_state.model_dump(mode="json")
        if commit:
            db.commit()
            db.refresh(run)
        return run

    def list(self, db: Session, *, skip: int, limit: int, status: QARunStatus | None = None) -> tuple[list[QARun], int]:
        self._expire_stale_runs(db)
        query = select(QARun)
        count_query = select(func.count(QARun.id))
        if status is not None:
            query = query.where(QARun.status == status.value)
            count_query = count_query.where(QARun.status == status.value)
        total = db.scalar(count_query) or 0
        runs = list(db.scalars(query.order_by(QARun.created_at.desc(), QARun.id).offset(skip).limit(limit)).all())
        return runs, total

    def get(self, db: Session, run_id: UUID) -> QARun:
        run = db.get(QARun, run_id)
        if run is None:
            raise QARunNotFound
        self._expire_stale_run(db, run)
        return run

    def request_cancel(self, db: Session, run_id: UUID) -> QARun:
        run = self.get(db, run_id)
        current = QARunStatus(run.status)
        if current in TERMINAL_STATUSES:
            raise QARunCannotBeCancelled

        state = QARunState.model_validate(run.state_data) if run.state_data else None
        if current == QARunStatus.PENDING:
            if state:
                state.status = QARunStatus.CANCELLED
                state.cancellation_requested = True
            values = {
                "status": QARunStatus.CANCELLED.value,
                "cancel_requested": True,
                "completed_at": self.clock(),
                "duration_ms": 0,
                "state_data": state.model_dump(mode="json") if state else None,
                "error": QARunError(code="cancelled", message="Run cancelled before the workflow started.").model_dump(mode="json"),
            }
        else:
            values = {"cancel_requested": True}

        eligible_statuses = (
            [QARunStatus.PENDING.value]
            if current == QARunStatus.PENDING
            else [status.value for status in ACTIVE_STATUSES if status != QARunStatus.PENDING]
        )
        updated = db.execute(
            update(QARun)
            .where(QARun.id == run_id, QARun.status.in_(eligible_statuses))
            .values(**values)
        )
        if updated.rowcount != 1:
            db.rollback()
            latest = db.get(QARun, run_id)
            if latest is None:
                raise QARunNotFound
            if QARunStatus(latest.status) in TERMINAL_STATUSES:
                raise QARunCannotBeCancelled
            raise InvalidQARunTransition("QA run changed state while cancellation was requested")
        db.commit()
        return db.get(QARun, run_id)

    def _expire_stale_runs(self, db: Session) -> None:
        runs = db.scalars(select(QARun).where(QARun.status.in_([status.value for status in ACTIVE_STATUSES]))).all()
        for run in runs:
            self._expire_stale_run(db, run)

    def _expire_stale_run(self, db: Session, run: QARun) -> None:
        current = QARunStatus(run.status)
        now = self.clock()
        if current not in ACTIVE_STATUSES or run.updated_at + timedelta(seconds=run.timeout_seconds) > now:
            return
        state = QARunState.model_validate(run.state_data) if run.state_data else None
        run.status = transition_status(current, QARunStatus.TIMED_OUT).value
        run.completed_at = now
        if run.started_at:
            run.duration_ms = max(0, int((now - run.started_at).total_seconds() * 1000))
        run.error = QARunError(
            code="timed_out",
            message="The QA run stopped making progress before it completed.",
        ).model_dump(mode="json")
        if state:
            state.status = QARunStatus.TIMED_OUT
            run.state_data = state.model_dump(mode="json")
        db.commit()
        db.refresh(run)

    def execute(self, run_id: UUID) -> None:
        db = self.session_factory()
        try:
            run = db.get(QARun, run_id)
            if run is None or QARunStatus(run.status) != QARunStatus.PENDING:
                return
            started_monotonic = self.monotonic_clock()
            deadline = started_monotonic + run.timeout_seconds
            state = QARunState.model_validate(run.state_data)
            state.status = transition_status(QARunStatus.PENDING, QARunStatus.INITIALIZING)
            state.progress = 5
            started_at = self.clock()
            claimed = db.execute(
                update(QARun)
                .where(QARun.id == run_id, QARun.status == QARunStatus.PENDING.value)
                .values(
                    status=state.status.value,
                    progress=state.progress,
                    started_at=started_at,
                    state_data=state.model_dump(mode="json"),
                )
            )
            if claimed.rowcount != 1:
                db.rollback()
                return
            db.commit()
        except Exception:
            db.rollback()
            try:
                failed_run = db.get(QARun, run_id)
                if failed_run is not None and QARunStatus(failed_run.status) not in TERMINAL_STATUSES:
                    current_status = QARunStatus(failed_run.status)
                    failed_run.status = transition_status(current_status, QARunStatus.FAILED).value
                    failed_run.error = QARunError(
                        code="workflow_failed",
                        message="The QA foundation workflow could not be initialized.",
                    ).model_dump(mode="json")
                    failed_run.completed_at = self.clock()
                    if failed_run.started_at:
                        failed_run.duration_ms = max(0, int((failed_run.completed_at - failed_run.started_at).total_seconds() * 1000))
                    db.commit()
            except Exception:
                db.rollback()
            return
        finally:
            db.close()

        def persist_progress(updated_state: QARunState) -> None:
            progress_db = self.session_factory()
            try:
                persisted = progress_db.get(QARun, run_id)
                if persisted is None:
                    raise QARunNotFound
                if persisted.cancel_requested or QARunStatus(persisted.status) in {QARunStatus.CANCELLED, QARunStatus.SUPERSEDED}:
                    raise QARunCancelled
                if self.monotonic_clock() >= deadline:
                    raise TimeoutError

                current = QARunStatus(persisted.status)
                if updated_state.status != current:
                    updated_state.status = transition_status(current, updated_state.status)
                    persisted.status = updated_state.status.value
                persisted.progress = max(persisted.progress, updated_state.progress)
                persisted.state_data = updated_state.model_dump(mode="json")
                progress_db.commit()
            except Exception:
                progress_db.rollback()
                raise
            finally:
                progress_db.close()

        async def run_workflow() -> QARunResult:
            remaining = max(0.001, deadline - self.monotonic_clock())
            return await asyncio.wait_for(
                self.workflow.execute(state, self.provider, persist_progress),
                timeout=remaining,
            )

        try:
            result = asyncio.run(run_workflow())
            state.status = transition_status(state.status, QARunStatus.COMPLETED)
            state.progress = 100
            self._finish(run_id, state, result=result)
        except QARunCancelled:
            state.status = QARunStatus.CANCELLED
            state.cancellation_requested = True
            self._finish(run_id, state, error=QARunError(code="cancelled", message="Run cancelled by request."))
        except (asyncio.TimeoutError, TimeoutError):
            state.status = QARunStatus.TIMED_OUT
            self._finish(run_id, state, error=QARunError(code="timed_out", message="The QA foundation workflow exceeded its time limit."))
        except Exception:
            state.status = QARunStatus.FAILED
            self._finish(run_id, state, error=QARunError(code="workflow_failed", message="The QA foundation workflow could not be completed."))

    def _finish(
        self,
        run_id: UUID,
        state: QARunState,
        *,
        result: QARunResult | None = None,
        error: QARunError | None = None,
    ) -> None:
        db = self.session_factory()
        try:
            # Finalize with a conditional UPDATE so cancellation and completion
            # serialize on the persisted row. A terminal run can never be overwritten.
            run = db.get(QARun, run_id)
            if run is None or QARunStatus(run.status) in TERMINAL_STATUSES:
                return

            final_status = state.status
            final_state = state.model_copy(deep=True)
            final_result = result.model_dump(mode="json") if result else None
            final_error = error.model_dump(mode="json") if error else None
            now = self.clock()

            # Even if this executor completed successfully, a cancellation that
            # committed first takes precedence over its in-memory state.
            if run.cancel_requested:
                final_status = QARunStatus.CANCELLED
                final_state.status = final_status
                final_state.cancellation_requested = True
                final_error = QARunError(code="cancelled", message="Run cancelled by request.").model_dump(mode="json")
                final_result = None

            final_state.progress = 100 if final_status == QARunStatus.COMPLETED else run.progress
            values = {
                "status": final_status.value,
                "progress": final_state.progress,
                "state_data": final_state.model_dump(mode="json"),
                "result": final_result,
                "error": final_error,
                "completed_at": now,
                "duration_ms": max(0, int((now - run.started_at).total_seconds() * 1000)) if run.started_at else None,
            }
            conditions = [
                QARun.id == run_id,
                QARun.status.in_([status.value for status in ACTIVE_STATUSES]),
            ]
            if final_status == QARunStatus.COMPLETED:
                conditions.append(QARun.cancel_requested.is_(False))
            elif final_status == QARunStatus.CANCELLED:
                conditions.append(QARun.cancel_requested.is_(True))

            finalized = db.execute(update(QARun).where(*conditions).values(**values))
            if finalized.rowcount == 1:
                db.commit()
                return

            db.rollback()
            # Completion may have lost the race to a committed cancellation.
            # Reload and finalize that outcome with its own conditional update.
            run = db.get(QARun, run_id)
            if run is None or QARunStatus(run.status) in TERMINAL_STATUSES or not run.cancel_requested:
                return
            cancelled_state = QARunState.model_validate(run.state_data)
            cancelled_state.status = QARunStatus.CANCELLED
            cancelled_state.cancellation_requested = True
            cancelled_error = QARunError(code="cancelled", message="Run cancelled by request.").model_dump(mode="json")
            cancelled_at = self.clock()
            db.execute(
                update(QARun)
                .where(
                    QARun.id == run_id,
                    QARun.status.in_([status.value for status in ACTIVE_STATUSES]),
                    QARun.cancel_requested.is_(True),
                )
                .values(
                    status=QARunStatus.CANCELLED.value,
                    progress=run.progress,
                    state_data=cancelled_state.model_dump(mode="json"),
                    result=None,
                    error=cancelled_error,
                    completed_at=cancelled_at,
                    duration_ms=max(0, int((cancelled_at - run.started_at).total_seconds() * 1000)) if run.started_at else None,
                )
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
