from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas.change_impact import TestPriority as Priority
from app.schemas.github import GitHubChangedFile, GitHubPRContext
from app.schemas.test_plan import (
    MAX_TEST_PLAN_PROPOSAL_BYTES,
    ProposedTestCase,
    TestPlan as BoundPlan,
    TestPlanProposal as ProposalSet,
    TestPlanSourceReference as SourceReference,
    TestPlanningInput as PlanningInput,
    bind_test_plan,
    build_test_planning_input,
)
from app.services.change_impact import ChangeImpactAnalyzer


def context(
    *,
    base_sha: str = "a" * 40,
    head_sha: str = "b" * 40,
    revision_id: str = "11111111-1111-4111-8111-111111111111",
) -> GitHubPRContext:
    return GitHubPRContext(
        repository_id=123456,
        repository_full_name="example/project",
        repository_owner="example",
        repository_name="project",
        pull_request_number=18,
        title="Untrusted PR title is excluded from planner input",
        url="https://github.com/example/project/pull/18",
        author="untrusted-author",
        state="open",
        draft=False,
        source_branch="feature/test-plan",
        target_branch="main",
        base_sha=base_sha,
        head_sha=head_sha,
        changed_files=[
            GitHubChangedFile(
                path="apps/api/app/api/routes/customers.py",
                status="modified",
                additions=5,
                deletions=1,
                changes=6,
            ),
            GitHubChangedFile(
                path="external/unmapped.txt",
                status="added",
                additions=1,
                deletions=0,
                changes=1,
            ),
        ],
        reported_changed_files=2,
        revision_id=revision_id,
        event_type="synchronize",
        delivery_id="planning-contract-test",
        event_updated_at=datetime(2026, 1, 10),
    )


def planning_input(source_context: GitHubPRContext | None = None) -> PlanningInput:
    revision = source_context or context()
    impact = ChangeImpactAnalyzer().analyze(revision)
    return build_test_planning_input(revision, impact)


def trusted_inputs():
    source_context = context()
    impact = ChangeImpactAnalyzer().analyze(source_context)
    return source_context, impact, build_test_planning_input(source_context, impact)


def proposal(
    input_data: PlanningInput,
    *,
    priority: Priority | str = Priority.MEDIUM,
) -> ProposalSet:
    gap_ref = input_data.coverage_gaps[0].reference_id
    return ProposalSet(
        cases=[
            ProposedTestCase(
                intent_key="reject_invalid_customer_input",
                title="Reject invalid customer input",
                objective="Verify malformed customer data is rejected without changing stored data.",
                test_kind="api",
                priority=priority,
                rationale="The customer route is changed and is reported as an impacted component.",
                source_references=[
                    {"kind": "changed_path", "reference": "apps/api/app/api/routes/customers.py"},
                    {"kind": "component", "reference": "customers"},
                    {"kind": "coverage_gap", "reference": gap_ref},
                ],
            ),
        ],
    )


def test_valid_proposal_binds_to_trusted_revision_and_preserves_coverage_gaps():
    source_context, impact, input_data = trusted_inputs()
    plan = bind_test_plan(source_context, impact, proposal(input_data))

    assert plan.approval_status == "suggested"
    assert plan.revision.repository_id == source_context.repository_id
    assert plan.revision.pull_request_number == source_context.pull_request_number
    assert plan.revision.revision_id == source_context.revision_id
    assert plan.revision.base_sha == source_context.base_sha
    assert plan.revision.head_sha == source_context.head_sha
    assert plan.cases[0].case_id.startswith("tc_")
    assert plan.cases[0].priority == Priority.MEDIUM
    assert plan.coverage_gaps == input_data.coverage_gaps
    assert BoundPlan.model_validate(plan.model_dump(mode="json")) == plan
    repeated = bind_test_plan(source_context, impact, proposal(input_data))
    assert [case.case_id for case in repeated.cases] == [case.case_id for case in plan.cases]


