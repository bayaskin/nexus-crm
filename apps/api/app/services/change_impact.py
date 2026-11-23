import fnmatch
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from app.schemas.change_impact import (
    CandidateTestSuite,
    ChangeImpactAnalysis,
    ChangeImpactSummary,
    ChangedFileImpact,
    CoverageGap,
    ImpactCategory,
    ImpactCategorySummary,
    ImpactReason,
    ImpactSeverity,
    ImpactedComponent,
    TestPriority,
    UnmappedChange,
)
from app.schemas.github import GitHubChangedFile, GitHubPRContext
from app.services.change_impact_rules import (
    COMPONENT_DISPLAY_NAMES,
    PATH_RULES,
    TEST_SUITE_RULES,
)


_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_SEVERITY_RANK = {
    ImpactSeverity.UNKNOWN: 0,
    ImpactSeverity.LOW: 1,
    ImpactSeverity.MEDIUM: 2,
    ImpactSeverity.HIGH: 3,
    ImpactSeverity.CRITICAL: 4,
}


@dataclass(frozen=True)
class _PathImpact:
    display_path: str
    normalized_path: str | None
    safe: bool
    component_ids: tuple[str, ...]
    categories: tuple[ImpactCategory, ...]
    severity: ImpactSeverity
    reasons: tuple[ImpactReason, ...]
    has_specific_mapping: bool


_FALLBACK_AREAS: tuple[tuple[str, str, ImpactCategory], ...] = (
    ("apps/api/app/api/", "api", ImpactCategory.API),
    ("apps/api/app/services/", "backend", ImpactCategory.BACKEND),
    ("apps/api/app/core/", "backend", ImpactCategory.BACKEND),
    ("apps/api/app/models/", "database", ImpactCategory.DATABASE),
    ("apps/api/alembic/", "database", ImpactCategory.DATABASE),
    ("apps/api/tests/", "test_automation", ImpactCategory.AUTOMATION),
    ("apps/web/src/", "frontend", ImpactCategory.FRONTEND),
    ("apps/web/", "frontend", ImpactCategory.FRONTEND),
    ("automation/", "test_automation", ImpactCategory.AUTOMATION),
    ("infra/", "infrastructure", ImpactCategory.INFRASTRUCTURE),
)


