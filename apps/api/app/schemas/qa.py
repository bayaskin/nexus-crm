from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.change_impact import ChangeImpactAnalysis
from app.schemas.github import GitHubPRContext


class QARunStatus(StrEnum):
    PENDING = "pending"
    INITIALIZING = "initializing"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    RUNNING_CHECKS = "running_checks"
    GENERATING_REPORT = "generating_report"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SUPERSEDED = "superseded"


class QATriggerType(StrEnum):
    MANUAL = "manual"
    GITHUB_PULL_REQUEST = "github_pull_request"


class QARunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: int = Field(default=30, ge=1, le=300)


class TestExecutionStatus(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    BROKEN = "broken"
    SKIPPED = "skipped"


class TestExecutionResult(BaseModel):
    """Generic execution summary that future reporters can populate without Allure coupling."""

    status: TestExecutionStatus = TestExecutionStatus.NOT_RUN
    total: int = Field(default=0, ge=0)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    broken: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    evidence_reference: str | None = None
    summary: str = "No tests were executed in the foundation workflow."


class CreateQARunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_type: Literal[QATriggerType.MANUAL] = QATriggerType.MANUAL
    config: QARunConfig = Field(default_factory=QARunConfig)


class FoundationContext(BaseModel):
    kind: Literal["foundation_context"] = "foundation_context"
    note: str = "No repository or pull request was provided for this foundation run."
    repository_analysis_performed: bool = False
    github_pull_request: GitHubPRContext | None = None


class FoundationPlan(BaseModel):
    kind: Literal["foundation_plan"] = "foundation_plan"
    provider: str = "mock"
    summary: str
    next_step: str
    analysis_performed: bool = False


class QARunStage(BaseModel):
    name: str
    summary: str


class QARunState(BaseModel):
    run_id: UUID
    trigger_type: QATriggerType
    status: QARunStatus
    progress: int = Field(ge=0, le=100)
    config: QARunConfig
    context: FoundationContext | None = None
    plan: FoundationPlan | None = None
    stages: list[QARunStage] = Field(default_factory=list)
    cancellation_requested: bool = False
    github_context: GitHubPRContext | None = None
    change_impact_analysis: ChangeImpactAnalysis | None = None


class QARunResult(BaseModel):
    mode: Literal["foundation_mock"] = "foundation_mock"
    summary: str
    assessment: Literal["not_assessed"] = "not_assessed"
    context: FoundationContext
    plan: FoundationPlan
    stages: list[QARunStage]
    change_impact_analysis: ChangeImpactAnalysis | None = None
    test_execution: TestExecutionResult = Field(default_factory=TestExecutionResult)


class QARunError(BaseModel):
    code: Literal["workflow_failed", "cancelled", "timed_out", "superseded"]
    message: str


class QARunResponse(BaseModel):
    id: UUID
    requested_by_user_id: UUID | None
    trigger_type: QATriggerType
    status: QARunStatus
    progress: int
    config: QARunConfig
    state: QARunState | None = None
    result: QARunResult | None = None
    error: QARunError | None = None
    cancel_requested: bool
    github_context: GitHubPRContext | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None


class QARunListResponse(BaseModel):
    items: list[QARunResponse]
    total: int
    skip: int
    limit: int
