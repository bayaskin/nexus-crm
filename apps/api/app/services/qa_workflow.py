from collections.abc import Callable
from typing import Protocol

from app.schemas.qa import (
    FoundationContext,
    QARunResult,
    QARunStage,
    QARunState,
    QARunStatus,
    QATriggerType,
)
from app.services.qa_providers import AIProvider, MockTestPlanningProvider, TestPlanningProvider
from app.services.change_impact import ChangeImpactAnalyzer
from app.schemas.test_plan import bind_test_plan, build_test_planning_input


class ProgressCallback(Protocol):
    def __call__(self, state: QARunState) -> None:
        ...


class WorkflowStage(Protocol):
    name: str
    status: QARunStatus
    progress: int

    async def execute(self, state: QARunState, provider: AIProvider) -> QARunStage:
        ...


class ContextStage:
    name = "foundation_context"
    status = QARunStatus.ANALYZING
    progress = 25

    async def execute(self, state: QARunState, _provider: AIProvider) -> QARunStage:
        if state.github_context:
            state.context = FoundationContext(
                note="Captured immutable GitHub pull request metadata and its changed-file snapshot.",
                github_pull_request=state.github_context,
            )
            return QARunStage(name=self.name, summary="Captured the immutable pull request revision and changed-file snapshot.")
        state.context = FoundationContext()
        return QARunStage(name=self.name, summary="Confirmed this run has no repository or pull request input.")


class ChangeImpactAnalysisStage:
    name = "change_impact_analysis"
    status = QARunStatus.ANALYZING
    progress = 40
    requires_github_context = True

    def __init__(self, analyzer: ChangeImpactAnalyzer | None = None) -> None:
        self.analyzer = analyzer or ChangeImpactAnalyzer()

    async def execute(self, state: QARunState, _provider: AIProvider) -> QARunStage:
        if state.github_context is None:
            raise RuntimeError("GitHub context is missing for change-impact analysis")
        state.change_impact_analysis = self.analyzer.analyze(state.github_context)
        return QARunStage(
            name=self.name,
            summary=(
                f"Analyzed {state.change_impact_analysis.summary.changed_file_count} changed files "
                f"and selected {state.change_impact_analysis.summary.candidate_suite_count} candidate test suites; no tests were run."
            ),
        )


class TestPlanningStage:
    name = "test_planning"
    status = QARunStatus.PLANNING
    progress = 50
    requires_github_context = True
    github_only = True

    def __init__(self, provider: TestPlanningProvider | None = None) -> None:
        self.provider = provider or MockTestPlanningProvider()

    async def execute(self, state: QARunState, _provider: AIProvider) -> QARunStage:
        state.test_plan = None
        if state.trigger_type != QATriggerType.GITHUB_PULL_REQUEST or state.github_context is None:
            raise RuntimeError("GitHub context is missing for test planning")
        if state.change_impact_analysis is None:
            raise RuntimeError("Deterministic change-impact analysis is missing for test planning")

        planning_input = build_test_planning_input(state.github_context, state.change_impact_analysis)
        proposal = await self.provider.create_test_plan_proposal(planning_input)
        bound_plan = bind_test_plan(state.github_context, state.change_impact_analysis, proposal)
        state.test_plan = bound_plan
        return QARunStage(
            name=self.name,
            summary=(
                f"Produced {len(bound_plan.cases)} suggested test cases; "
                f"{bound_plan.omitted_suite_count} eligible suites omitted by the case limit. No tests were run."
            ),
        )


class PlanningStage:
    name = "foundation_plan"
    status = QARunStatus.PLANNING
    progress = 60

    async def execute(self, state: QARunState, provider: AIProvider) -> QARunStage:
        if state.context is None:
            raise RuntimeError("Foundation context is missing")
        state.plan = await provider.create_foundation_plan(state.context)
        return QARunStage(name=self.name, summary="Produced a structured mock foundation plan.")


class ReportStage:
    name = "foundation_report"
    status = QARunStatus.GENERATING_REPORT
    progress = 90

    async def execute(self, state: QARunState, _provider: AIProvider) -> QARunStage:
        return QARunStage(name=self.name, summary="Prepared the foundation-only result; no tests were executed.")


class QAWorkflow(Protocol):
    async def execute(
        self,
        state: QARunState,
        provider: AIProvider,
        on_progress: ProgressCallback,
    ) -> QARunResult:
        ...


class FoundationWorkflow:
    """Minimal offline workflow with replaceable, ordered stages."""

    def __init__(
        self,
        stages: tuple[WorkflowStage, ...] | None = None,
        *,
        test_planning_provider: TestPlanningProvider | None = None,
    ) -> None:
        self.stages = stages or (
            ContextStage(),
            ChangeImpactAnalysisStage(),
            TestPlanningStage(test_planning_provider),
            PlanningStage(),
            ReportStage(),
        )

    async def execute(
        self,
        state: QARunState,
        provider: AIProvider,
        on_progress: ProgressCallback,
    ) -> QARunResult:
        for stage in self.stages:
            if getattr(stage, "github_only", False) and state.trigger_type != QATriggerType.GITHUB_PULL_REQUEST:
                continue
            if getattr(stage, "requires_github_context", False) and state.github_context is None:
                continue
            state.status = stage.status
            state.progress = stage.progress
            on_progress(state)
            stage_result = await stage.execute(state, provider)
            state.stages.append(stage_result)
            on_progress(state)

        if state.context is None or state.plan is None:
            raise RuntimeError("Foundation workflow did not produce its required outputs")

        if state.github_context is None:
            summary = "Foundation workflow completed. No pull request analysis or test execution was performed."
        else:
            summary = "Deterministic pull request change-impact analysis and suggested test planning completed. No tests were executed."
        return QARunResult(
            summary=summary,
            context=state.context,
            plan=state.plan,
            stages=state.stages,
            change_impact_analysis=state.change_impact_analysis,
            test_plan=state.test_plan,
        )
