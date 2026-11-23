from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ImpactCategory(StrEnum):
    API = "api"
    BACKEND = "backend"
    DATABASE = "database"
    FRONTEND = "frontend"
    INTEGRATION = "integration"
    INFRASTRUCTURE = "infrastructure"
    AUTOMATION = "automation"
    DOCUMENTATION = "documentation"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


class ImpactSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class TestPriority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


ChangeFileStatus = Literal["added", "modified", "removed", "renamed", "copied", "changed", "unchanged"]


class ImpactReason(BaseModel):
    rule_id: str
    explanation: str


class ChangedFileImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=4096)
    previous_path: str | None = Field(default=None, max_length=4096)
    status: ChangeFileStatus
    occurrences: int = Field(default=1, ge=1)
    safe_path: bool = True
    components: list[str] = Field(default_factory=list)
    categories: list[ImpactCategory] = Field(default_factory=list)
    severity: ImpactSeverity
    reasons: list[ImpactReason] = Field(default_factory=list)


class ImpactedComponent(BaseModel):
    component_id: str
    severity: ImpactSeverity
    categories: list[ImpactCategory]
    paths: list[str]
    rule_ids: list[str]
    reasons: list[str]


class ImpactCategorySummary(BaseModel):
    category: ImpactCategory
    severity: ImpactSeverity
    paths: list[str]
    rule_ids: list[str]


class CandidateTestSuite(BaseModel):
    suite_id: str
    priority: TestPriority
    reason: str
    impacted_components: list[str]
    selection_source: str


class UnmappedChange(BaseModel):
    path: str
    status: ChangeFileStatus
    reason: str


class CoverageGap(BaseModel):
    code: str
    message: str
    components: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)


class ChangeImpactSummary(BaseModel):
    text: str
    changed_file_count: int = Field(ge=0)
    impacted_component_count: int = Field(ge=0)
    impact_category_count: int = Field(ge=0)
    candidate_suite_count: int = Field(ge=0)
    unmapped_change_count: int = Field(ge=0)


class ChangeImpactAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_version: str = "1.0"
    repository_id: int = Field(gt=0)
    pull_request_number: int = Field(gt=0)
    base_sha: str
    head_sha: str
    summary: ChangeImpactSummary
    overall_severity: ImpactSeverity
    changed_files: list[ChangedFileImpact]
    impacted_components: list[ImpactedComponent]
    impact_categories: list[ImpactCategorySummary]
    candidate_test_suites: list[CandidateTestSuite]
    unmapped_changes: list[UnmappedChange]
    coverage_gaps: list[CoverageGap]
