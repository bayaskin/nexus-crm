from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GitHubPullRequestState(Base):
    """Serializes latest-revision decisions for a repository pull request."""

    __tablename__ = "github_pull_request_states"

    repository_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pull_request_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    latest_head_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latest_base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latest_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    next_request_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    latest_observation_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GitHubPRRevision(Base):
    """One observed occurrence of a PR head revision (A→B→A has 3 rows)."""

    __tablename__ = "github_pr_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pull_request_number: Mapped[int] = mapped_column(Integer, nullable=False)
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)


class GitHubWebhookDelivery(Base):
    __tablename__ = "github_webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    repository_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pull_request_number: Mapped[int] = mapped_column(Integer, nullable=False)
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    qa_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("qa_runs.id", ondelete="SET NULL"), nullable=True)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False, default="accepted")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