def test_zero_case_plan_requires_bounded_explanation():
    source_context, impact, input_data = trusted_inputs()
    plan = bind_test_plan(
        source_context,
        impact,
        ProposalSet(cases=[], no_cases_explanation="No candidate case is justified by the supplied evidence."),
    )
    assert plan.cases == ()
    assert plan.no_cases_explanation

    with pytest.raises(ValidationError):
        ProposalSet(cases=[])


def test_duplicate_case_proposals_are_rejected_as_duplicate_stable_ids():
    source_context, impact, input_data = trusted_inputs()
    single = proposal(input_data).cases[0]
    with pytest.raises(ValidationError, match="case IDs must be unique"):
        bind_test_plan(source_context, impact, ProposalSet(cases=[single, single]))


def test_provider_cannot_reference_unsupplied_evidence():
    source_context, impact, input_data = trusted_inputs()
    invalid = proposal(input_data).model_copy(deep=True)
    invalid.cases[0].source_references[0] = SourceReference(
        kind="changed_path", reference="src/private.py"
    )
    with pytest.raises(ValueError, match="outside the supplied planning evidence"):
        bind_test_plan(source_context, impact, invalid)


def test_case_count_and_text_sizes_are_bounded():
    input_data = planning_input()
    one_case = proposal(input_data).cases[0]
    with pytest.raises(ValidationError):
        ProposalSet(cases=[one_case] * 21)
    with pytest.raises(ValidationError):
        ProposedTestCase(
            **{
                **one_case.model_dump(mode="python"),
                "title": "x" * 121,
            }
        )


def test_unknown_fields_and_attempted_executable_payloads_are_rejected():
    input_data = planning_input()
    with pytest.raises(ValidationError):
        ProposalSet(cases=[], no_cases_explanation="No case is supported.", approval_status="approved")
    with pytest.raises(ValidationError):
        ProposedTestCase(
            **{
                **proposal(input_data).cases[0].model_dump(mode="python"),
                "shell_command": "pytest -q",
            }
        )
    base = proposal(input_data).cases[0].model_dump(mode="python")
    assert ProposedTestCase(**{
        **base,
        "rationale": "Make sure customer input is handled correctly; a reviewer might run `pytest -q` later.",
    })
    for field in ("code", "script", "shell_command", "shell_arguments"):
        with pytest.raises(ValidationError):
            ProposedTestCase(**{**base, field: "pytest -q"})


def test_binder_validates_raw_provider_mappings():
    source_context, impact, input_data = trusted_inputs()
    raw_proposal = proposal(input_data).model_dump(mode="json")

    plan = bind_test_plan(source_context, impact, raw_proposal)

    assert plan.cases[0].priority == Priority.MEDIUM


def test_raw_provider_output_has_an_aggregate_size_limit():
    source_context, impact, _input_data = trusted_inputs()

    with pytest.raises(ValueError, match="exceeds the permitted size"):
        bind_test_plan(source_context, impact, {"untrusted": "x" * MAX_TEST_PLAN_PROPOSAL_BYTES})


def test_unsupported_priority_is_rejected():
    input_data = planning_input()
    with pytest.raises(ValidationError):
        proposal(input_data, priority="critical")


def test_priority_cannot_exceed_priority_supported_by_associated_evidence():
    source_context, impact, input_data = trusted_inputs()
    with pytest.raises(ValueError, match="exceeds the deterministic priority"):
        bind_test_plan(source_context, impact, proposal(input_data, priority=Priority.HIGH))


