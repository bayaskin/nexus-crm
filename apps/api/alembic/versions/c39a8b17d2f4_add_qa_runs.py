"""Add persistent Agentic QA runs.

Revision ID: c39a8b17d2f4
Revises: b14c4e28a901
Create Date: 2026-11-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c39a8b17d2f4"
down_revision: Union[str, None] = "b14c4e28a901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qa_runs",
        sa.Column("requested_by_user_id", sa.Uuid(native_uuid=False), nullable=True),
        sa.Column("trigger_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("input_config", sa.JSON(), nullable=False),
        sa.Column("state_data", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("started_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'initializing', 'analyzing', 'planning', 'running_checks', "
            "'generating_report', 'completed', 'failed', 'cancelled', 'timed_out')",
            name="ck_qa_runs_status",
        ),
        sa.CheckConstraint("progress >= 0 and progress <= 100", name="ck_qa_runs_progress"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qa_runs_status_created_at", "qa_runs", ["status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_qa_runs_status_created_at", table_name="qa_runs")
    op.drop_table("qa_runs")
