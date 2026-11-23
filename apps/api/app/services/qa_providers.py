from typing import Protocol

from app.schemas.qa import FoundationContext, FoundationPlan


class AIProvider(Protocol):
    """Text-only planning interface; providers never receive command access."""

    async def create_foundation_plan(self, context: FoundationContext) -> FoundationPlan:
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
