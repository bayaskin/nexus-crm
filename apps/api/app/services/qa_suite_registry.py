"""Trusted QA suite registry and fail-closed execution policy.

This module describes approved suites; it does not launch containers or run
tests. Selectors and service identifiers are version-controlled data. They
must never be populated from GitHub content, a test plan, or environment
overrides. Phase 3B.1 supplies an atomic claim and a stable selector ID; a
future fixed launcher must still resolve these trusted values without accepting
commands or runtime configuration from callers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.schemas.qa_execution import RunnerServiceId as SuiteRunner


class ExecutionReasonCode(StrEnum):
    ALLOWED = "allowed"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_OVERRIDE = "unsupported_override"
    UNKNOWN_SUITE = "unknown_suite"
    SUITE_DISABLED = "suite_disabled"
    UNSAFE_ENVIRONMENT = "unsafe_environment"
    TIMEOUT_EXCEEDED = "timeout_exceeded"
    CONCURRENCY_LIMIT = "concurrency_limit"
    REGISTRY_INVALID = "registry_invalid"
    POLICY_CONFIG_INVALID = "policy_config_invalid"
    INCOMPATIBLE_SUITES = "incompatible_suites"


_SuiteId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,63}$", max_length=64)]
_Selector = Annotated[str, Field(min_length=1, max_length=256)]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SuiteDefinition(_StrictFrozenModel):
    """A trusted, fixed selection exposed by the versioned registry."""

    suite_id: _SuiteId
    description: str = Field(min_length=1, max_length=300)
    runner_service: SuiteRunner
    selector_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,63}$", max_length=64)]
    selectors: tuple[_Selector, ...] = Field(min_length=1, max_length=32)
    timeout_limit_seconds: int = Field(ge=1, le=3600)
    enabled: bool

    @field_validator("selectors", mode="before")
    @classmethod
    def freeze_selectors(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("selectors")
    @classmethod
    def validate_fixed_path_selectors(cls, selectors: tuple[str, ...]) -> tuple[str, ...]:
        # These are path/glob selectors, never command-line fragments. This
        # rejects absolute paths, traversal, options, shell syntax, and URLs.
        for selector in selectors:
            if (
                selector.startswith("/")
                or "\\" in selector
                or ".." in selector.split("/")
                or not re.fullmatch(r"[A-Za-z0-9_./*-]+", selector)
                or selector.startswith("-")
            ):
                raise ValueError("Suite selector must be a safe repository-relative path")
        if len(selectors) != len(set(selectors)):
            raise ValueError("Suite selectors must be unique")
        return selectors


class ExecutionPolicyConfig(_StrictFrozenModel):
    max_concurrent_executions: int = Field(ge=1, le=32)


class ExecutionRequest(_StrictFrozenModel):
    """Untrusted selection request; deliberately has no command/env/path field."""

    suite_ids: tuple[_SuiteId, ...] = Field(min_length=1, max_length=4)
    requested_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)

    @field_validator("suite_ids", mode="before")
    @classmethod
    def freeze_suite_ids(cls, value):
        if isinstance(value, (list, tuple)):
            return tuple(sorted(value))
        return value

    @model_validator(mode="after")
    def unique_suite_ids(self) -> ExecutionRequest:
        if len(self.suite_ids) != len(set(self.suite_ids)):
            raise ValueError("Suite IDs must be unique")
        return self


class ExecutionEnvironment(_StrictFrozenModel):
    """Non-secret facts the trusted caller must derive from QA configuration."""

    app_env: Literal["test"]
    qa_mode: Literal[True]
    compose_project: Literal["nexus-qa"]
    network_profile: Literal["none", "qa_app", "qa_app_data"]
    network_internal: Literal[True]
    development_database_access: Literal[False]
    development_service_access: Literal[False]
    database_host: Literal["qa-mysql"] | None = None
    database_name: Literal["nexus_qa_test"] | None = None
    service_base_url: Literal["http://api:8000", "http://proxy"] | None = None


class ExecutionDecision(_StrictFrozenModel):
    """Safe policy result; never contains a command or caller-supplied value."""

    allowed: bool
    reason_code: ExecutionReasonCode
    suite_ids: tuple[str, ...] = ()
    runner_service: SuiteRunner | None = None
    selectors: tuple[str, ...] = ()
    timeout_seconds: int | None = None
    max_concurrent_executions: int | None = None


class SuiteRegistry:
    """Validated immutable registry built only from trusted application config."""

    __slots__ = ("_definitions",)

    def __init__(self, definitions: Sequence[SuiteDefinition | Mapping[str, object]] | None) -> None:
        if not definitions:
            raise ValueError("Suite registry configuration is missing or empty")
        parsed = tuple(
            item if isinstance(item, SuiteDefinition) else SuiteDefinition.model_validate(item)
            for item in definitions
        )
        ids = tuple(item.suite_id for item in parsed)
        if len(ids) != len(set(ids)):
            raise ValueError("Suite registry contains duplicate IDs")
        object.__setattr__(self, "_definitions", MappingProxyType({item.suite_id: item for item in parsed}))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SuiteRegistry is immutable")

    def get(self, suite_id: str) -> SuiteDefinition | None:
        return self._definitions.get(suite_id)

    @property
    def suite_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))


# Selectors mirror the existing guarded QA services and are intentionally
# explicit. The backend selection excludes the MySQL-only migration/concurrency
# suite; api-regression excludes the separately registered auth contract.
TRUSTED_SUITE_DEFINITIONS: tuple[SuiteDefinition, ...] = (
    SuiteDefinition(
        suite_id="unit-backend",
        description="Guarded backend unit tests using the network-disabled, in-memory SQLite runner.",
        runner_service=SuiteRunner.BACKEND_UNIT,
        selector_id="backend-unit-maintained-v1",
        selectors=(
            "tests/test_reset_qa_admin.py",
            "tests/test_migrate_qa_admin_email.py",
            "tests/test_auth_login.py",
            "tests/test_permission_resolution.py",
            "tests/test_user_delivery.py",
            "tests/test_qa_foundation.py",
            "tests/test_qa_orchestrator.py",
            "tests/test_qa_api.py",
            "tests/test_qa_github.py",
            "tests/test_qa_change_impact.py",
            "tests/test_config.py",
        ),
        timeout_limit_seconds=300,
        enabled=True,
    ),
    SuiteDefinition(
        suite_id="api-auth",
        description="API authentication contract tests against the isolated QA API and database.",
        runner_service=SuiteRunner.API,
        selector_id="api-auth-contract-v1",
        selectors=("tests/test_auth_contract.py",),
        timeout_limit_seconds=600,
        enabled=True,
    ),
    SuiteDefinition(
        suite_id="api-regression",
        description="CRM API regression tests against the isolated QA API and database.",
        runner_service=SuiteRunner.API,
        selector_id="api-regression-maintained-v1",
        selectors=(
            "tests/test_customers.py",
            "tests/test_health.py",
            "tests/test_inventory.py",
            "tests/test_orders.py",
            "tests/test_products.py",
            "tests/test_rbac.py",
            "tests/test_user_management.py",
            "tests/test_user_management_security.py",
        ),
        timeout_limit_seconds=1800,
        enabled=True,
    ),
    SuiteDefinition(
        suite_id="ui-regression",
        description="Playwright UI regression tests through the isolated QA proxy.",
        runner_service=SuiteRunner.UI,
        selector_id="ui-regression-maintained-v1",
        selectors=(
            "tests/customer-contacts.spec.ts",
            "tests/customer-lifecycle.spec.ts",
            "tests/inventory.spec.ts",
            "tests/order-creation.spec.ts",
            "tests/order-inventory.spec.ts",
            "tests/order-items.spec.ts",
            "tests/smoke.spec.ts",
            "tests/users-ui.spec.ts",
            "tests/users.spec.ts",
        ),
        timeout_limit_seconds=2400,
        enabled=True,
    ),
)

# Only maintained deterministic test evidence maps to stable suite IDs. The
# MySQL-only QA migration/concurrency suite is deliberately unmapped because
# qa-unit-tests has network_mode=none; no selector or command is derived from
# any source path.
CANDIDATE_SUITE_EXECUTION_MAP: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "apps/api/tests/test_permission_resolution.py": ("unit-backend", "api-auth"),
    "apps/api/tests/test_user_delivery.py": (
        "unit-backend", "api-auth", "api-regression",
    ),
    "apps/api/tests/test_qa_foundation.py": ("unit-backend",),
    "apps/api/tests/test_qa_orchestrator.py": ("unit-backend",),
    "apps/api/tests/test_qa_api.py": ("unit-backend",),
    "apps/api/tests/test_qa_github.py": ("unit-backend",),
    "apps/api/tests/test_qa_change_impact.py": ("unit-backend",),
    "apps/api/tests/test_qa_github_mysql.py": (),
    "apps/api/tests/test_config.py": ("unit-backend",),
})

DEFAULT_SUITE_REGISTRY = SuiteRegistry(TRUSTED_SUITE_DEFINITIONS)
DEFAULT_EXECUTION_POLICY_CONFIG = ExecutionPolicyConfig(max_concurrent_executions=2)
_USE_TRUSTED_DEFAULT = object()


def evaluate_execution_policy(
    request: ExecutionRequest | Mapping[str, object],
    environment: ExecutionEnvironment | Mapping[str, object],
    *,
    active_execution_count: int,
    registry: SuiteRegistry | Sequence[SuiteDefinition | Mapping[str, object]] | None | object = _USE_TRUSTED_DEFAULT,
    config: ExecutionPolicyConfig | Mapping[str, object] | None | object = _USE_TRUSTED_DEFAULT,
) -> ExecutionDecision:
    """Return a deterministic decision without launching or reserving work.

    The caller must provide an environment descriptor assembled from trusted
    QA configuration, not from the execution request. A future runner must
    atomically reserve concurrency after this check and before starting work.
    """

    if registry is _USE_TRUSTED_DEFAULT:
        validated_registry = DEFAULT_SUITE_REGISTRY
    else:
        try:
            validated_registry = registry if isinstance(registry, SuiteRegistry) else SuiteRegistry(registry)  # type: ignore[arg-type]
        except (TypeError, ValueError, ValidationError):
            return _deny(ExecutionReasonCode.REGISTRY_INVALID)

    if config is _USE_TRUSTED_DEFAULT:
        validated_config = DEFAULT_EXECUTION_POLICY_CONFIG
    else:
        try:
            validated_config = config if isinstance(config, ExecutionPolicyConfig) else ExecutionPolicyConfig.model_validate(config)
        except (TypeError, ValueError, ValidationError):
            return _deny(ExecutionReasonCode.POLICY_CONFIG_INVALID)

    if type(active_execution_count) is not int or active_execution_count < 0:
        return _deny(ExecutionReasonCode.POLICY_CONFIG_INVALID)
    if active_execution_count >= validated_config.max_concurrent_executions:
        return _deny(ExecutionReasonCode.CONCURRENCY_LIMIT)

    request_data = request.model_dump(mode="python") if isinstance(request, ExecutionRequest) else request
    if isinstance(request_data, Mapping):
        allowed_fields = {"suite_ids", "requested_timeout_seconds"}
        if set(request_data) - allowed_fields:
            return _deny(ExecutionReasonCode.UNSUPPORTED_OVERRIDE)
    try:
        validated_request = request if isinstance(request, ExecutionRequest) else ExecutionRequest.model_validate(request)
    except (TypeError, ValueError, ValidationError):
        return _deny(ExecutionReasonCode.INVALID_REQUEST)

    try:
        validated_environment = environment if isinstance(environment, ExecutionEnvironment) else ExecutionEnvironment.model_validate(environment)
    except (TypeError, ValueError, ValidationError):
        return _deny(ExecutionReasonCode.UNSAFE_ENVIRONMENT)

    definitions: list[SuiteDefinition] = []
    for suite_id in validated_request.suite_ids:
        definition = validated_registry.get(suite_id)
        if definition is None:
            return _deny(ExecutionReasonCode.UNKNOWN_SUITE)
        if not definition.enabled:
            return _deny(ExecutionReasonCode.SUITE_DISABLED)
        definitions.append(definition)

    runners = {definition.runner_service for definition in definitions}
    if len(runners) != 1:
        return _deny(ExecutionReasonCode.INCOMPATIBLE_SUITES)
    runner = definitions[0].runner_service
    if not _environment_matches_runner(validated_environment, runner):
        return _deny(ExecutionReasonCode.UNSAFE_ENVIRONMENT)

    timeout_limit = min(definition.timeout_limit_seconds for definition in definitions)
    requested_timeout = validated_request.requested_timeout_seconds
    if requested_timeout is not None and requested_timeout > timeout_limit:
        return _deny(ExecutionReasonCode.TIMEOUT_EXCEEDED)

    return ExecutionDecision(
        allowed=True,
        reason_code=ExecutionReasonCode.ALLOWED,
        suite_ids=validated_request.suite_ids,
        runner_service=runner,
        selectors=tuple(selector for definition in definitions for selector in definition.selectors),
        timeout_seconds=requested_timeout or timeout_limit,
        max_concurrent_executions=validated_config.max_concurrent_executions,
    )


def _environment_matches_runner(environment: ExecutionEnvironment, runner: SuiteRunner) -> bool:
    # Model literals enforce QA project, test mode, private network, and false
    # development-access flags before these runner-specific checks.
    if runner == SuiteRunner.BACKEND_UNIT:
        return (
            environment.network_profile == "none"
            and environment.database_host is None
            and environment.database_name is None
            and environment.service_base_url is None
        )
    if runner == SuiteRunner.API:
        return (
            environment.network_profile == "qa_app_data"
            and environment.database_host == "qa-mysql"
            and environment.database_name == "nexus_qa_test"
            and environment.service_base_url == "http://api:8000"
        )
    return (
        environment.network_profile == "qa_app"
        and environment.database_host is None
        and environment.database_name is None
        and environment.service_base_url == "http://proxy"
    )


def _deny(reason: ExecutionReasonCode) -> ExecutionDecision:
    return ExecutionDecision(allowed=False, reason_code=reason)