def test_unmapped_evidence_conservatively_supports_only_low_priority():
    source_context = context()
    source_context.changed_files = [source_context.changed_files[1]]
    source_context.reported_changed_files = 1
    impact = ChangeImpactAnalyzer().analyze(source_context)
    input_data = build_test_planning_input(source_context, impact)
    low = ProposedTestCase(
        intent_key="review_unmapped_change",
        title="Review unmapped change",
        objective="Assess the changed file using the available review process.",
        test_kind="regression",
        priority=Priority.LOW,
        rationale="No deterministic component severity is defined for this path.",
        source_references=[{"kind": "changed_path", "reference": "external/unmapped.txt"}],
    )
    assert bind_test_plan(source_context, impact, ProposalSet(cases=[low])).cases[0].priority == Priority.LOW
    elevated = low.model_copy(update={"priority": Priority.MEDIUM})
    with pytest.raises(ValueError, match="exceeds the deterministic priority"):
        bind_test_plan(source_context, impact, ProposalSet(cases=[elevated]))


def test_suite_and_gap_references_must_be_associated_with_cited_paths():
    source_context = context()
    source_context.changed_files.append(GitHubChangedFile(
        path="apps/api/app/api/routes/auth.py",
        status="modified",
        additions=1,
        deletions=0,
        changes=1,
    ))
    source_context.reported_changed_files = 3
    impact = ChangeImpactAnalyzer().analyze(source_context)
    input_data = build_test_planning_input(source_context, impact)
    customer = proposal(input_data).cases[0]
    unrelated_suite = SourceReference(
        kind="candidate_suite", reference="apps/api/tests/test_permission_resolution.py"
    )
    with pytest.raises(ValueError, match="candidate suite unrelated"):
        bind_test_plan(
            source_context,
            impact,
            ProposalSet(cases=[customer.model_copy(update={
                "source_references": [*customer.source_references, unrelated_suite]
            })]),
        )
    unrelated_gap = next(gap for gap in input_data.coverage_gaps if "external/unmapped.txt" in gap.paths)
    with pytest.raises(ValueError, match="coverage gap unrelated"):
        bind_test_plan(
            source_context,
            impact,
            ProposalSet(cases=[customer.model_copy(update={
                "source_references": [
                    ref for ref in customer.source_references if ref.kind != "coverage_gap"
                ] + [SourceReference(kind="coverage_gap", reference=unrelated_gap.reference_id)]
            })]),
        )


def test_case_can_reference_multiple_related_components_and_suite():
    source_context = context()
    source_context.changed_files = [
        GitHubChangedFile(path="apps/api/app/api/routes/auth.py", status="modified", additions=1, deletions=0, changes=1),
        GitHubChangedFile(path="apps/api/app/api/routes/users.py", status="modified", additions=1, deletions=0, changes=1),
    ]
    source_context.reported_changed_files = 2
    impact = ChangeImpactAnalyzer().analyze(source_context)
    case = ProposedTestCase(
        intent_key="verify_access_control",
        title="Verify access control",
        objective="Verify authentication and user permission behavior together.",
        test_kind="integration",
        priority=Priority.HIGH,
        rationale="Both related components changed in this revision.",
        source_references=[
            {"kind": "changed_path", "reference": "apps/api/app/api/routes/auth.py"},
            {"kind": "changed_path", "reference": "apps/api/app/api/routes/users.py"},
            {"kind": "component", "reference": "authentication"},
            {"kind": "component", "reference": "user_management"},
            {"kind": "candidate_suite", "reference": "apps/api/tests/test_permission_resolution.py"},
        ],
    )
    plan = bind_test_plan(source_context, impact, ProposalSet(cases=[case]))
    assert plan.cases[0].priority == Priority.HIGH


def test_case_ids_depend_on_intent_and_evidence_not_wording():
    source_context, impact, input_data = trusted_inputs()
    base = proposal(input_data).cases[0]
    wording_changed = base.model_copy(update={
        "title": "Check customer input",
        "objective": "Confirm malformed customer data is rejected.",
        "rationale": "Customer route evidence supports this check.",
    })
    first = bind_test_plan(source_context, impact, ProposalSet(cases=[base]))
    same_identity = bind_test_plan(source_context, impact, ProposalSet(cases=[wording_changed]))
    assert first.cases[0].case_id == same_identity.cases[0].case_id
    changed_evidence = base.model_copy(update={
        "source_references": [SourceReference(kind="changed_path", reference="apps/api/app/api/routes/customers.py")]
    })
    changed_identity = bind_test_plan(source_context, impact, ProposalSet(cases=[changed_evidence]))
    assert first.cases[0].case_id != changed_identity.cases[0].case_id


