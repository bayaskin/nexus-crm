import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.change_impact import ChangeImpactAnalysis as ImpactAnalysis
from app.schemas.github import GitHubChangedFile as GitHubFile, GitHubPRContext as PRContext
from app.schemas.qa import (
    FoundationContext as Context,
    FoundationPlan as FoundationResult,
    QARunConfig,
    QARunResult as RunResult,
    QARunStage,
    QARunState as RunState,
    QARunStatus,
    QATriggerType,
)
from app.schemas.test_plan import TestPlanProposal as PlanProposal
from app.services.change_impact import ChangeImpactAnalyzer
from app.services.qa_providers import MockAIProvider, MockTestPlanningProvider
from app.services.qa_workflow import FoundationWorkflow, TestPlanningStage as PlanningStageUnderTest


def github_context(*, head_sha: str = "b" * 40, path: str = "apps/api/app/api/routes/auth.py") -> PRContext:
    return PRContext(
        repository_id=123456,
        repository_full_name="example/project",
        repository_owner="example",
        repository_name="project",
        pull_request_number=9,
        title="Untrusted pull request title",
        url="https://github.com/example/project/pull/9",
        author="contributor",
        state="open",
        draft=False,
        source_branch="feature/qa-plan",
        target_branch="main",
        base_sha="a" * 40,
        head_sha=head_sha,
        changed_files=[
            GitHubFile(path=path, status="modified", additions=2, deletions=1, changes=3),
            GitHubFile(path="external/unmapped.txt", status="added", additions=1, deletions=0, changes=1),
        ],
        reported_changed_files=2,
        revision_id="33333333-3333-4333-8333-333333333333",
        event_type="synchronize",
        delivery_id="test-planning-stage",
        event_updated_at=datetime(2026, 2, 1),
    )


def state_for(context: PRContext | None = None) -> RunState:
    return RunState(
        run_id=uuid4(),
        trigger_type=QATriggerType.GITHUB_PULL_REQUEST if context else QATriggerType.MANUAL,
        status=QARunStatus.PLANNING,
        progress=50,
        config=QARunConfig(),
        github_context=context,
        change_impact_analysis=ChangeImpactAnalyzer().analyze(context) if context else None,
    )


class RecordingPlanner:
    def __init__(self, result=None, error: BaseException | None = None):
        self.result = result if result is not None else PlanProposal(cases=[], no_cases_explanation="No cases supplied.")
        self.error = error
        self.calls = 0

    async def create_test_plan_proposal(self, _planning_input):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_github_workflow_persists_bound_plan_in_state_and_result():
    state = state_for(github_context())
    progress = []

    result = asyncio.run(FoundationWorkflow().execute(state, MockAIProvider(), progress.append))

    assert state.test_plan is not None
    assert result.test_plan == state.test_plan
    assert state.test_plan.approval_status == "suggested"
    assert state.test_plan.revision.revision_id == state.github_context.revision_id
    assert state.test_plan.coverage_gaps
    assert state.test_plan.omitted_suite_count == 0
    assert [stage.name for stage in state.stages] == [
        "foundation_context",
        "change_impact_analysis",
        "test_planning",
        "foundation_plan",
        "foundation_report",
    ]


def test_manual_workflow_skips_planner_and_keeps_legacy_result_shape():
    planner = RecordingPlanner()
    state = state_for()

    result = asyncio.run(FoundationWorkflow(test_planning_provider=planner).execute(
        state, MockAIProvider(), lambda _state: None
    ))

    assert planner.calls == 0
    assert state.test_plan is None
    assert result.test_plan is None
    assert [stage.name for stage in state.stages] == [
        "foundation_context", "foundation_plan", "foundation_report"
    ]


def test_old_state_and_result_json_validate_without_test_plan():
    state = state_for().model_dump(mode="json")
    state.pop("test_plan")
    assert RunState.model_validate(state).test_plan is None

    result = RunResult(
        summary="Legacy result",
        context=Context(),
        plan=FoundationResult(summary="No assessment", next_step="Later"),
        stages=[QARunStage(name="foundation_report", summary="Legacy")],
    ).model_dump(mode="json")
    result.pop("test_plan")
    assert RunResult.model_validate(result).test_plan is None


