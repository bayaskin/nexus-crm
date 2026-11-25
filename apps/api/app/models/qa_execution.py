from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPrimaryKey


class QAExecutionCapacity(Base):
    """Single durable counter row serializing all QA admission claims."""

    __tablename__ = "qa_execution_capacity"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_qa_execution_capacity_singleton"),
        CheckConstraint(
            "active_count >= 0 and max_concurrent_executions between 1 and 32 "
            "and active_count <= max_concurrent_executions",
            name="ck_qa_execution_capacity_bounds",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    active_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_concurrent_executions: Mapped[int] = mapped_column(Integer, nullable=False)


class QAExecutionAdmission(UUIDPrimaryKey, Base):
    """Auditable suite admission and durable reservation lifecycle record."""

    __tablename__ = "qa_execution_admissions"
    __table_args__ = (
        CheckConstraint(
            "state in ('reserved', 'running', 'completed', 'failed', 'cancelled', 'expired', 'denied')",
            name="ck_qa_execution_admissions_state",
        ),
        Index("ix_qa_execution_admissions_state_expiry", "state", "reserved_until"),
        Index("ix_qa_execution_admissions_run_created", "qa_run_id", "created_at"),
        Index("ix_qa_execution_admissions_run_suite_state", "qa_run_id", "suite_id", "state"),
        Index(
            "uq_qa_execution_admissions_active_run_suite",
            "qa_run_id",
            "suite_id",
            "active_slot",
            unique=True,
        ),
    )

    suite_id: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True, native_uuid=False), ForeignKey("users.id", ondelete="SET NULL")
    )
    qa_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=False), ForeignKey("qa_runs.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    # Active rows share slot 1; terminal rows are NULL, which permits any
    # number of historical admissions under MySQL's unique-index semantics.
    active_slot: Mapped[int | None] = mapped_column(
        Integer,
        Computed("CASE WHEN state IN ('reserved', 'running') THEN 1 ELSE NULL END", persisted=True),
        nullable=True,
    )
    reason_code: Mapped[str | None] = mapped_column(String(64))
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
