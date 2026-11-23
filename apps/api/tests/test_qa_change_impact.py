from datetime import datetime

import pytest

from app.schemas.change_impact import ImpactCategory, ImpactSeverity, TestPriority as ImpactTestPriority
from app.schemas.github import GitHubChangedFile, GitHubPRContext
from app.services.change_impact import ChangeImpactAnalyzer


BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def changed_file(path: str, status: str = "modified", previous: str | None = None) -> GitHubChangedFile:
    return GitHubChangedFile(
        path=path,
        status=status,
        additions=1,
        deletions=0,
        changes=1,
        previous_filename=previous,
    )


def context(files: list[GitHubChangedFile]) -> GitHubPRContext:
    return GitHubPRContext(
        repository_id=987654321,
        repository_full_name="acme/widget",
        repository_owner="acme",
        repository_name="widget",
        pull_request_number=42,
        title="Impact analysis fixture",
        url="https://github.com/acme/widget/pull/42",
        author="tester",
        state="open",
        draft=False,
        source_branch="feature/change",
        target_branch="main",
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        changed_files=files,
        reported_changed_files=len(files),
        event_type="synchronize",
        delivery_id="impact-test",
        event_updated_at=datetime(2026, 11, 20),
    )


def test_classifies_repository_paths_and_conservatively_maps_candidate_suites():
    result = ChangeImpactAnalyzer().analyze(context([
        changed_file("apps/api/app/api/routes/auth.py"),
        changed_file("apps/api/app/models/inventory.py"),
        changed_file("apps/web/src/pages/OrdersPage.tsx"),
        changed_file("apps/api/alembic/versions/20261120_add_github_ingestion.py"),
        changed_file("docker-compose.yml"),
        changed_file("README.md"),
        changed_file("apps/api/tests/test_qa_github.py"),
        changed_file("unmapped/vendor.bin"),
    ]))

    components = {component.component_id: component for component in result.impacted_components}
    categories = {category.category for category in result.impact_categories}
    suites = {suite.suite_id: suite for suite in result.candidate_test_suites}
    assert components["authentication"].severity == ImpactSeverity.HIGH
    assert "inventory" in components
    assert "orders" in components
    assert "agentic_qa" in components
    assert "infrastructure" in components
    assert "documentation" in components
    assert "unknown" in components
    assert ImpactCategory.API in categories
    assert ImpactCategory.DATABASE in categories
    assert ImpactCategory.FRONTEND in categories
    assert ImpactCategory.INFRASTRUCTURE in categories
    assert ImpactCategory.DOCUMENTATION in categories
    assert suites["apps/api/tests/test_permission_resolution.py"].priority == ImpactTestPriority.HIGH
    assert suites["apps/api/tests/test_qa_github.py"].selection_source == "changed_test_file+component_rule"
    assert any(gap.code == "unmapped_or_unsafe_path" for gap in result.coverage_gaps)
    assert result.summary.unmapped_change_count == 1
    assert result.base_sha == BASE_SHA and result.head_sha == HEAD_SHA


@pytest.mark.parametrize("status", ["added", "modified", "removed", "renamed", "copied", "changed", "unchanged"])
def test_accepts_each_github_change_status(status):
    previous = "apps/api/app/services/auth.py" if status in {"renamed", "copied"} else None
    result = ChangeImpactAnalyzer().analyze(context([
        changed_file("apps/api/app/api/routes/customers.py", status, previous)
    ]))
    assert result.changed_files[0].status == status
    assert "customers" in result.changed_files[0].components


def test_rename_and_copy_analyze_both_paths():
    result = ChangeImpactAnalyzer().analyze(context([
        changed_file("apps/api/app/api/routes/customers.py", "renamed", "apps/api/app/services/auth.py"),
        changed_file("apps/api/app/models/catalog.py", "copied", "apps/api/app/models/inventory.py"),
    ]))
    assert result.changed_files[0].previous_path == "apps/api/app/services/auth.py"
    assert {"authentication", "customers"}.issubset(set(result.changed_files[0].components))
    assert {"catalog", "inventory"}.issubset(set(result.changed_files[1].components))


def test_unsafe_previous_filename_is_reported_without_rejecting_safe_new_path():
    result = ChangeImpactAnalyzer().analyze(context([
        changed_file("apps/api/app/api/routes/customers.py", "renamed", "../../outside.py")
    ]))
    impact = result.changed_files[0]
    assert impact.path == "apps/api/app/api/routes/customers.py"
    assert impact.previous_path == "../../outside.py"
    assert impact.safe_path is False
    assert "customers" in impact.components
    assert "unknown" in impact.components
    assert any(item.path == "../../outside.py" for item in result.unmapped_changes)


def test_empty_change_set_is_explicit_and_deterministic():
    result = ChangeImpactAnalyzer().analyze(context([]))
    assert result.changed_files == []
    assert result.impacted_components == []
    assert result.candidate_test_suites == []
    assert result.overall_severity == ImpactSeverity.UNKNOWN
    assert result.coverage_gaps[0].code == "empty_changed_file_list"


