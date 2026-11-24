import asyncio
import inspect
from datetime import datetime

import pytest
from pydantic import ValidationError

import app.services.qa_providers as qa_providers
from app.schemas.change_impact import ImpactSeverity, TestPriority as Priority
from app.schemas.github import GitHubChangedFile as GitHubFile, GitHubPRContext as PRContext
from app.schemas.qa import FoundationContext as Context, FoundationPlan as FoundationResult
from app.schemas.test_plan import (
    PlanningCandidateSuite as CandidateSuite,
    PlanningChangedFile as ChangedFile,
    PlanningComponent as Component,
    PlanningCoverageGap as CoverageGap,
    TestPlan as BoundPlan,
    TestPlanProposal as PlanProposal,
    TestPlanningInput as PlanningInput,
    TestPlanningRevision as PlanningRevision,
    bind_test_plan,
    build_test_planning_input,
)
from app.services.change_impact import ChangeImpactAnalyzer
from app.services.qa_providers import (
    MockAIProvider,
    MockTestPlanningProvider,
    TestPlanningProvider as PlanningProvider,
)


def planning_input(
    *,
    paths: tuple[ChangedFile, ...] | None = None,
    suites: tuple[CandidateSuite, ...] | None = None,
) -> PlanningInput:
    changed_files = paths if paths is not None else (
        ChangedFile(
            path="apps/api/app/api/routes/customers.py",
            status="modified",
            severity=ImpactSeverity.MEDIUM,
            components=("customers",),
        ),
        ChangedFile(
            path="apps/api/app/api/routes/orders.py",
            status="modified",
            severity=ImpactSeverity.MEDIUM,
            components=("orders",),
        ),
    )
    candidate_suites = suites if suites is not None else (
        CandidateSuite(
            suite_id="apps/api/tests/test_orders.py",
            priority=Priority.MEDIUM,
            reason="Covers order behavior.",
            impacted_components=("orders",),
        ),
        CandidateSuite(
            suite_id="apps/api/tests/test_customers.py",
            priority=Priority.MEDIUM,
            reason="Covers customer behavior.",
            impacted_components=("customers",),
        ),
    )
    return PlanningInput(
        revision=PlanningRevision(
            repository_id=123,
            pull_request_number=7,
            revision_id="11111111-1111-4111-8111-111111111111",
            base_sha="a" * 40,
            head_sha="b" * 40,
        ),
        impact_analysis_version="1.0",
        changed_files=changed_files,
        impacted_components=(
            Component(component_id="customers", severity=ImpactSeverity.MEDIUM, reasons=("route changed",)),
            Component(component_id="orders", severity=ImpactSeverity.MEDIUM, reasons=("route changed",)),
        ),
        candidate_suites=candidate_suites,
        coverage_gaps=(
            CoverageGap(
                reference_id="gap_" + "c" * 64,
                code="no_candidate_suite_mapping",
                message="An unrelated component has no mapped suite.",
                components=("inventory",),
                paths=("apps/api/app/api/routes/inventory.py",),
            ),
        ),
    )


def test_mock_provider_implements_async_planning_protocol_and_preserves_foundation_provider():
    provider = MockTestPlanningProvider()

    assert isinstance(provider, PlanningProvider)
    assert inspect.iscoroutinefunction(provider.create_test_plan_proposal)
    # Existing FoundationPlan behavior remains independently available.
    foundation_method = MockAIProvider().create_foundation_plan
    assert inspect.iscoroutinefunction(foundation_method)
    assert isinstance(asyncio.run(foundation_method(Context())), FoundationResult)


def test_mock_provider_returns_valid_cases_from_associated_supplied_evidence():
    result = asyncio.run(MockTestPlanningProvider().create_test_plan_proposal(planning_input()))

    assert isinstance(result, PlanProposal)
    assert len(result.cases) == 2
    assert result.no_cases_explanation is None
    suite_references = [
        next(ref.reference for ref in case.source_references if ref.kind == "candidate_suite")
        for case in result.cases
    ]
    assert suite_references == sorted(suite_references)
    for case in result.cases:
        refs = {(ref.kind, ref.reference) for ref in case.source_references}
        assert any(kind == "changed_path" for kind, _ in refs)
        assert any(kind == "component" for kind, _ in refs)
        assert any(kind == "candidate_suite" for kind, _ in refs)
        assert all(ref.kind != "coverage_gap" for ref in case.source_references)
    PlanProposal.model_validate(result.model_dump(mode="python"))


def test_output_is_stable_when_input_collections_are_reordered():
    source = planning_input()
    reordered = source.model_copy(update={
        "changed_files": tuple(reversed(source.changed_files)),
        "impacted_components": tuple(reversed(source.impacted_components)),
        "candidate_suites": tuple(reversed(source.candidate_suites)),
        "coverage_gaps": tuple(reversed(source.coverage_gaps)),
    })
    provider = MockTestPlanningProvider()

    first = asyncio.run(provider.create_test_plan_proposal(source)).model_dump(mode="json")
    second = asyncio.run(provider.create_test_plan_proposal(reordered)).model_dump(mode="json")

    assert first == second


