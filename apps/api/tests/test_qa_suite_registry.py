from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.services.qa_suite_registry import (
    DEFAULT_EXECUTION_POLICY_CONFIG,
    DEFAULT_SUITE_REGISTRY,
    ExecutionEnvironment,
    ExecutionReasonCode,
    SuiteDefinition,
    SuiteRegistry,
    SuiteRunner,
    evaluate_execution_policy,
)


def _environment(runner: str = "api") -> dict[str, object]:
    base: dict[str, object] = {
        "app_env": "test",
        "qa_mode": True,
        "compose_project": "nexus-qa",
        "network_internal": True,
        "development_database_access": False,
        "development_service_access": False,
    }
    if runner == "unit":
        base.update(network_profile="none")
    elif runner == "ui":
        base.update(network_profile="qa_app", service_base_url="http://proxy")
    else:
        base.update(
            network_profile="qa_app_data",
            database_host="qa-mysql",
            database_name="nexus_qa_test",
            service_base_url="http://api:8000",
        )
    return base


def _request(*suite_ids: str, **extra: object) -> dict[str, object]:
    return {"suite_ids": list(suite_ids), **extra}


def test_trusted_registry_contains_expected_stable_suites_and_fixed_selectors():
    assert DEFAULT_SUITE_REGISTRY.suite_ids == (
        "api-auth",
        "api-regression",
        "ui-regression",
        "unit-backend",
    )
    decision = evaluate_execution_policy(
        _request("unit-backend"), _environment("unit"), active_execution_count=0
    )
    assert decision.allowed is True
    assert decision.reason_code == ExecutionReasonCode.ALLOWED
    assert decision.runner_service == "qa-unit-tests"
    assert decision.selectors == (
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
    )
    assert not hasattr(decision, "command")


@pytest.mark.parametrize(
    ("suite_id", "environment", "runner"),
    [
        ("api-auth", _environment("api"), "api"),
        ("api-regression", _environment("api"), "api"),
        ("ui-regression", _environment("ui"), "ui"),
    ],
)
def test_known_suites_are_allowed_only_in_their_isolated_qa_profile(suite_id, environment, runner):
    decision = evaluate_execution_policy(
        _request(suite_id), environment, active_execution_count=0
    )
    assert decision.allowed is True
    assert decision.runner_service.value == {"api": "api-tests", "ui": "ui-tests"}[runner]
    assert decision.selectors


def test_unknown_suite_is_denied_without_echoing_untrusted_value():
    hostile = "../../nexus_crm; drop database"
    decision = evaluate_execution_policy(
        _request(hostile), _environment(), active_execution_count=0
    )
    assert decision.allowed is False
    assert decision.reason_code == ExecutionReasonCode.INVALID_REQUEST
    assert hostile not in str(decision.model_dump())

    unknown = evaluate_execution_policy(
        _request("api-invented"), _environment(), active_execution_count=0
    )
    assert unknown.reason_code == ExecutionReasonCode.UNKNOWN_SUITE


def test_disabled_suite_is_denied():
    definitions = [item.model_dump(mode="python") for item in (
        SuiteDefinition(
            suite_id="disabled-api",
            description="Disabled regression suite.",
            runner_service=SuiteRunner.API,
            selectors=("tests/test_auth_contract.py",),
            timeout_limit_seconds=60,
            enabled=False,
        ),
    )]
    decision = evaluate_execution_policy(
        _request("disabled-api"), _environment(), active_execution_count=0,
        registry=SuiteRegistry(definitions),
    )
    assert decision.allowed is False
    assert decision.reason_code == ExecutionReasonCode.SUITE_DISABLED


@pytest.mark.parametrize(
    "payload",
    [
        {"suite_ids": ["api-auth"], "command": "pytest tests/evil.py"},
        {"suite_ids": ["api-auth"], "selector": "../../nexus_crm"},
        {"suite_ids": ["api-auth"], "environment_overrides": {"DATABASE_URL": "..."}},
        {"suite_ids": ["api-auth"], "docker_args": ["--network=host"]},
    ],
)
def test_command_selector_and_environment_override_attempts_are_denied(payload):
    decision = evaluate_execution_policy(payload, _environment(), active_execution_count=0)
    assert decision.allowed is False
    assert decision.reason_code == ExecutionReasonCode.UNSUPPORTED_OVERRIDE
    assert decision.selectors == ()
    assert decision.runner_service is None


