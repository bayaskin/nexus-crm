from typing import Protocol

from app.schemas.qa import FoundationContext, FoundationPlan


class AIProvider(Protocol):
    """Text-only planning interface; providers never receive command access."""

    async def create_foundation_plan(self, context: FoundationContext) -> FoundationPlan:
        ...


class MockAIProvider:
    """Deterministic provider that works offline and makes no external calls."""

    async def create_foundation_plan(self, context: FoundationContext) -> FoundationPlan:
        del context
        return FoundationPlan(
            summary="Mock provider is ready; no repository or pull request was analyzed.",
            next_step="Add a controlled pull request context collection stage in a later milestone.",
        )