@pytest.mark.parametrize("missing_impact", [True, False])
def test_missing_or_mismatched_impact_prevents_provider_invocation(missing_impact):
    source_context = github_context()
    state = state_for(source_context)
    if missing_impact:
        state.change_impact_analysis = None
    else:
        other_context = github_context(path="apps/api/app/api/routes/orders.py")
        state.change_impact_analysis = ChangeImpactAnalyzer().analyze(other_context)
    planner = RecordingPlanner()

    with pytest.raises((RuntimeError, ValueError)):
        asyncio.run(PlanningStageUnderTest(planner).execute(state, MockAIProvider()))

    assert planner.calls == 0
    assert state.test_plan is None


def test_forged_impact_with_matching_revision_metadata_prevents_provider_invocation():
    state = state_for(github_context())
    impact = state.change_impact_analysis
    altered_summary = impact.summary.model_copy(update={"changed_file_count": 99})
    state.change_impact_analysis = impact.model_copy(update={"summary": altered_summary})
    planner = RecordingPlanner()

    with pytest.raises(ValueError, match="does not match the stored GitHub changed-file snapshot"):
        asyncio.run(PlanningStageUnderTest(planner).execute(state, MockAIProvider()))

    assert state.change_impact_analysis.repository_id == state.github_context.repository_id
    assert state.change_impact_analysis.head_sha == state.github_context.head_sha
    assert planner.calls == 0
    assert state.test_plan is None


@pytest.mark.parametrize(
    "planner",
    [
        RecordingPlanner(result={"cases": [{"shell_command": "pytest"}]}),
        RecordingPlanner(error=RuntimeError("private provider detail")),
    ],
)
def test_invalid_proposal_or_provider_failure_cannot_set_a_plan(planner):
    state = state_for(github_context())
    stage = PlanningStageUnderTest(planner)
    with pytest.raises((ValidationError, RuntimeError)):
        asyncio.run(stage.execute(state, MockAIProvider()))
    assert state.test_plan is None


def test_provider_cancellation_and_timeout_leave_no_partial_plan():
    async def scenario():
        entered = asyncio.Event()

        class WaitingPlanner:
            async def create_test_plan_proposal(self, _planning_input):
                entered.set()
                await asyncio.Event().wait()

        cancelled_state = state_for(github_context())
        task = asyncio.create_task(PlanningStageUnderTest(WaitingPlanner()).execute(cancelled_state, MockAIProvider()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled_state.test_plan is None

        timed_out_state = state_for(github_context())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                PlanningStageUnderTest(WaitingPlanner()).execute(timed_out_state, MockAIProvider()),
                timeout=0.001,
            )
        assert timed_out_state.test_plan is None

    asyncio.run(scenario())


def test_provider_text_remains_declarative_and_is_not_sent_to_an_execution_stage():
    class TextPlanner:
        async def create_test_plan_proposal(self, planning_input):
            proposal = await MockTestPlanningProvider().create_test_plan_proposal(planning_input)
            case = proposal.cases[0].model_copy(update={
                "objective": "Display this untrusted text only: run `pytest` on the contributor's machine.",
            })
            return proposal.model_copy(update={"cases": [case]})

    class ContextOnlyAIProvider(MockAIProvider):
        async def create_foundation_plan(self, context):
            assert not hasattr(context, "test_plan")
            return await super().create_foundation_plan(context)

    state = state_for(github_context())
    result = asyncio.run(FoundationWorkflow(test_planning_provider=TextPlanner()).execute(
        state, ContextOnlyAIProvider(), lambda _state: None
    ))

    assert result.test_plan.cases[0].objective.endswith("contributor's machine.")
    assert result.test_execution.status.value == "not_run"
    assert all(stage.name != "test_execution" for stage in result.stages)
