"""Centralized deterministic impact rules.

Every matching rule contributes its category and explanation. The highest
matched severity wins, using the order critical > high > medium > low > unknown.
The analyzer maps critical/high to high test priority, medium to medium, and
low/unknown to low. Rules describe path-based triage only, not test coverage or
proof that a change is safe.
"""

from dataclasses import dataclass

from app.schemas.change_impact import ImpactCategory, ImpactSeverity


@dataclass(frozen=True)
class PathRule:
    rule_id: str
    patterns: tuple[str, ...]
    explanation: str
    categories: tuple[ImpactCategory, ...]
    severity: ImpactSeverity
    component_id: str | None = None


PATH_RULES: tuple[PathRule, ...] = (
    PathRule(
        "component.authentication",
        ("apps/api/app/api/routes/auth.py", "apps/api/app/services/auth.py", "apps/api/app/core/security.py", "apps/api/app/models/sessions.py", "apps/web/src/auth/**", "apps/web/src/pages/LoginPage.tsx", "apps/web/src/pages/ChangePasswordPage.tsx", "apps/web/src/api/client.ts"),
        "Authentication routes, token security, session persistence, or authentication service changed.",
        (ImpactCategory.INTEGRATION,),
        ImpactSeverity.HIGH,
        "authentication",
    ),
    PathRule(
        "component.user_management",
        ("apps/api/app/api/routes/users.py", "apps/api/app/models/identity.py", "apps/api/app/services/permissions.py", "apps/api/app/schemas/auth.py", "apps/web/src/pages/UsersPage.tsx"),
        "User, role, permission, or user-delivery functionality changed.",
        (ImpactCategory.INTEGRATION,),
        ImpactSeverity.HIGH,
        "user_management",
    ),
    PathRule(
        "component.customers",
        ("apps/api/app/api/routes/customers.py", "apps/api/app/schemas/customers.py", "apps/api/app/models/crm.py", "apps/web/src/pages/CustomersPage.tsx", "apps/web/src/pages/CustomerDetailsPage.tsx", "apps/web/src/api/resources.ts"),
        "Customer routes, schemas, persistence, or customer pages changed.",
        (ImpactCategory.INTEGRATION,),
        ImpactSeverity.MEDIUM,
        "customers",
    ),
    PathRule(
        "component.catalog",
        ("apps/api/app/api/routes/products.py", "apps/api/app/schemas/products.py", "apps/api/app/models/catalog.py", "apps/web/src/pages/CreateOrderPage.tsx", "apps/web/src/api/resources.ts"),
        "Product and catalog functionality changed.",
        (ImpactCategory.INTEGRATION,),
        ImpactSeverity.MEDIUM,
        "catalog",
    ),
    PathRule(
        "component.orders",
        ("apps/api/app/api/routes/orders.py", "apps/api/app/services/orders.py", "apps/api/app/schemas/orders.py", "apps/api/app/models/sales.py", "apps/web/src/pages/OrdersPage.tsx", "apps/web/src/pages/OrderDetailsPage.tsx", "apps/web/src/pages/CreateOrderPage.tsx", "apps/web/src/api/orders.ts"),
        "Order routes, services, persistence, or order pages changed.",
        (ImpactCategory.INTEGRATION,),
        ImpactSeverity.MEDIUM,
        "orders",
    ),
    PathRule(
        "component.inventory",
        ("apps/api/app/api/routes/inventory.py", "apps/api/app/services/inventory.py", "apps/api/app/schemas/inventory.py", "apps/api/app/models/inventory.py", "apps/web/src/pages/InventoryPage.tsx", "apps/web/src/api/inventory.ts"),
        "Inventory routes, services, persistence, or inventory page changed.",
        (ImpactCategory.INTEGRATION,),
        ImpactSeverity.MEDIUM,
        "inventory",
    ),
    PathRule(
        "component.agentic_qa",
        ("apps/api/app/api/routes/qa.py", "apps/api/app/api/routes/github.py", "apps/api/app/services/qa_*.py", "apps/api/app/services/github_*.py", "apps/api/app/schemas/qa.py", "apps/api/app/schemas/github.py", "apps/api/app/schemas/change_impact.py", "apps/api/app/models/qa.py", "apps/api/app/models/github.py", "apps/api/app/services/change_impact*.py", "apps/api/app/services/change_impact_rules.py", "apps/api/alembic/versions/*qa*.py", "apps/api/alembic/versions/*github*.py"),
        "Agentic QA orchestration, GitHub ingestion, or change-impact analysis changed.",
        (ImpactCategory.INTEGRATION,),
        ImpactSeverity.HIGH,
        "agentic_qa",
    ),
    PathRule(
        "component.agentic_qa_tests",
        ("apps/api/tests/test_qa_*.py",),
        "A test suite for Agentic QA changed.",
        (ImpactCategory.AUTOMATION,),
        ImpactSeverity.LOW,
        "agentic_qa",
    ),
    PathRule(
        "component.authentication_tests",
        ("apps/api/tests/test_permission_resolution.py", "apps/api/tests/test_user_delivery.py"),
        "A test suite covering authentication or user access behavior changed.",
        (ImpactCategory.AUTOMATION,),
        ImpactSeverity.LOW,
        "authentication",
    ),
    PathRule(
        "component.user_management_tests",
        ("apps/api/tests/test_permission_resolution.py", "apps/api/tests/test_user_delivery.py"),
        "A test suite covering user management behavior changed.",
        (ImpactCategory.AUTOMATION,),
        ImpactSeverity.LOW,
        "user_management",
    ),
    PathRule(
        "component.configuration_tests",
        ("apps/api/tests/test_config.py",),
        "The application configuration test suite changed.",
        (ImpactCategory.AUTOMATION,),
        ImpactSeverity.LOW,
        "configuration",
    ),
    PathRule(
        "category.api_routes",
        ("apps/api/app/api/routes/*.py",),
        "An API route changed.",
        (ImpactCategory.API,),
        ImpactSeverity.MEDIUM,
    ),
    PathRule(
        "category.backend_services",
        ("apps/api/app/services/*.py", "apps/api/app/core/*.py", "apps/api/app/schemas/*.py"),
        "Backend service, schema, or application-core code changed.",
        (ImpactCategory.BACKEND,),
        ImpactSeverity.MEDIUM,
    ),
    PathRule(
        "category.database_models",
        ("apps/api/app/models/*.py", "apps/api/alembic/versions/*.py"),
        "Database model or migration code changed.",
        (ImpactCategory.DATABASE,),
        ImpactSeverity.HIGH,
    ),
    PathRule(
        "category.frontend",
        ("apps/web/src/**/*.tsx", "apps/web/src/**/*.ts", "apps/web/src/**/*.css", "apps/web/src/**/*.html", "apps/web/src/components/**"),
        "Frontend source code or styles changed.",
        (ImpactCategory.FRONTEND,),
        ImpactSeverity.MEDIUM,
    ),
    PathRule(
        "category.frontend_api_client",
        ("apps/web/src/api/*.ts",),
        "A frontend API client changed.",
        (ImpactCategory.INTEGRATION,),
        ImpactSeverity.MEDIUM,
    ),
    PathRule(
        "category.automation_tests",
        ("apps/api/tests/*.py", "apps/web/**/*.test.ts", "apps/web/**/*.test.tsx", "automation/**"),
        "An automation or test file changed.",
        (ImpactCategory.AUTOMATION,),
        ImpactSeverity.LOW,
        "test_automation",
    ),
    PathRule(
        "category.infrastructure",
        ("docker-compose.yml", "Dockerfile", "**/Dockerfile", "infra/**", ".github/workflows/**"),
        "Deployment, container, or CI infrastructure changed.",
        (ImpactCategory.INFRASTRUCTURE,),
        ImpactSeverity.HIGH,
        "infrastructure",
    ),
    PathRule(
        "category.configuration",
        ("apps/api/requirements*.txt", "apps/web/package.json", "apps/web/package-lock.json", "apps/web/tsconfig*.json", "apps/web/vite.config.*", "apps/api/app/core/config.py", ".env.example", ".gitignore", ".env*", "**/.env*"),
        "Application dependencies or configuration changed.",
        (ImpactCategory.CONFIGURATION,),
        ImpactSeverity.MEDIUM,
        "configuration",
    ),
    PathRule(
        "category.documentation",
        ("README*", "**/*.md", "docs/**"),
        "Documentation changed.",
        (ImpactCategory.DOCUMENTATION,),
        ImpactSeverity.LOW,
        "documentation",
    ),
)