def test_duplicate_intent_and_evidence_is_rejected_even_if_wording_differs():
    source_context, impact, input_data = trusted_inputs()
    base = proposal(input_data).cases[0]
    variant = base.model_copy(update={"title": "Different phrasing"})
    with pytest.raises(ValidationError, match="case IDs must be unique"):
        bind_test_plan(source_context, impact, ProposalSet(cases=[base, variant]))


def test_revision_mismatch_is_rejected_before_planning_input_is_built():
    source_context = context()
    impact = ChangeImpactAnalyzer().analyze(source_context)
    mismatched_context = context(base_sha="c" * 40)

    with pytest.raises(ValueError, match="does not match"):
        build_test_planning_input(mismatched_context, impact)


def test_changed_file_snapshot_must_match_exact_analysis_evidence():
    source_context = context()
    impact = ChangeImpactAnalyzer().analyze(source_context)
    substituted_context = context()
    substituted_context.changed_files[0] = GitHubChangedFile(
        path="apps/api/app/api/routes/orders.py",
        status="modified",
        additions=5,
        deletions=1,
        changes=6,
    )
    with pytest.raises(ValueError, match="does not match the stored GitHub changed-file snapshot"):
        build_test_planning_input(substituted_context, impact)


def test_repeated_revision_occurrences_bind_to_the_exact_occurrence_id():
    occurrence_a = context(revision_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    occurrence_b = context(revision_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    impact = ChangeImpactAnalyzer().analyze(occurrence_a)
    plan_a = bind_test_plan(occurrence_a, impact, proposal(build_test_planning_input(occurrence_a, impact)))
    plan_b = bind_test_plan(occurrence_b, impact, proposal(build_test_planning_input(occurrence_b, impact)))
    assert plan_a.revision.revision_id == occurrence_a.revision_id
    assert plan_b.revision.revision_id == occurrence_b.revision_id
    assert plan_a.revision.revision_id != plan_b.revision.revision_id


def test_plan_rejects_invented_or_approved_provenance_fields():
    source_context, impact, input_data = trusted_inputs()
    built = bind_test_plan(source_context, impact, proposal(input_data)).model_dump(mode="python")
    built["approval_status"] = "approved"
    with pytest.raises(ValidationError):
        BoundPlan.model_validate(built)

    built = bind_test_plan(source_context, impact, proposal(input_data)).model_dump(mode="python")
    built["resolved_coverage_gaps"] = [input_data.coverage_gaps[0].reference_id]
    with pytest.raises(ValidationError):
        BoundPlan.model_validate(built)


def test_provider_mutation_of_its_planning_input_cannot_forge_plan_provenance():
    source_context, impact, input_data = trusted_inputs()
    with pytest.raises(TypeError):
        input_data.changed_files[0] = input_data.changed_files[0]
    with pytest.raises((TypeError, ValidationError)):
        input_data.changed_files[0].components += ("forged",)
    plan = bind_test_plan(source_context, impact, proposal(input_data))
    with pytest.raises(TypeError):
        plan.cases[0].source_references[0] = SourceReference(kind="component", reference="forged")
    with pytest.raises((TypeError, ValidationError)):
        plan.coverage_gaps[0].paths += ("forged",)
    restored = BoundPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert isinstance(restored.cases, tuple)
    assert isinstance(restored.cases[0].source_references, tuple)
    assert isinstance(restored.coverage_gaps, tuple)


def test_planning_input_excludes_unnecessary_and_untrusted_pr_metadata():
    data = planning_input().model_dump(mode="json")
    serialized = str(data)
    for excluded in ("title", "author", "url", "source_branch", "description"):
        assert excluded not in data
    assert "Untrusted PR title" not in serialized
    assert "untrusted-author" not in serialized
