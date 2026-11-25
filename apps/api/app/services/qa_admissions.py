"""Trusted, durable admission for future QA suite execution. No runner lives here."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import utcnow_naive
from app.models.identity import User
from app.models.qa import QARun
from app.models.github import GitHubPRRevision
from app.models.qa_execution import QAExecutionAdmission, QAExecutionCapacity
from app.schemas.qa import (
    CreateQAExecutionAdmissionRequest,
    QAExecutionAdmissionState,
    QARunResult,
    QARunState,
    QARunStatus,
    QATriggerType,
)
from app.schemas.change_impact import ChangeImpactAnalysis
from app.schemas.github import GitHubPRContext
from app.schemas.test_plan import ProposedTestCase, TestPlan, TestPlanProposal, bind_test_plan
from app.services.change_impact import ChangeImpactAnalyzer
from app.services.permissions import has_permission
from app.services.qa_suite_registry import (
    CANDIDATE_SUITE_EXECUTION_MAP,
    DEFAULT_SUITE_REGISTRY,
    ExecutionEnvironment,
    ExecutionPolicyConfig,
    SuiteRegistry,
    SuiteRunner,
    evaluate_execution_policy,
)


RESERVATION_TTL = timedelta(minutes=5)
ACTIVE_ADMISSION_STATES = ("reserved", "running")


class AdmissionRunNotFound(LookupError):
    pass


class CapacityConsistencyError(RuntimeError):
    pass


def trusted_qa_execution_environment(
    runner: SuiteRunner,
    *,
    app_env: str | None = None,
    database_url: str | None = None,
) -> ExecutionEnvironment | None:
    """Derive the isolated target from server configuration, never a request.

    The fixed network/project facts are version-controlled alongside the QA
    Compose file. The API's own DB URL is parsed only to confirm the process is
    running in the isolated QA deployment; credentials never enter the DTO.
    """

    selected_env = settings.app_env if app_env is None else app_env
    selected_url = settings.database_url if database_url is None else database_url
    try:
        parsed_url = make_url(selected_url)
    except (TypeError, ValueError):
        return None
    if (
        selected_env != "test"
        or parsed_url.drivername != "mysql+pymysql"
        or parsed_url.host != "qa-mysql"
        or parsed_url.port != 3306
        or parsed_url.database != "nexus_qa_test"
        or parsed_url.username != "nexus_qa_runner"
        or not parsed_url.password
    ):
        return None

    common = {
        "app_env": "test",
        "qa_mode": True,
        "compose_project": "nexus-qa",
        "network_internal": True,
        "development_database_access": False,
        "development_service_access": False,
    }
    if runner == SuiteRunner.BACKEND_UNIT:
        data = {**common, "network_profile": "none"}
    elif runner == SuiteRunner.API:
        data = {
            **common,
            "network_profile": "qa_app_data",
            "database_host": "qa-mysql",
            "database_name": "nexus_qa_test",
            "service_base_url": "http://api:8000",
        }
    else:
        data = {**common, "network_profile": "qa_app", "service_base_url": "http://proxy"}
    try:
        return ExecutionEnvironment.model_validate(data)
    except ValidationError:
        return None


def map_plan_evidence_to_suite_ids(
    context: GitHubPRContext,
    impact: ChangeImpactAnalysis,
    plan: TestPlan,
) -> tuple[str, ...]:
    """Rebind stored plan evidence, then map cited deterministic suite IDs.

    Provider-generated titles, objectives, rationale, paths, and other text are
    never interpreted as runner instructions. Only exact candidate-suite IDs
    present in the canonical deterministic impact analysis enter the static map.
    """

    verified_plan = _rebind_stored_plan(context, impact, plan)
    candidate_ids = {item.suite_id for item in impact.candidate_test_suites}
    suite_ids: set[str] = set()
    for case in verified_plan.cases:
        cited = {
            item.reference
            for item in case.source_references
            if item.kind == "candidate_suite" and item.reference in candidate_ids
        }
        for candidate_id in cited:
            suite_ids.update(CANDIDATE_SUITE_EXECUTION_MAP.get(candidate_id, ()))
    return tuple(sorted(suite_ids))


def _rebind_stored_plan(
    context: GitHubPRContext,
    impact: ChangeImpactAnalysis,
    plan: TestPlan,
) -> TestPlan:
    cases = [
        ProposedTestCase.model_validate(case.model_dump(mode="json", exclude={"case_id"}))
        for case in plan.cases
    ]
    proposal = TestPlanProposal(
        cases=cases,
        no_cases_explanation=plan.no_cases_explanation,
        omitted_suite_count=plan.omitted_suite_count,
    )
    rebound = bind_test_plan(context, impact, proposal)
    if rebound.model_dump(mode="json") != plan.model_dump(mode="json"):
        raise ValueError("Stored test plan does not match the trusted GitHub revision and impact evidence")
    return rebound


class QAAdmissionService:
    """Persist policy decisions and atomically reserve global QA capacity."""

    def __init__(
        self,
        *,
        registry: SuiteRegistry = DEFAULT_SUITE_REGISTRY,
        environment_factory: Callable[[SuiteRunner], ExecutionEnvironment | Mapping[str, Any] | None]
        = trusted_qa_execution_environment,
        clock: Callable[[], datetime] = utcnow_naive,
    ) -> None:
        self.registry = registry
        self.environment_factory = environment_factory
        self.clock = clock

    def admit(
        self,
        db: Session,
        actor: User,
        run_id: UUID,
        request: CreateQAExecutionAdmissionRequest,
    ) -> QAExecutionAdmission:
        """Create a denial record or a reserved admission in one transaction."""

        try:
            request = CreateQAExecutionAdmissionRequest.model_validate(request.model_dump(mode="python"))
        except (AttributeError, TypeError, ValueError, ValidationError):
            db.rollback()
            raise ValueError("Admission request is invalid") from None

        try:
            # A no-op update obtains the singleton row's write lock on MySQL and
            # SQLite before reading admission state; all admission mutations use
            # this same lock order.
            db.execute(
                update(QAExecutionCapacity)
                .where(QAExecutionCapacity.id == 1)
                .values(active_count=QAExecutionCapacity.active_count)
            )
            self._expire_reservations_in_transaction(db, self.clock())

            run = db.get(QARun, run_id)
            if run is None:
                raise AdmissionRunNotFound

            if (
                actor.id is None
                or not actor.is_active
                or actor.must_change_password
                or not has_permission(actor, "qa:execute")
            ):
                return self._deny(db, actor, run_id, request.suite_id, "unauthorized")

            evidence = self._load_validated_plan(db, run)
            if evidence is None:
                return self._deny(db, actor, run_id, request.suite_id, "plan_unavailable")
            context, impact, plan = evidence
            try:
                suggested_ids = map_plan_evidence_to_suite_ids(context, impact, plan)
            except (AttributeError, TypeError, ValueError, ValidationError):
                return self._deny(db, actor, run_id, request.suite_id, "evidence_invalid")

            definition = self.registry.get(request.suite_id)
            if definition is None:
                return self._deny(db, actor, run_id, request.suite_id, "unknown_suite")
            if not definition.enabled:
                return self._deny(db, actor, run_id, request.suite_id, "suite_disabled")
            if request.suite_id not in suggested_ids:
                return self._deny(db, actor, run_id, request.suite_id, "suite_not_suggested")

            environment = self.environment_factory(definition.runner_service)
            if environment is None:
                return self._deny(db, actor, run_id, request.suite_id, "unsafe_environment")

            existing = db.scalar(
                select(QAExecutionAdmission)
                .where(
                    QAExecutionAdmission.qa_run_id == run_id,
                    QAExecutionAdmission.suite_id == request.suite_id,
                    QAExecutionAdmission.state.in_(ACTIVE_ADMISSION_STATES),
                )
                .order_by(QAExecutionAdmission.created_at)
                .limit(1)
                .with_for_update()
            )
            if existing is not None:
                # Retries return the existing durable reservation and never
                # consume a second slot for the same run/suite pair.
                db.commit()
                return existing

            capacity = db.scalar(
                select(QAExecutionCapacity)
                .where(QAExecutionCapacity.id == 1)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if capacity is None:
                return self._deny(db, actor, run_id, request.suite_id, "policy_config_invalid")

            try:
                policy_config = ExecutionPolicyConfig(
                    max_concurrent_executions=capacity.max_concurrent_executions
                )
            except ValidationError:
                return self._deny(db, actor, run_id, request.suite_id, "policy_config_invalid")

            decision = evaluate_execution_policy(
                {"suite_ids": [request.suite_id], "requested_timeout_seconds": request.requested_timeout_seconds},
                environment,
                active_execution_count=capacity.active_count,
                registry=self.registry,
                config=policy_config,
            )
            if not decision.allowed:
                return self._deny(db, actor, run_id, request.suite_id, decision.reason_code.value)

            claimed = db.execute(
                update(QAExecutionCapacity)
                .where(
                    QAExecutionCapacity.id == 1,
                    QAExecutionCapacity.active_count < QAExecutionCapacity.max_concurrent_executions,
                )
                .values(active_count=QAExecutionCapacity.active_count + 1)
            )
            if claimed.rowcount != 1:
                return self._deny(db, actor, run_id, request.suite_id, "concurrency_limit")

            now = self.clock()
            admission = QAExecutionAdmission(
                suite_id=request.suite_id,
                requested_by_user_id=actor.id,
                qa_run_id=run.id,
                state=QAExecutionAdmissionState.RESERVED.value,
                timeout_seconds=decision.timeout_seconds,
                created_at=now,
                reserved_until=now + RESERVATION_TTL,
            )
            db.add(admission)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                if not self._is_active_admission_conflict(exc):
                    raise
                # The failed insert rolled back the counter increment too.
                # Reacquire locks in the global capacity-first order and use a
                # current read to resolve the winner after a concurrent commit.
                db.execute(
                    update(QAExecutionCapacity)
                    .where(QAExecutionCapacity.id == 1)
                    .values(active_count=QAExecutionCapacity.active_count)
                )
                existing = db.scalar(
                    select(QAExecutionAdmission)
                    .where(
                        QAExecutionAdmission.qa_run_id == run_id,
                        QAExecutionAdmission.suite_id == request.suite_id,
                        QAExecutionAdmission.state.in_(ACTIVE_ADMISSION_STATES),
                    )
                    .order_by(QAExecutionAdmission.created_at)
                    .limit(1)
                    .with_for_update()
                )
                if existing is None:
                    raise
                db.commit()
                return existing
            db.refresh(admission)
            return admission
        except Exception:
            db.rollback()
            raise

    def recover_expired_reservations(self, db: Session) -> int:
        """Release only never-started reservations whose durable TTL elapsed."""

        try:
            db.execute(
                update(QAExecutionCapacity)
                .where(QAExecutionCapacity.id == 1)
                .values(active_count=QAExecutionCapacity.active_count)
            )
            expired = self._expire_reservations_in_transaction(db, self.clock())
            db.commit()
            return expired
        except Exception:
            db.rollback()
            raise

    def mark_running(self, db: Session, admission_id: UUID) -> bool:
        """Phase 3B hook: running work stays counted until an explicit terminal update."""

        try:
            db.execute(
                update(QAExecutionCapacity)
                .where(QAExecutionCapacity.id == 1)
                .values(active_count=QAExecutionCapacity.active_count)
            )
            if db.get(QAExecutionCapacity, 1) is None:
                db.rollback()
                return False
            now = self.clock()
            result = db.execute(
                update(QAExecutionAdmission)
                .where(
                    QAExecutionAdmission.id == admission_id,
                    QAExecutionAdmission.state == QAExecutionAdmissionState.RESERVED.value,
                    QAExecutionAdmission.reserved_until > now,
                )
                .values(state=QAExecutionAdmissionState.RUNNING.value, started_at=now)
            )
            db.commit()
            return result.rowcount == 1
        except Exception:
            db.rollback()
            raise

    def finish(self, db: Session, admission_id: UUID, state: QAExecutionAdmissionState) -> bool:
        """Release capacity exactly once after a confirmed terminal runner state."""

        if state not in {
            QAExecutionAdmissionState.COMPLETED,
            QAExecutionAdmissionState.FAILED,
            QAExecutionAdmissionState.CANCELLED,
        }:
            raise ValueError("Admission terminal state is invalid")
        try:
            db.execute(
                update(QAExecutionCapacity)
                .where(QAExecutionCapacity.id == 1)
                .values(active_count=QAExecutionCapacity.active_count)
            )
            admission = db.scalar(
                select(QAExecutionAdmission)
                .where(QAExecutionAdmission.id == admission_id)
                .with_for_update()
            )
            if admission is None or admission.state not in ACTIVE_ADMISSION_STATES:
                db.rollback()
                return False
            result = db.execute(
                update(QAExecutionAdmission)
                .where(
                    QAExecutionAdmission.id == admission_id,
                    QAExecutionAdmission.state.in_(ACTIVE_ADMISSION_STATES),
                )
                .values(state=state.value, completed_at=self.clock())
            )
            if result.rowcount != 1:
                db.rollback()
                return False
            self._decrement_capacity(db, 1)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise

    def _load_validated_plan(
        self, db: Session, run: QARun
    ) -> tuple[GitHubPRContext, ChangeImpactAnalysis, TestPlan] | None:
        if run.status != QARunStatus.COMPLETED.value or run.trigger_type != QATriggerType.GITHUB_PULL_REQUEST.value:
            return None
        if not run.github_context or not run.state_data or not run.result:
            return None
        try:
            context = GitHubPRContext.model_validate(run.github_context)
            state = QARunState.model_validate(run.state_data)
            result = QARunResult.model_validate(run.result)
            revision = db.scalar(
                select(GitHubPRRevision)
                .where(GitHubPRRevision.id == run.github_revision_id)
                .execution_options(populate_existing=True)
            )
            if (
                revision is None
                or state.test_plan is None
                or result.test_plan is None
                or state.change_impact_analysis is None
                or result.change_impact_analysis is None
                or state.github_context is None
                or state.github_context.model_dump(mode="json") != context.model_dump(mode="json")
                or state.trigger_type.value != run.trigger_type
                or context.revision_id != run.github_revision_id
                or revision.id != context.revision_id
                or revision.repository_id != context.repository_id
                or revision.pull_request_number != context.pull_request_number
                or revision.base_sha != context.base_sha
                or revision.head_sha != context.head_sha
                or context.repository_id != run.github_repository_id
                or context.pull_request_number != run.github_pr_number
                or context.head_sha != run.github_head_sha
                or state.context is None
                or result.context.model_dump(mode="json") != state.context.model_dump(mode="json")
                or state.context.github_pull_request is None
                or state.context.github_pull_request.model_dump(mode="json")
                != context.model_dump(mode="json")
                or state.test_plan.model_dump(mode="json") != result.test_plan.model_dump(mode="json")
                or state.change_impact_analysis.model_dump(mode="json")
                != result.change_impact_analysis.model_dump(mode="json")
            ):
                return None
            impact = ChangeImpactAnalysis.model_validate(state.change_impact_analysis.model_dump(mode="python"))
            canonical_impact = ChangeImpactAnalyzer().analyze(context)
            if impact.model_dump(mode="json") != canonical_impact.model_dump(mode="json"):
                return None
            plan = TestPlan.model_validate(state.test_plan.model_dump(mode="python"))
            return context, impact, plan
        except (TypeError, ValueError, ValidationError):
            return None

    @staticmethod
    def _is_active_admission_conflict(error: IntegrityError) -> bool:
        """Recognize only the active-pair uniqueness backstop violation."""

        original = error.orig
        args = getattr(original, "args", ())
        if args and args[0] == 1062:
            return "uq_qa_execution_admissions_active_run_suite" in str(original)
        message = str(original)
        return (
            "UNIQUE constraint failed: qa_execution_admissions.qa_run_id, "
            "qa_execution_admissions.suite_id, qa_execution_admissions.active_slot"
        ) in message

    def _expire_reservations_in_transaction(self, db: Session, now: datetime) -> int:
        expired_result = db.execute(
            update(QAExecutionAdmission)
            .where(
                QAExecutionAdmission.state == QAExecutionAdmissionState.RESERVED.value,
                QAExecutionAdmission.reserved_until <= now,
            )
            .values(
                state=QAExecutionAdmissionState.EXPIRED.value,
                reason_code="reservation_expired",
                completed_at=now,
            )
        )
        count = expired_result.rowcount or 0
        if count:
            self._decrement_capacity(db, count)
        return count

    @staticmethod
    def _decrement_capacity(db: Session, count: int) -> None:
        result = db.execute(
            update(QAExecutionCapacity)
            .where(QAExecutionCapacity.id == 1, QAExecutionCapacity.active_count >= count)
            .values(active_count=QAExecutionCapacity.active_count - count)
        )
        if result.rowcount != 1:
            raise CapacityConsistencyError("QA execution capacity state is inconsistent")

    @staticmethod
    def _deny(
        db: Session,
        actor: User,
        run_id: UUID,
        suite_id: str,
        reason_code: str,
    ) -> QAExecutionAdmission:
        denial = QAExecutionAdmission(
            suite_id=suite_id,
            requested_by_user_id=actor.id,
            qa_run_id=run_id,
            state=QAExecutionAdmissionState.DENIED.value,
            reason_code=reason_code,
        )
        db.add(denial)
        db.commit()
        db.refresh(denial)
        return denial
