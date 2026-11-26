"""Internal, data-only contract passed to a future trusted QA runner."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RunnerServiceId(StrEnum):
    BACKEND_UNIT = "qa-unit-tests"
    API = "api-tests"
    UI = "ui-tests"


class TrustedRunnerContract(BaseModel):
    """Server-derived launch description; intentionally contains no commands or paths."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    admission_id: UUID
    qa_run_id: UUID
    suite_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$", max_length=64)
    runner_service: RunnerServiceId
    selector_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$", max_length=64)
    effective_timeout_seconds: int = Field(ge=1, le=3600)
    environment_identity: Literal["nexus-qa"]
    execution_attempt_id: UUID
