from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import SessionLocal, get_db
from app.models.identity import User
from app.models.qa import QARun
from app.schemas.qa import (
    CreateQAExecutionAdmissionRequest,
    CreateQARunRequest,
    QAExecutionAdmissionResponse,
    QARunError,
    QARunListResponse,
    QARunResponse,
    QARunResult,
    QARunState,
    QARunStatus,
    QARunConfig,
    QATriggerType,
)
from app.schemas.github import GitHubPRContext
from app.services.qa_orchestrator import (
    InvalidQARunTransition,
    QARunCannotBeCancelled,
    QARunNotFound,
    QARunOrchestrator,
)
from app.services.qa_admissions import AdmissionRunNotFound, QAAdmissionService

router = APIRouter(prefix="/v1/qa/runs", tags=["agentic QA"])


def get_qa_orchestrator() -> QARunOrchestrator:
    return QARunOrchestrator(SessionLocal)


def get_qa_admission_service() -> QAAdmissionService:
    return QAAdmissionService()


def serialize_run(run: QARun) -> QARunResponse:
    return QARunResponse(
        id=run.id,
        requested_by_user_id=run.requested_by_user_id,
        trigger_type=QATriggerType(run.trigger_type),
        status=QARunStatus(run.status),
        progress=run.progress,
        config=QARunConfig.model_validate(run.input_config),
        state=QARunState.model_validate(run.state_data) if run.state_data else None,
        result=QARunResult.model_validate(run.result) if run.result else None,
        error=QARunError.model_validate(run.error) if run.error else None,
        cancel_requested=run.cancel_requested,
        github_context=GitHubPRContext.model_validate(run.github_context) if run.github_context else None,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
    )


@router.post("", response_model=QARunResponse, status_code=status.HTTP_202_ACCEPTED)
def create_run(
    payload: CreateQARunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("qa", "execute")),
    orchestrator: QARunOrchestrator = Depends(get_qa_orchestrator),
) -> QARunResponse:
    run = orchestrator.create(db, payload, actor)
    background_tasks.add_task(orchestrator.execute, run.id)
    return serialize_run(run)


@router.post(
    "/{run_id}/admissions",
    response_model=QAExecutionAdmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"model": QAExecutionAdmissionResponse}},
)
def admit_suite(
    run_id: UUID,
    payload: CreateQAExecutionAdmissionRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("qa", "execute")),
    admission_service: QAAdmissionService = Depends(get_qa_admission_service),
):
    """Authorize and reserve a suggested suite; this endpoint never launches it."""

    try:
        admission = admission_service.admit(db, actor, run_id, payload)
    except AdmissionRunNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QA run not found") from None

    response = QAExecutionAdmissionResponse(
        admission_id=admission.id,
        qa_run_id=admission.qa_run_id,
        suite_id=admission.suite_id,
        state=admission.state,
        reason_code=admission.reason_code,
        timeout_seconds=admission.timeout_seconds,
        created_at=admission.created_at,
        reserved_until=admission.reserved_until,
    )
    return JSONResponse(
        status_code=(status.HTTP_409_CONFLICT if admission.state == "denied" else status.HTTP_202_ACCEPTED),
        content=response.model_dump(mode="json"),
    )


@router.get("", response_model=QARunListResponse)
def list_runs(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    run_status: QARunStatus | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("qa", "read")),
    orchestrator: QARunOrchestrator = Depends(get_qa_orchestrator),
) -> QARunListResponse:
    runs, total = orchestrator.list(db, skip=skip, limit=limit, status=run_status)
    return QARunListResponse(items=[serialize_run(run) for run in runs], total=total, skip=skip, limit=limit)


@router.get("/{run_id}", response_model=QARunResponse)
def get_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("qa", "read")),
    orchestrator: QARunOrchestrator = Depends(get_qa_orchestrator),
) -> QARunResponse:
    try:
        return serialize_run(orchestrator.get(db, run_id))
    except QARunNotFound:
        raise HTTPException(status_code=404, detail="QA run not found")


@router.post("/{run_id}/cancel", response_model=QARunResponse)
def cancel_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("qa", "execute")),
    orchestrator: QARunOrchestrator = Depends(get_qa_orchestrator),
) -> QARunResponse:
    try:
        return serialize_run(orchestrator.request_cancel(db, run_id))
    except QARunNotFound:
        raise HTTPException(status_code=404, detail="QA run not found")
    except QARunCannotBeCancelled:
        raise HTTPException(status_code=409, detail="QA run is already complete and cannot be cancelled")
    except InvalidQARunTransition:
        raise HTTPException(status_code=409, detail="QA run cannot be cancelled in its current state")
