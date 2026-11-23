from collections.abc import Callable
from typing import Protocol

from app.schemas.qa import FoundationContext, QARunResult, QARunStage, QARunState, QARunStatus
from app.services.qa_providers import AIProvider
from app.services.change_impact import ChangeImpactAnalyzer


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

    def __init__(self, stages: tuple[WorkflowStage, ...] | None = None) -> None:
        self.stages = stages or (ContextStage(), ChangeImpactAnalysisStage(), PlanningStage(), ReportStage())

    async def execute(
        self,
        state: QARunState,
        provider: AIProvider,
        on_progress: ProgressCallback,
    ) -> QARunResult:
        for stage in self.stages:
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
            summary = "Deterministic pull request change-impact analysis completed. No tests were executed."
        return QARunResult(
            summary=summary,
            context=state.context,
            plan=state.plan,
            stages=state.stages,
            change_impact_analysis=state.change_impact_analysis,
        )
