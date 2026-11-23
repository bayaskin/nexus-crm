from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKey


class QARun(UUIDPrimaryKey, Base):
    __tablename__ = "qa_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'initializing', 'analyzing', 'planning', 'running_checks', "
            "'generating_report', 'completed', 'failed', 'cancelled', 'timed_out', 'superseded')",
            name="ck_qa_runs_status",
        ),
        CheckConstraint("progress >= 0 and progress <= 100", name="ck_qa_runs_progress"),
        Index("ix_qa_runs_status_created_at", "status", "created_at"),
        UniqueConstraint("github_revision_id", name="uq_qa_runs_github_revision_occurrence"),
        UniqueConstraint("github_delivery_id", name="uq_qa_runs_github_delivery"),
    )

    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True, native_uuid=False), ForeignKey("users.id", ondelete="SET NULL")
    )
    trigger_type: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    input_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    state_data: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[dict | None] = mapped_column(JSON)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    github_repository_id: Mapped[int | None] = mapped_column(BigInteger)
    github_pr_number: Mapped[int | None] = mapped_column(Integer)
    github_head_sha: Mapped[str | None] = mapped_column(String(40))
    github_delivery_id: Mapped[str | None] = mapped_column(String(128))
    github_context: Mapped[dict | None] = mapped_column(JSON)
    github_revision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("github_pr_revisions.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
