from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKey


class QARun(UUIDPrimaryKey, Base):
    __tablename__ = "qa_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'initializing', 'analyzing', 'planning', 'running_checks', "
            "'generating_report', 'completed', 'failed', 'cancelled', 'timed_out')",
            name="ck_qa_runs_status",
        ),
        CheckConstraint("progress >= 0 and progress <= 100", name="ck_qa_runs_progress"),
        Index("ix_qa_runs_status_created_at", "status", "created_at"),
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