class ChangeImpactAnalyzer:
    """Rule-based analyzer. It never reads files or executes candidate suites."""

    def analyze(self, context: GitHubPRContext) -> ChangeImpactAnalysis:
        unique_files: dict[tuple[str, str | None, str], tuple[GitHubChangedFile, int]] = {}
        for changed_file in context.changed_files:
            current = self._analyze_path(changed_file.path)
            previous = None
            if changed_file.status in {"renamed", "copied"} and changed_file.previous_filename:
                previous = self._analyze_path(changed_file.previous_filename)
            key = (current.display_path, previous.display_path if previous else None, changed_file.status)
            if key in unique_files:
                prior_file, occurrences = unique_files[key]
                unique_files[key] = (prior_file, occurrences + 1)
            else:
                unique_files[key] = (changed_file, 1)

        file_impacts: list[ChangedFileImpact] = []
        component_paths: dict[str, set[str]] = defaultdict(set)
        component_categories: dict[str, set[ImpactCategory]] = defaultdict(set)
        component_severities: dict[str, list[ImpactSeverity]] = defaultdict(list)
        component_reasons: dict[str, set[str]] = defaultdict(set)
        component_reason_text: dict[str, set[str]] = defaultdict(set)
        category_paths: dict[ImpactCategory, set[str]] = defaultdict(set)
        category_severities: dict[ImpactCategory, list[ImpactSeverity]] = defaultdict(list)
        category_rule_ids: dict[ImpactCategory, set[str]] = defaultdict(set)
        suites_by_id: dict[str, dict[str, object]] = {}
        unmapped: dict[tuple[str, str, str], UnmappedChange] = {}

        for key in sorted(unique_files):
            changed_file, occurrences = unique_files[key]
            current = self._analyze_path(changed_file.path)
            old = None
            if changed_file.status in {"renamed", "copied"} and changed_file.previous_filename:
                old = self._analyze_path(changed_file.previous_filename)
            impacts = (current, old) if old else (current,)
            components = sorted({component for impact in impacts for component in impact.component_ids})
            categories = sorted(
                {category for impact in impacts for category in impact.categories}, key=lambda item: item.value
            )
            reasons = {
                (reason.rule_id, reason.explanation)
                for impact in impacts
                for reason in impact.reasons
            }
            severity = self._maximum_severity(impact.severity for impact in impacts)
            safe = all(impact.safe for impact in impacts)
            displayed_path = current.display_path
            displayed_previous = old.display_path if old else None
            file_impacts.append(ChangedFileImpact(
                path=displayed_path,
                previous_path=displayed_previous,
                status=changed_file.status,
                occurrences=occurrences,
                safe_path=safe,
                components=components,
                categories=categories,
                severity=severity,
                reasons=[ImpactReason(rule_id=rule_id, explanation=explanation)
                         for rule_id, explanation in sorted(reasons)],
            ))

            for impact in impacts:
                normalized_or_display = impact.normalized_path or impact.display_path
                if not impact.has_specific_mapping or not impact.safe:
                    reason = (
                        "Path was rejected as unsafe; it was not used for rule matching."
                        if not impact.safe
                        else "Only a broad repository-area mapping was available."
                    )
                    unmapped[(impact.display_path, changed_file.status, reason)] = UnmappedChange(
                        path=impact.display_path,
                        status=changed_file.status,
                        reason=reason,
                    )
                for component in impact.component_ids:
                    component_paths[component].add(normalized_or_display)
                    component_categories[component].update(impact.categories)
                    component_severities[component].append(impact.severity)
                    component_reasons[component].update(reason.rule_id for reason in impact.reasons)
                    component_reason_text[component].update(reason.explanation for reason in impact.reasons)
                for category in impact.categories:
                    category_paths[category].add(normalized_or_display)
                    category_severities[category].append(impact.severity)
                    category_rule_ids[category].update(reason.rule_id for reason in impact.reasons)

        components_output = [
            ImpactedComponent(
                component_id=component,
                severity=self._maximum_severity(component_severities[component]),
                categories=sorted(component_categories[component], key=lambda item: item.value),
                paths=sorted(component_paths[component]),
                rule_ids=sorted(component_reasons[component]),
                reasons=sorted(component_reason_text[component]),
            )
            for component in sorted(component_paths)
        ]
        categories_output = [
            ImpactCategorySummary(
                category=category,
                severity=self._maximum_severity(category_severities[category]),
                paths=sorted(category_paths[category]),
                rule_ids=sorted(category_rule_ids[category]),
            )
            for category in sorted(category_paths, key=lambda item: item.value)
        ]

        component_ids = {item.component_id for item in components_output}
        for component in component_ids:
            matching_rules = [rule for rule in TEST_SUITE_RULES if component in rule.components]
            if matching_rules:
                severity = next(item.severity for item in components_output if item.component_id == component)
                priority = self._priority_for_severity(severity)
                for rule in matching_rules:
                    self._add_suite(suites_by_id, rule.suite_id, priority, rule.reason, component, "component_rule")

        suite_rules_by_path = {rule.suite_id: rule for rule in TEST_SUITE_RULES}
        for file_impact in file_impacts:
            suite_rule = suite_rules_by_path.get(file_impact.path)
            if suite_rule:
                for component in suite_rule.components:
                    if component in component_ids:
                        self._add_suite(
                            suites_by_id,
                            suite_rule.suite_id,
                            self._priority_for_severity(file_impact.severity),
                            "The changed file is an existing candidate test suite.",
                            component,
                            "changed_test_file",
                        )

        unavailable_suite_paths = {
            path
            for file_impact in file_impacts
            for path in (
                [file_impact.path] if file_impact.status == "removed"
                else [file_impact.previous_path]
                if file_impact.status == "renamed" and file_impact.previous_path
                else []
            )
        }
        available_suite_paths = {
            file_impact.path
            for file_impact in file_impacts
            if file_impact.status != "removed"
        }
        unavailable_suite_paths.difference_update(available_suite_paths)
        unavailable_suites = {
            suite_id: rule
            for rule in TEST_SUITE_RULES
            if (suite_id := rule.suite_id) in suites_by_id and suite_id in unavailable_suite_paths
        }
        for suite_id in unavailable_suites:
            suites_by_id.pop(suite_id)

        suites_output = [
            CandidateTestSuite(
                suite_id=suite_id,
                priority=data["priority"],
                reason=data["reason"],
                impacted_components=sorted(data["components"]),
                selection_source="+".join(sorted(data["sources"])),
            )
            for suite_id, data in sorted(suites_by_id.items())
        ]

        coverage_gaps: list[CoverageGap] = []
        components_with_suites = {
            component
            for data in suites_by_id.values()
            for component in data["components"]
        }
        for component in sorted(component_ids - components_with_suites):
            if component in {"documentation", "test_automation"}:
                continue
            coverage_gaps.append(CoverageGap(
                code="no_candidate_suite_mapping",
                message=f"No maintained candidate test-suite mapping exists for {COMPONENT_DISPLAY_NAMES.get(component, component)}.",
                components=[component],
                paths=sorted(component_paths[component]),
            ))
        if not file_impacts:
            coverage_gaps.append(CoverageGap(
                code="empty_changed_file_list",
                message="No changed files were supplied, so no components or test candidates could be selected.",
            ))
        for item in unmapped.values():
            coverage_gaps.append(CoverageGap(
                code="unmapped_or_unsafe_path",
                message=item.reason,
                components=[component for component in next(
                    (impact.components for impact in file_impacts if impact.path == item.path), []
                )],
                paths=[item.path],
            ))
        for suite_id, rule in unavailable_suites.items():
            coverage_gaps.append(CoverageGap(
                code="candidate_suite_unavailable",
                message=f"Candidate test suite {suite_id} is unavailable at the pull request head because it was removed or renamed.",
                components=sorted(rule.components),
                paths=[suite_id],
            ))
        coverage_gaps.sort(key=lambda item: (item.code, item.paths, item.components))

        overall_severity = self._maximum_severity(item.severity for item in file_impacts)
        return ChangeImpactAnalysis(
            repository_id=context.repository_id,
            pull_request_number=context.pull_request_number,
            base_sha=context.base_sha,
            head_sha=context.head_sha,
            summary=ChangeImpactSummary(
                text=(
                    f"{len(file_impacts)} unique changed files affect {len(components_output)} components; "
                    f"overall severity is {overall_severity.value}; {len(suites_output)} candidate test suites selected."
                ),
                changed_file_count=len(file_impacts),
                impacted_component_count=len(components_output),
                impact_category_count=len(categories_output),
                candidate_suite_count=len(suites_output),
                unmapped_change_count=len(unmapped),
            ),
            overall_severity=overall_severity,
            changed_files=file_impacts,
            impacted_components=components_output,
            impact_categories=categories_output,
            candidate_test_suites=suites_output,
            unmapped_changes=sorted(unmapped.values(), key=lambda item: (item.path, item.status, item.reason)),
            coverage_gaps=coverage_gaps,
        )

    def _analyze_path(self, raw_path: str) -> _PathImpact:
        normalized = self._normalize_path(raw_path)
        if normalized is None:
            display = self._display_untrusted_path(raw_path)
            return _PathImpact(
                display_path=display,
                normalized_path=None,
                safe=False,
                component_ids=("unknown",),
                categories=(ImpactCategory.UNKNOWN,),
                severity=ImpactSeverity.UNKNOWN,
                reasons=(ImpactReason(
                    rule_id="path.unsafe",
                    explanation="Path is absolute, traverses outside the repository, or contains malformed control characters.",
                ),),
                has_specific_mapping=False,
            )

        matched = [
            rule for rule in PATH_RULES
            if any(fnmatch.fnmatchcase(normalized, pattern) for pattern in rule.patterns)
        ]
        explicit_components = tuple(sorted({rule.component_id for rule in matched if rule.component_id}))
        has_specific = bool(explicit_components)
        fallback = self._fallback_area(normalized) if not explicit_components else None
        components = explicit_components or ((fallback[0],) if fallback else ("unknown",))
        categories = {category for rule in matched for category in rule.categories}
        if fallback:
            categories.add(fallback[1])
        if not categories:
            categories.add(ImpactCategory.UNKNOWN)
        if matched:
            reasons = tuple(
                ImpactReason(rule_id=rule.rule_id, explanation=rule.explanation)
                for rule in sorted(matched, key=lambda rule: rule.rule_id)
            )
            severity = self._maximum_severity(rule.severity for rule in matched)
        elif fallback:
            reasons = (ImpactReason(
                rule_id="area.broad_fallback",
                explanation="Only the repository area could be identified; no component-specific rule matched.",
            ),)
            severity = ImpactSeverity.UNKNOWN
        else:
            reasons = (ImpactReason(
                rule_id="path.unmapped",
                explanation="No configured impact rule matches this repository path.",
            ),)
            severity = ImpactSeverity.UNKNOWN
        return _PathImpact(
            display_path=normalized,
            normalized_path=normalized,
            safe=True,
            component_ids=components,
            categories=tuple(sorted(categories, key=lambda item: item.value)),
            severity=severity,
            reasons=reasons,
            has_specific_mapping=has_specific,
        )

    @staticmethod
    def _normalize_path(path: str) -> str | None:
        if not isinstance(path, str) or not path or len(path) > 4096:
            return None
        if any(unicodedata.category(character).startswith("C") for character in path):
            return None
        converted = path.replace("\\", "/")
        if converted.startswith("/") or _DRIVE_PREFIX.match(converted):
            return None
        segments = converted.split("/")
        if any(segment == ".." for segment in segments):
            return None
        normalized_segments = [segment for segment in segments if segment not in {"", "."}]
        if not normalized_segments:
            return None
        return "/".join(normalized_segments)

    @staticmethod
    def _display_untrusted_path(path: str) -> str:
        if not isinstance(path, str):
            return "<non-string path>"
        escaped = "".join(
            f"\\u{ord(character):04x}" if unicodedata.category(character).startswith("C") else character
            for character in path
        )
        return escaped[:4096]

    @staticmethod
    def _fallback_area(path: str) -> tuple[str, ImpactCategory] | None:
        for prefix, component, category in _FALLBACK_AREAS:
            if path.startswith(prefix):
                return component, category
        if path.startswith("docs/") or path.lower().startswith("readme"):
            return "documentation", ImpactCategory.DOCUMENTATION
        if path.startswith(".github/"):
            return "infrastructure", ImpactCategory.INFRASTRUCTURE
        if path in {"docker-compose.yml", "Dockerfile"}:
            return "infrastructure", ImpactCategory.INFRASTRUCTURE
        if path.startswith("apps/api/") or path.startswith("apps/web/"):
            return "unknown", ImpactCategory.UNKNOWN
        return None

    @staticmethod
    def _maximum_severity(severities: Iterable[ImpactSeverity]) -> ImpactSeverity:
        return max(severities, key=lambda severity: _SEVERITY_RANK[severity], default=ImpactSeverity.UNKNOWN)

    @staticmethod
    def _priority_for_severity(severity: ImpactSeverity) -> TestPriority:
        if severity in {ImpactSeverity.CRITICAL, ImpactSeverity.HIGH}:
            return TestPriority.HIGH
        if severity == ImpactSeverity.MEDIUM:
            return TestPriority.MEDIUM
        return TestPriority.LOW

    @staticmethod
    def _add_suite(
        suites: dict[str, dict[str, object]],
        suite_id: str,
        priority: TestPriority,
        reason: str,
        component: str,
        source: str,
    ) -> None:
        suite = suites.setdefault(suite_id, {
            "priority": priority,
            "reason": reason,
            "components": set(),
            "sources": set(),
        })
        priorities = {TestPriority.HIGH: 3, TestPriority.MEDIUM: 2, TestPriority.LOW: 1}
        if priorities[priority] > priorities[suite["priority"]]:
            suite["priority"] = priority
        suite["components"].add(component)
        suite["sources"].add(source)