@dataclass(frozen=True)
class TestSuiteRule:
    suite_id: str
    components: tuple[str, ...]
    reason: str


TEST_SUITE_RULES: tuple[TestSuiteRule, ...] = (
    TestSuiteRule("apps/api/tests/test_permission_resolution.py", ("authentication", "user_management"), "Covers role and explicit permission resolution."),
    TestSuiteRule("apps/api/tests/test_user_delivery.py", ("authentication", "user_management"), "Covers user credential delivery and related lifecycle behavior."),
    TestSuiteRule("apps/api/tests/test_qa_foundation.py", ("agentic_qa",), "Covers QA state, transitions, and foundation contracts."),
    TestSuiteRule("apps/api/tests/test_qa_orchestrator.py", ("agentic_qa",), "Covers QA lifecycle, cancellation, timeout, and workflow integration."),
    TestSuiteRule("apps/api/tests/test_qa_api.py", ("agentic_qa",), "Covers QA API authorization and lifecycle behavior."),
    TestSuiteRule("apps/api/tests/test_qa_github.py", ("agentic_qa",), "Covers GitHub ingestion and change-impact integration behavior."),
    TestSuiteRule("apps/api/tests/test_qa_change_impact.py", ("agentic_qa",), "Covers deterministic changed-file classification, prioritization, and coverage gaps."),
    TestSuiteRule("apps/api/tests/test_qa_github_mysql.py", ("agentic_qa",), "Covers MySQL migration and concurrency behavior; requires the disposable MySQL test environment."),
    TestSuiteRule("apps/api/tests/test_config.py", ("configuration",), "Covers application configuration validation."),
)


COMPONENT_DISPLAY_NAMES: dict[str, str] = {
    "authentication": "Authentication",
    "user_management": "User management",
    "customers": "Customers",
    "catalog": "Product catalog",
    "orders": "Orders",
    "inventory": "Inventory",
    "agentic_qa": "Agentic QA",
    "api": "API",
    "backend": "Backend",
    "database": "Database",
    "frontend": "Frontend",
    "test_automation": "Test automation",
    "infrastructure": "Infrastructure",
    "configuration": "Configuration",
    "documentation": "Documentation",
    "unknown": "Unknown or unmapped",
}

