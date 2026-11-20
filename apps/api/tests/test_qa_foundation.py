import asyncio
import os
from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "qa-foundation-test-signing-key-2026-abcdef")

from app.schemas.qa import CreateQARunRequest, QARunState, QARunStatus  # noqa: E402
from app.services.qa_orchestrator import InvalidQARunTransition, transition_status  # noqa: E402
from app.services.qa_providers import MockAIProvider  # noqa: E402


def test_run_config_validates_timeout_and_rejects_unrecognized_fields():
    assert CreateQARunRequest().config.timeout_seconds == 30
    assert CreateQARunRequest(config={"timeout_seconds": 4}).config.timeout_seconds == 4
    with pytest.raises(ValidationError):
        CreateQARunRequest(config={"timeout_seconds": 0})
    with pytest.raises(ValidationError):
        CreateQARunRequest(extra_input="not supported")


def test_status_values_and_state_serialization_round_trip():
    state = QARunState(
        run_id=uuid4(), trigger_type="manual", status=QARunStatus.PENDING,
        progress=0, config={"timeout_seconds": 15},
    )
    encoded = state.model_dump(mode="json")
    decoded = QARunState.model_validate_json(state.model_dump_json())
    assert decoded == state
    assert encoded["status"] == "pending"
    assert {status.value for status in QARunStatus} == {
        "pending", "initializing", "analyzing", "planning", "running_checks",
        "generating_report", "completed", "failed", "cancelled", "timed_out",
    }


def test_status_transitions_enforce_lifecycle():
    assert transition_status(QARunStatus.PENDING, QARunStatus.INITIALIZING) == QARunStatus.INITIALIZING
    assert transition_status(QARunStatus.GENERATING_REPORT, QARunStatus.COMPLETED) == QARunStatus.COMPLETED
    with pytest.raises(InvalidQARunTransition):
        transition_status(QARunStatus.PENDING, QARunStatus.COMPLETED)
    with pytest.raises(InvalidQARunTransition):
        transition_status(QARunStatus.COMPLETED, QARunStatus.CANCELLED)


def test_mock_provider_returns_structured_non_claiming_output():
    from app.schemas.qa import FoundationContext

    plan = asyncio.run(MockAIProvider().create_foundation_plan(FoundationContext()))
    assert plan.provider == "mock"
    assert plan.analysis_performed is False
    assert "no repository or pull request was analyzed" in plan.summary.lower()