def test_mock_provider_limits_output_to_twenty_cases():
    files = tuple(
        ChangedFile(
            path=f"apps/api/app/api/routes/component_{number}.py",
            status="modified",
            severity=ImpactSeverity.MEDIUM,
            components=(f"component_{number}",),
        )
        for number in range(21)
    )
    suites = tuple(
        CandidateSuite(
            suite_id=(
                f"apps/api/tests/test_component_{number:02}.py"
                if number < 20
                else "zz/apps/api/tests/test_high_priority.py"
            ),
            priority=Priority.LOW if number < 20 else Priority.HIGH,
            reason="Selected deterministic suite.",
            impacted_components=(f"component_{number}",),
        )
        for number in range(21)
    )

    source = planning_input(paths=files, suites=suites).model_copy(update={
        "impacted_components": tuple(
            Component(
                component_id=f"component_{number}",
                severity=ImpactSeverity.MEDIUM,
                reasons=("route changed",),
            )
            for number in range(21)
        ),
    })
    result = asyncio.run(MockTestPlanningProvider().create_test_plan_proposal(source))

    assert len(result.cases) == 20
    assert result.omitted_suite_count == 1
    selected_suites = {
        ref.reference
        for case in result.cases
        for ref in case.source_references
        if ref.kind == "candidate_suite"
    }
    assert "zz/apps/api/tests/test_high_priority.py" in selected_suites
    assert "apps/api/tests/test_component_19.py" not in selected_suites
    with pytest.raises(ValidationError, match="reaches its case limit"):
        PlanProposal(cases=result.cases[:1], omitted_suite_count=1)


def test_mock_proposal_binds_to_trusted_impact_and_revision():
    context = PRContext(
        repository_id=123456,
        repository_full_name="example/project",
        repository_owner="example",
        repository_name="project",
        pull_request_number=18,
        title="A realistic pull request",
        url="https://github.com/example/project/pull/18",
        author="contributor",
        state="open",
        draft=False,
        source_branch="feature/security-review",
        target_branch="main",
        base_sha="a" * 40,
        head_sha="b" * 40,
        changed_files=[
            GitHubFile(
                path="apps/api/app/api/routes/auth.py",
                status="modified",
                additions=8,
                deletions=2,
                changes=10,
            ),
            GitHubFile(
                path="external/unmapped.txt",
                status="added",
                additions=1,
                deletions=0,
                changes=1,
            ),
        ],
        reported_changed_files=2,
        revision_id="22222222-2222-4222-8222-222222222222",
        event_type="synchronize",
        delivery_id="provider-binder-contract",
        event_updated_at=datetime(2026, 2, 12),
    )
    impact = ChangeImpactAnalyzer().analyze(context)
    planning = build_test_planning_input(context, impact)
    proposal = asyncio.run(MockTestPlanningProvider().create_test_plan_proposal(planning))

    plan = bind_test_plan(context, impact, proposal)

    assert isinstance(plan, BoundPlan)
    assert plan.approval_status == "suggested"
    assert plan.revision == planning.revision
    assert plan.coverage_gaps == planning.coverage_gaps
    assert plan.omitted_suite_count == proposal.omitted_suite_count
    assert plan.cases
    changed_paths = {
        path
        for item in planning.changed_files
        for path in (item.path, item.previous_path)
        if path is not None
    }
    component_ids = {item.component_id for item in planning.impacted_components}
    suites = {item.suite_id: item for item in planning.candidate_suites}
    for case in plan.cases:
        assert any(ref.kind == "changed_path" and ref.reference in changed_paths for ref in case.source_references)
        assert all(
            ref.reference in component_ids
            for ref in case.source_references
            if ref.kind == "component"
        )
        suite_refs = [ref.reference for ref in case.source_references if ref.kind == "candidate_suite"]
        assert len(suite_refs) == 1
        assert case.priority == suites[suite_refs[0]].priority


@pytest.mark.parametrize(
    "files",
    [
        (),
        (
            ChangedFile(
                path="unmapped/external.txt",
                status="added",
                severity=ImpactSeverity.UNKNOWN,
                components=("unknown",),
            ),
        ),
    ],
)
def test_empty_or_unmapped_evidence_returns_zero_cases_with_explanation(files):
    result = asyncio.run(MockTestPlanningProvider().create_test_plan_proposal(
        planning_input(paths=files)
    ))

    assert result.cases == []
    assert result.no_cases_explanation
    assert "unresolved" in result.no_cases_explanation


def test_suite_without_path_component_association_is_not_proposed():
    source = planning_input(
        paths=(ChangedFile(
            path="apps/api/app/api/routes/customers.py",
            status="modified",
            severity=ImpactSeverity.MEDIUM,
            components=("customers",),
        ),),
        suites=(CandidateSuite(
            suite_id="apps/api/tests/test_inventory.py",
            priority=Priority.HIGH,
            reason="This suite belongs to another component.",
            impacted_components=("inventory",),
        ),),
    )

    result = asyncio.run(MockTestPlanningProvider().create_test_plan_proposal(source))

    assert result.cases == []
    assert result.no_cases_explanation


def test_duplicate_candidate_suite_ids_are_rejected_as_ambiguous():
    suite = CandidateSuite(
        suite_id="apps/api/tests/test_customers.py",
        priority=Priority.MEDIUM,
        reason="Covers customer behavior.",
        impacted_components=("customers",),
    )
    source = planning_input(suites=(suite, suite))

    with pytest.raises(ValueError, match="duplicate candidate suite IDs"):
        asyncio.run(MockTestPlanningProvider().create_test_plan_proposal(source))


def test_provider_observes_cancellation_at_its_async_checkpoint(monkeypatch):
    async def scenario():
        entered_checkpoint = asyncio.Event()
        actual_sleep = asyncio.sleep
        checkpoint_count = 0

        async def wait_at_checkpoint(_delay):
            nonlocal checkpoint_count
            checkpoint_count += 1
            if checkpoint_count == 2:
                entered_checkpoint.set()
                await actual_sleep(3600)
            else:
                await actual_sleep(0)

        monkeypatch.setattr(qa_providers.asyncio, "sleep", wait_at_checkpoint)
        task = asyncio.create_task(MockTestPlanningProvider().create_test_plan_proposal(planning_input()))
        await entered_checkpoint.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