def test_duplicate_paths_are_collapsed_without_duplicate_suites():
    item = changed_file("apps/api/app/api/routes/auth.py")
    result = ChangeImpactAnalyzer().analyze(context([item, item.model_copy(deep=True)]))
    assert result.summary.changed_file_count == 1
    assert result.changed_files[0].occurrences == 2
    suite_ids = [suite.suite_id for suite in result.candidate_test_suites]
    assert len(suite_ids) == len(set(suite_ids))


@pytest.mark.parametrize("unsafe_path", [
    "/apps/api/app/api/routes/auth.py",
    "C:\\repo\\apps\\api\\app\\api\\routes\\auth.py",
    "../README.md",
    "apps/api/../../README.md",
    "apps/api/app/\x00routes.py",
])
def test_absolute_traversal_and_malformed_paths_are_never_matched(unsafe_path):
    result = ChangeImpactAnalyzer().analyze(context([changed_file(unsafe_path)]))
    impact = result.changed_files[0]
    assert impact.safe_path is False
    assert impact.components == ["unknown"]
    assert impact.categories == [ImpactCategory.UNKNOWN]
    assert impact.severity == ImpactSeverity.UNKNOWN


def test_windows_separators_are_normalized_and_match_repository_rules():
    result = ChangeImpactAnalyzer().analyze(context([
        changed_file(r"apps\api\app\api\routes\customers.py")
    ]))
    assert result.changed_files[0].path == "apps/api/app/api/routes/customers.py"
    assert result.changed_files[0].safe_path is True
    assert "customers" in result.changed_files[0].components


def test_unknown_and_broad_paths_are_reported_as_unmapped():
    result = ChangeImpactAnalyzer().analyze(context([
        changed_file("apps/api/app/api/routes/future_route.py"),
        changed_file("vendor/generated/tool.bin"),
    ]))
    assert len(result.unmapped_changes) == 2
    assert any(component.component_id == "api" for component in result.impacted_components)
    assert any(component.component_id == "unknown" for component in result.impacted_components)
    assert any(gap.code == "no_candidate_suite_mapping" for gap in result.coverage_gaps)


def test_customer_path_without_a_maintained_suite_reports_coverage_gap():
    result = ChangeImpactAnalyzer().analyze(context([
        changed_file("apps/api/app/api/routes/customers.py"),
    ]))

    assert any(item.component_id == "customers" for item in result.impacted_components)
    assert result.candidate_test_suites == []
    assert any(
        gap.code == "no_candidate_suite_mapping" and "customers" in gap.components
        for gap in result.coverage_gaps
    )


def test_removed_candidate_suite_is_excluded_and_reported_as_a_coverage_gap():
    removed_suite = "apps/api/tests/test_qa_orchestrator.py"
    result = ChangeImpactAnalyzer().analyze(context([
        changed_file(removed_suite, status="removed"),
    ]))

    candidate_ids = {suite.suite_id for suite in result.candidate_test_suites}
    assert removed_suite not in candidate_ids
    assert "apps/api/tests/test_qa_api.py" in candidate_ids
    assert any(
        gap.code == "candidate_suite_unavailable" and gap.paths == [removed_suite]
        for gap in result.coverage_gaps
    )


def test_renamed_away_candidate_suite_is_excluded_but_other_suites_remain():
    old_suite = "apps/api/tests/test_qa_orchestrator.py"
    result = ChangeImpactAnalyzer().analyze(context([
        changed_file(
            "apps/api/tests/test_qa_orchestrator_replacement.py",
            status="renamed",
            previous=old_suite,
        ),
    ]))

    candidate_ids = {suite.suite_id for suite in result.candidate_test_suites}
    assert old_suite not in candidate_ids
    assert "apps/api/tests/test_qa_api.py" in candidate_ids
    assert any(
        gap.code == "candidate_suite_unavailable" and gap.paths == [old_suite]
        for gap in result.coverage_gaps
    )


def test_changed_config_selects_config_suite_and_reason_rules_are_explainable():
    result = ChangeImpactAnalyzer().analyze(context([changed_file("apps/api/app/core/config.py")]))
    suite = next(item for item in result.candidate_test_suites if item.suite_id == "apps/api/tests/test_config.py")
    assert suite.priority == ImpactTestPriority.MEDIUM
    assert suite.selection_source == "component_rule"
    assert suite.reason
    assert any(reason.rule_id == "category.configuration" for reason in result.changed_files[0].reasons)


def test_frontend_components_and_api_clients_are_classified():
    result = ChangeImpactAnalyzer().analyze(context([
        changed_file("apps/web/src/components/StatusBadge.tsx"),
        changed_file("apps/web/src/api/orders.ts"),
    ]))
    categories = {item.category for item in result.impact_categories}
    components = {item.component_id for item in result.impacted_components}
    assert ImpactCategory.FRONTEND in categories
    assert ImpactCategory.INTEGRATION in categories
    assert "frontend" in components
    assert "orders" in components


def test_output_is_stable_when_input_order_changes():
    files = [
        changed_file("apps/web/src/pages/OrdersPage.tsx"),
        changed_file("apps/api/app/api/routes/auth.py"),
        changed_file("misc/unknown.bin"),
    ]
    analyzer = ChangeImpactAnalyzer()
    first = analyzer.analyze(context(files)).model_dump(mode="json")
    second = analyzer.analyze(context(list(reversed(files)))).model_dump(mode="json")
    assert first == second