def test_invalid_or_development_environment_is_denied_without_echoing_values():
    for changes in (
        {"compose_project": "nexus-development"},
        {"database_name": "nexus_crm"},
        {"database_host": "mysql"},
        {"development_database_access": True},
        {"network_internal": False},
        {"service_base_url": "https://production.example"},
    ):
        environment = {**_environment(), **changes}
        decision = evaluate_execution_policy(
            _request("api-auth"), environment, active_execution_count=0
        )
        assert decision.allowed is False
        assert decision.reason_code == ExecutionReasonCode.UNSAFE_ENVIRONMENT
        assert "nexus_crm" not in str(decision.model_dump())

    with_secret = {**_environment(), "DATABASE_URL": "mysql://user:secret@host/db"}
    decision = evaluate_execution_policy(
        _request("api-auth"), with_secret, active_execution_count=0
    )
    assert decision.reason_code == ExecutionReasonCode.UNSAFE_ENVIRONMENT
    assert "secret" not in str(decision.model_dump())


def test_timeout_and_concurrency_limits_are_enforced():
    too_long = evaluate_execution_policy(
        _request("api-auth", requested_timeout_seconds=601),
        _environment(),
        active_execution_count=0,
    )
    assert too_long.reason_code == ExecutionReasonCode.TIMEOUT_EXCEEDED

    at_limit = evaluate_execution_policy(
        _request("api-auth", requested_timeout_seconds=600),
        _environment(),
        active_execution_count=0,
    )
    assert at_limit.allowed is True
    assert at_limit.timeout_seconds == 600

    busy = evaluate_execution_policy(
        _request("api-auth"), _environment(), active_execution_count=2
    )
    assert busy.reason_code == ExecutionReasonCode.CONCURRENCY_LIMIT


def test_missing_or_malformed_registry_and_policy_configuration_fail_closed():
    assert evaluate_execution_policy(
        _request("api-auth"), _environment(), active_execution_count=0, registry=None
    ).reason_code == ExecutionReasonCode.REGISTRY_INVALID
    assert evaluate_execution_policy(
        _request("api-auth"), _environment(), active_execution_count=0, registry=[]
    ).reason_code == ExecutionReasonCode.REGISTRY_INVALID
    assert evaluate_execution_policy(
        _request("api-auth"), _environment(), active_execution_count=0,
        config={"max_concurrent_executions": 0},
    ).reason_code == ExecutionReasonCode.POLICY_CONFIG_INVALID
    with pytest.raises((ValueError, ValidationError)):
        SuiteRegistry([{
            "suite_id": "unsafe-suite",
            "description": "Malformed selector",
            "runner_service": "api-tests",
            "selectors": ["../../etc/passwd"],
            "timeout_limit_seconds": 60,
            "enabled": True,
        }])


def test_same_request_is_deterministic_independent_of_suite_order():
    kwargs = {"environment": _environment(), "active_execution_count": 0}
    first = evaluate_execution_policy(_request("api-regression", "api-auth"), **kwargs)
    second = evaluate_execution_policy(_request("api-auth", "api-regression"), **kwargs)
    assert first == second
    assert first.suite_ids == ("api-auth", "api-regression")


def test_default_registry_and_candidate_mapping_cannot_be_mutated():
    from app.services.qa_suite_registry import CANDIDATE_SUITE_EXECUTION_MAP

    with pytest.raises(AttributeError):
        DEFAULT_SUITE_REGISTRY._definitions = {}  # type: ignore[misc]
    with pytest.raises(TypeError):
        CANDIDATE_SUITE_EXECUTION_MAP["apps/api/tests/attacker.py"] = ("api-auth",)  # type: ignore[index]


def test_incompatible_runner_suites_are_denied_and_invalid_environment_shape_fails_closed():
    mixed = evaluate_execution_policy(
        _request("api-auth", "ui-regression"), _environment(), active_execution_count=0
    )
    assert mixed.reason_code == ExecutionReasonCode.INCOMPATIBLE_SUITES

    missing = deepcopy(_environment())
    missing.pop("network_profile")
    assert evaluate_execution_policy(
        _request("api-auth"), missing, active_execution_count=0
    ).reason_code == ExecutionReasonCode.UNSAFE_ENVIRONMENT


def test_environment_contract_is_strict_and_does_not_accept_database_url():
    with pytest.raises(ValidationError):
        ExecutionEnvironment.model_validate({**_environment(), "DATABASE_URL": "secret"})
    assert DEFAULT_EXECUTION_POLICY_CONFIG.max_concurrent_executions == 2
