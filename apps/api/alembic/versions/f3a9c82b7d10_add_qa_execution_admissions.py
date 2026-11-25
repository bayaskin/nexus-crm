"""Add trusted QA execution admission records and capacity counter.

Revision ID: f3a9c82b7d10
Revises: d620a8e4f19b
Create Date: 2026-11-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a9c82b7d10"
down_revision: Union[str, None] = "d620a8e4f19b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qa_execution_capacity",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("active_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_concurrent_executions", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_qa_execution_capacity_singleton"),
        sa.CheckConstraint(
            "active_count >= 0 and max_concurrent_executions between 1 and 32 "
            "and active_count <= max_concurrent_executions",
            name="ck_qa_execution_capacity_bounds",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        sa.table(
            "qa_execution_capacity",
            sa.column("id", sa.Integer()),
            sa.column("active_count", sa.Integer()),
            sa.column("max_concurrent_executions", sa.Integer()),
        ),
        [{"id": 1, "active_count": 0, "max_concurrent_executions": 2}],
    )
    op.create_table(
        "qa_execution_admissions",
        sa.Column("suite_id", sa.String(length=64), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(native_uuid=False), nullable=True),
        sa.Column("qa_run_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("reserved_until", sa.DateTime(timezone=False), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.CheckConstraint(
            "state in ('reserved', 'running', 'completed', 'failed', 'cancelled', 'expired', 'denied')",
            name="ck_qa_execution_admissions_state",
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["qa_run_id"], ["qa_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_qa_execution_admissions_state_expiry",
        "qa_execution_admissions",
        ["state", "reserved_until"],
    )
    op.create_index(
        "ix_qa_execution_admissions_run_created",
        "qa_execution_admissions",
        ["qa_run_id", "created_at"],
    )
    op.create_index(
        "ix_qa_execution_admissions_run_suite_state",
        "qa_execution_admissions",
        ["qa_run_id", "suite_id", "state"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    admission_count = bind.execute(sa.text("SELECT count(*) FROM qa_execution_admissions")).scalar_one()
    if admission_count:
        raise RuntimeError(
            "Cannot downgrade QA execution admissions while admission history exists; "
            "export the history before removing it and retrying the downgrade."
        )
    active_count = bind.execute(
        sa.text("SELECT active_count FROM qa_execution_capacity WHERE id = 1")
    ).scalar_one_or_none()
    if active_count not in (None, 0):
        raise RuntimeError("Cannot downgrade QA execution admissions while capacity is reserved.")
    op.drop_index("ix_qa_execution_admissions_run_created", table_name="qa_execution_admissions")
    op.drop_index("ix_qa_execution_admissions_run_suite_state", table_name="qa_execution_admissions")
    op.drop_index("ix_qa_execution_admissions_state_expiry", table_name="qa_execution_admissions")
    op.drop_table("qa_execution_admissions")
    op.drop_table("qa_execution_capacity")
