import asyncio
import hashlib
import json
from typing import Protocol, runtime_checkable

from app.schemas.change_impact import TestPriority
from app.schemas.qa import FoundationContext, FoundationPlan
from app.schemas.test_plan import (
    MAX_TEST_PLANNING_INPUT_BYTES,
    PlanningCandidateSuite,
    ProposedTestCase,
    TestPlanProposal,
    TestPlanSourceReference,
    TestPlanningInput,
)


class AIProvider(Protocol):
    """Text-only planning interface; providers never receive command access."""

    async def create_foundation_plan(self, context: FoundationContext) -> FoundationPlan:
        ...


@runtime_checkable
class TestPlanningProvider(Protocol):
    """Async interface for proposing declarative, non-executable test cases.

    Implementations must cooperate with cancellation by propagating
    ``asyncio.CancelledError`` and must not detach or shield provider work. The
    orchestrator applies its deadline with ``asyncio.wait_for``; a provider that
    suppresses cancellation can delay timeout completion until it exits. Plan
    text is untrusted display data and must never be executed.
    """

    async def create_test_plan_proposal(self, planning_input: TestPlanningInput) -> TestPlanProposal:
        ...


class MockAIProvider:
    """Deterministic provider that works offline and makes no external calls."""

    async def create_foundation_plan(self, context: FoundationContext) -> FoundationPlan:
        if context.github_pull_request is not None:
            return FoundationPlan(
                summary="Pull request metadata was captured; no code assessment was performed.",
                next_step="Code assessment and test execution are outside this milestone.",
            )
        return FoundationPlan(
            summary="Mock provider is ready; no repository or pull request was analyzed.",
            next_step="Add controlled pull request context collection in a later milestone.",
        )


class MockTestPlanningProvider:
    """Offline planner that proposes cases only for supplied maintained suites."""

    async def create_test_plan_proposal(self, planning_input: TestPlanningInput) -> TestPlanProposal:
        # Yield before validation and between suites so cancellation is observed
        # during the bounded, pure in-memory planning pass.
        await asyncio.sleep(0)
        validated_input = TestPlanningInput.model_validate(planning_input.model_dump(mode="python"))
        encoded_input = json.dumps(
            validated_input.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded_input) > MAX_TEST_PLANNING_INPUT_BYTES:
            raise ValueError("Test-planning evidence exceeds the permitted size")

        impacted_component_ids = {item.component_id for item in validated_input.impacted_components}
        path_components: dict[str, set[str]] = {}
        for changed_file in validated_input.changed_files:
            for path in {changed_file.path, changed_file.previous_path} - {None}:
                associated = set(changed_file.components) & impacted_component_ids
                if path in path_components:
                    path_components[path].intersection_update(associated)
                else:
                    path_components[path] = associated

        suites = sorted(validated_input.candidate_suites, key=lambda item: item.suite_id)
        suite_ids = [suite.suite_id for suite in suites]
        if len(suite_ids) != len(set(suite_ids)):
            raise ValueError("Planning input contains duplicate candidate suite IDs")

        eligible_suites: list[tuple[PlanningCandidateSuite, list[tuple[str, str]]]] = []
        for suite in suites:
            await asyncio.sleep(0)
            suite_components = set(suite.impacted_components) & impacted_component_ids
            associated_pairs = sorted({
                (path, component)
                for path, components in path_components.items()
                for component in components & suite_components
            })
            if associated_pairs:
                eligible_suites.append((suite, associated_pairs))

        priority_order = {
            TestPriority.HIGH: 0,
            TestPriority.MEDIUM: 1,
            TestPriority.LOW: 2,
        }
        eligible_suites.sort(key=lambda item: (priority_order[item[0].priority], item[0].suite_id))
        omitted_suite_count = max(0, len(eligible_suites) - 20)

        cases: list[ProposedTestCase] = []
        for suite, associated_pairs in eligible_suites[:20]:
            suite_id = suite.suite_id

            # Three evidence pairs plus the suite reference stay within the
            # contract's eight-reference bound while allowing related files.
            selected_pairs = associated_pairs[:3]
            selected_components = sorted({component for _path, component in selected_pairs})
            references = [
                TestPlanSourceReference(kind="changed_path", reference=path)
                for path in sorted({path for path, _component in selected_pairs})
            ]
            references.extend(
                TestPlanSourceReference(kind="component", reference=component)
                for component in selected_components
            )
            references.append(TestPlanSourceReference(kind="candidate_suite", reference=suite_id))
            intent_suffix = hashlib.sha256(suite_id.encode("utf-8")).hexdigest()[:32]
            display_component = selected_components[0]
            cases.append(ProposedTestCase(
                intent_key=f"suite_{intent_suffix}",
                title=f"Review {display_component} behavior",
                objective="Assess changed behavior associated with this maintained test suite.",
                test_kind="regression",
                priority=suite.priority,
                rationale="Deterministic impact evidence associates the cited changed path and component with this maintained suite.",
                source_references=references,
            ))

        if not cases:
            explanation = "No supplied changed path is associated with a maintained candidate suite."
            if validated_input.coverage_gaps:
                explanation += " Supplied deterministic coverage gaps remain unresolved."
            else:
                explanation += " No unsupported case was proposed."
            return TestPlanProposal(
                cases=[],
                no_cases_explanation=explanation,
            )
        return TestPlanProposal(cases=cases, omitted_suite_count=omitted_suite_count)
