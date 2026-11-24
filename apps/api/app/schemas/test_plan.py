"""Strict contracts for suggested test plans.

Proposal text is untrusted display-only data. No field represents executable
content; downstream consumers must never execute or interpret proposal text as
code, commands, or scripts.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.change_impact import ChangeImpactAnalysis, ImpactSeverity, TestPriority
from app.schemas.github import GitHubPRContext
from app.services.change_impact import ChangeImpactAnalyzer

MAX_TEST_PLANNING_INPUT_BYTES = 1_000_000
MAX_TEST_PLAN_PROPOSAL_BYTES = 256_000

_BoundedComponentId = Annotated[str, Field(min_length=1, max_length=100)]
_BoundedReason = Annotated[str, Field(min_length=1, max_length=500)]
_BoundedPath = Annotated[str, Field(min_length=1, max_length=4096)]
_PRIORITY_RANK = {TestPriority.LOW: 1, TestPriority.MEDIUM: 2, TestPriority.HIGH: 3}


def _as_tuple(value):
    return tuple(value) if isinstance(value, list) else value


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TestPlanningRevision(_StrictContract):
    repository_id: int = Field(gt=0)
    pull_request_number: int = Field(gt=0)
    revision_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class PlanningChangedFile(_StrictContract):
    path: str = Field(min_length=1, max_length=4096)
    previous_path: str | None = Field(default=None, max_length=4096)
    status: Literal["added", "modified", "removed", "renamed", "copied", "changed", "unchanged"]
    severity: ImpactSeverity
    components: tuple[_BoundedComponentId, ...] = Field(max_length=20)

    @field_validator("components", mode="before")
    @classmethod
    def make_components_immutable(cls, value):
        return _as_tuple(value)

    @field_validator("severity", mode="before")
    @classmethod
    def parse_severity(cls, value):
        if isinstance(value, str):
            try:
                return ImpactSeverity(value)
            except ValueError:
                return value
        return value


class PlanningComponent(_StrictContract):
    component_id: str = Field(min_length=1, max_length=100)
    severity: ImpactSeverity
    reasons: tuple[_BoundedReason, ...] = Field(max_length=20)

    @field_validator("reasons", mode="before")
    @classmethod
    def make_reasons_immutable(cls, value):
        return _as_tuple(value)

    @field_validator("severity", mode="before")
    @classmethod
    def parse_severity(cls, value):
        if isinstance(value, str):
            try:
                return ImpactSeverity(value)
            except ValueError:
                return value
        return value


class PlanningCandidateSuite(_StrictContract):
    suite_id: str = Field(min_length=1, max_length=256)
    priority: TestPriority
    reason: str = Field(min_length=1, max_length=500)
    impacted_components: tuple[_BoundedComponentId, ...] = Field(max_length=20)

    @field_validator("impacted_components", mode="before")
    @classmethod
    def make_components_immutable(cls, value):
        return _as_tuple(value)

    @field_validator("priority", mode="before")
    @classmethod
    def parse_priority(cls, value):
        if isinstance(value, str):
            try:
                return TestPriority(value)
            except ValueError:
                return value
        return value


class PlanningCoverageGap(_StrictContract):
    reference_id: str = Field(pattern=r"^gap_[0-9a-f]{64}$")
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    components: tuple[_BoundedComponentId, ...] = Field(max_length=20)
    paths: tuple[_BoundedPath, ...] = Field(max_length=3000)

    @field_validator("components", "paths", mode="before")
    @classmethod
    def make_collections_immutable(cls, value):
        return _as_tuple(value)


class TestPlanningInput(_StrictContract):
    """Bounded, data-only input assembled from trusted run and impact data."""

    schema_version: Literal["1.0"] = "1.0"
    revision: TestPlanningRevision
    impact_analysis_version: str = Field(min_length=1, max_length=20)
    changed_files: tuple[PlanningChangedFile, ...] = Field(max_length=3000)
    impacted_components: tuple[PlanningComponent, ...] = Field(max_length=128)
    candidate_suites: tuple[PlanningCandidateSuite, ...] = Field(max_length=128)
    coverage_gaps: tuple[PlanningCoverageGap, ...] = Field(max_length=3000)

    @field_validator("changed_files", "impacted_components", "candidate_suites", "coverage_gaps", mode="before")
    @classmethod
    def make_collections_immutable(cls, value):
        return _as_tuple(value)


TestKindValue = Literal["ui", "api", "integration", "regression", "security"]
SourceReferenceKind = Literal["changed_path", "component", "candidate_suite", "coverage_gap"]


class TestPlanSourceReference(_StrictContract):
    kind: SourceReferenceKind
    reference: str = Field(min_length=1, max_length=4096)


class DeclarativeTestCase(_StrictContract):
    intent_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$", max_length=64)
    title: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    test_kind: TestKindValue
    priority: TestPriority
    rationale: str = Field(min_length=1, max_length=500)
    source_references: list[TestPlanSourceReference] = Field(min_length=1, max_length=8)

    @field_validator("priority", mode="before")
    @classmethod
    def parse_priority(cls, value):
        if isinstance(value, str):
            try:
                return TestPriority(value)
            except ValueError:
                return value
        return value

class ProposedTestCase(DeclarativeTestCase):
    """Untrusted provider proposal; intentionally contains no provenance or status."""


class TestPlanProposal(_StrictContract):
    cases: list[ProposedTestCase] = Field(max_length=20)
    no_cases_explanation: str | None = Field(default=None, max_length=500)
    omitted_suite_count: int = Field(default=0, ge=0, le=128)

    @model_validator(mode="after")
    def require_zero_case_explanation(self) -> TestPlanProposal:
        if not self.cases and not self.no_cases_explanation:
            raise ValueError("A zero-case proposal requires an explanation")
        if self.cases and self.no_cases_explanation is not None:
            raise ValueError("A non-empty proposal cannot include a zero-case explanation")
        if self.omitted_suite_count and len(self.cases) != 20:
            raise ValueError("Omitted suites can be reported only when the proposal reaches its case limit")
        return self


class PlannedTestCase(DeclarativeTestCase):
    case_id: str = Field(pattern=r"^tc_[0-9a-f]{64}$")
    source_references: tuple[TestPlanSourceReference, ...] = Field(min_length=1, max_length=8)

    @field_validator("source_references", mode="before")
    @classmethod
    def make_source_references_immutable(cls, value):
        return _as_tuple(value)


class TestPlan(_StrictContract):
    schema_version: Literal["1.0"] = "1.0"
    approval_status: Literal["suggested"] = "suggested"
    revision: TestPlanningRevision
    impact_analysis_version: str = Field(min_length=1, max_length=20)
    cases: tuple[PlannedTestCase, ...] = Field(max_length=20)
    no_cases_explanation: str | None = Field(default=None, max_length=500)
    omitted_suite_count: int = Field(default=0, ge=0, le=128)
    coverage_gaps: tuple[PlanningCoverageGap, ...] = Field(max_length=3000)

    @field_validator("cases", "coverage_gaps", mode="before")
    @classmethod
    def make_collections_immutable(cls, value):
        return _as_tuple(value)

    @model_validator(mode="after")
    def validate_case_ids_and_empty_plan(self) -> TestPlan:
        if not self.cases and not self.no_cases_explanation:
            raise ValueError("A zero-case plan requires an explanation")
        if self.cases and self.no_cases_explanation is not None:
            raise ValueError("A non-empty plan cannot include a zero-case explanation")
        if self.omitted_suite_count and len(self.cases) != 20:
            raise ValueError("Omitted suites can be reported only when the plan reaches its case limit")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Test plan case IDs must be unique")
        return self


def build_test_planning_input(
    context: GitHubPRContext,
    impact: ChangeImpactAnalysis,
) -> TestPlanningInput:
    """Validate the snapshot binding and produce a bounded provider input."""

    # Revalidate model instances to avoid trusting mutated Pydantic objects.
    try:
        context = GitHubPRContext.model_validate(context.model_dump(mode="python"))
        impact = ChangeImpactAnalysis.model_validate(impact.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("GitHub revision context or impact analysis is invalid") from None

    if (
        impact.repository_id != context.repository_id
        or impact.pull_request_number != context.pull_request_number
        or impact.base_sha != context.base_sha
        or impact.head_sha != context.head_sha
    ):
        raise ValueError("GitHub revision context does not match deterministic impact analysis")
    if impact.analysis_version != "1.0":
        raise ValueError("Unsupported deterministic impact-analysis version")
    recomputed = ChangeImpactAnalyzer().analyze(context)
    if _canonical_json(impact.model_dump(mode="json")) != _canonical_json(recomputed.model_dump(mode="json")):
        raise ValueError("Impact analysis does not match the stored GitHub changed-file snapshot")

    revision = TestPlanningRevision(
        repository_id=context.repository_id,
        pull_request_number=context.pull_request_number,
        revision_id=context.revision_id,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
    )
    gaps = [
        PlanningCoverageGap(
            reference_id="gap_" + hashlib.sha256(
                json.dumps(gap.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            code=gap.code,
            message=gap.message,
            components=gap.components,
            paths=gap.paths,
        )
        for gap in recomputed.coverage_gaps
    ]
    planning_input = TestPlanningInput(
        revision=revision,
        impact_analysis_version=recomputed.analysis_version,
        changed_files=[
            PlanningChangedFile(
                path=item.path,
                previous_path=item.previous_path,
                status=item.status,
                severity=item.severity,
                components=item.components,
            )
            for item in recomputed.changed_files
        ],
        impacted_components=[
            PlanningComponent(
                component_id=item.component_id,
                severity=item.severity,
                reasons=item.reasons,
            )
            for item in recomputed.impacted_components
        ],
        candidate_suites=[
            PlanningCandidateSuite(
                suite_id=item.suite_id,
                priority=item.priority,
                reason=item.reason,
                impacted_components=item.impacted_components,
            )
            for item in recomputed.candidate_test_suites
        ],
        coverage_gaps=gaps,
    )
    encoded_input = json.dumps(
        planning_input.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded_input) > MAX_TEST_PLANNING_INPUT_BYTES:
        raise ValueError("Test-planning evidence exceeds the permitted size")
    return planning_input


def bind_test_plan(
    context: GitHubPRContext,
    impact: ChangeImpactAnalysis,
    proposal: TestPlanProposal | dict[str, object],
) -> TestPlan:
    """Validate untrusted proposals and bind them to stored trusted inputs.

    The planning DTO sent to a provider is intentionally not accepted here as
    provenance. The binder rebuilds evidence from the persisted snapshot and
    deterministic impact analysis so provider-side mutation cannot forge it.
    """

    validated_input = build_test_planning_input(context, impact)
    proposal_data = proposal.model_dump(mode="json") if isinstance(proposal, TestPlanProposal) else proposal
    try:
        encoded_proposal = json.dumps(
            proposal_data, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Provider proposal is not valid bounded JSON") from None
    if len(encoded_proposal) > MAX_TEST_PLAN_PROPOSAL_BYTES:
        raise ValueError("Provider proposal exceeds the permitted size")
    validated_proposal = TestPlanProposal.model_validate(proposal_data)
    files_by_path = {
        path: item
        for item in validated_input.changed_files
        for path in (item.path, item.previous_path)
        if path is not None
    }
    components = {item.component_id: item for item in validated_input.impacted_components}
    suites = {item.suite_id: item for item in validated_input.candidate_suites}
    gaps = {item.reference_id: item for item in validated_input.coverage_gaps}
    allowed_references = {
        "changed_path": set(files_by_path),
        "component": set(components),
        "candidate_suite": set(suites),
        "coverage_gap": set(gaps),
    }

    planned_cases: list[PlannedTestCase] = []
    for proposed in validated_proposal.cases:
        refs = [(source.kind, source.reference) for source in proposed.source_references]
        if len(refs) != len(set(refs)):
            raise ValueError("Test case contains duplicate evidence references")
        for source in proposed.source_references:
            if source.reference not in allowed_references[source.kind]:
                raise ValueError("Test case contains a reference outside the supplied planning evidence")

        cited_paths = {ref for kind, ref in refs if kind == "changed_path"}
        if not cited_paths:
            raise ValueError("Each test case must cite at least one changed path as primary evidence")
        cited_files = [files_by_path[path] for path in sorted(cited_paths)]
        path_components = {component for item in cited_files for component in item.components}
        path_values = {
            path for item in cited_files for path in (item.path, item.previous_path) if path is not None
        }
        explicit_components = {ref for kind, ref in refs if kind == "component"}
        if not explicit_components.issubset(path_components):
            raise ValueError("Test case references a component unrelated to its changed paths")
        scoped_components = explicit_components or path_components

        cited_suites = {ref for kind, ref in refs if kind == "candidate_suite"}
        for suite_id in cited_suites:
            if not scoped_components.intersection(suites[suite_id].impacted_components):
                raise ValueError("Test case references a candidate suite unrelated to its changed paths")
        cited_gaps = {ref for kind, ref in refs if kind == "coverage_gap"}
        for gap_id in cited_gaps:
            gap = gaps[gap_id]
            if not (scoped_components.intersection(gap.components) or path_values.intersection(gap.paths)):
                raise ValueError("Test case references a coverage gap unrelated to its changed paths")

        # Unknown severity and gap-only evidence are conservative LOW. Suite
        # priority can support a higher proposal only when its association above
        # has been established.
        supported_priorities = [_priority_for_severity(item.severity) for item in cited_files]
        supported_priorities.extend(suites[suite_id].priority for suite_id in cited_suites)
        priority_ceiling = max(supported_priorities, key=lambda value: _PRIORITY_RANK[value], default=TestPriority.LOW)
        if _PRIORITY_RANK[proposed.priority] > _PRIORITY_RANK[priority_ceiling]:
            raise ValueError("Proposed priority exceeds the deterministic priority supported by its evidence")

        canonical_refs = sorted(set(refs), key=lambda item: (item[0], item[1]))
        canonical = {
            "intent_key": proposed.intent_key,
            "source_references": [{"kind": kind, "reference": reference} for kind, reference in canonical_refs],
        }
        case_id = "tc_" + hashlib.sha256(
            _canonical_json(canonical).encode("utf-8")
        ).hexdigest()
        planned_cases.append(PlannedTestCase(case_id=case_id, **proposed.model_dump(mode="python")))

    # Coverage gaps come only from deterministic analysis; proposals have no
    # field with which to resolve, suppress, or rewrite them.
    return TestPlan(
        revision=validated_input.revision,
        impact_analysis_version=validated_input.impact_analysis_version,
        cases=sorted(planned_cases, key=lambda case: case.case_id),
        no_cases_explanation=validated_proposal.no_cases_explanation,
        omitted_suite_count=validated_proposal.omitted_suite_count,
        coverage_gaps=validated_input.coverage_gaps,
    )


def _priority_for_severity(severity: ImpactSeverity) -> TestPriority:
    if severity in {ImpactSeverity.HIGH, ImpactSeverity.CRITICAL}:
        return TestPriority.HIGH
    if severity == ImpactSeverity.MEDIUM:
        return TestPriority.MEDIUM
    # LOW and UNKNOWN both map to the analyzer's conservative low priority.
    return TestPriority.LOW


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
