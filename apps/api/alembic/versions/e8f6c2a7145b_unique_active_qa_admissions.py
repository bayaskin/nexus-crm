"""Enforce one active suite admission per QA run.

Revision ID: e8f6c2a7145b
Revises: f3a9c82b7d10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f6c2a7145b"
down_revision: Union[str, None] = "f3a9c82b7d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACTIVE_ADMISSION_INDEX = "uq_qa_execution_admissions_active_run_suite"


def upgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            "SELECT qa_run_id, suite_id FROM qa_execution_admissions "
            "WHERE state IN ('reserved', 'running') "
            "GROUP BY qa_run_id, suite_id HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot enforce unique active QA admissions while duplicate active run/suite pairs exist. "
            "Resolve the duplicates before retrying the migration."
        )

    op.add_column(
        "qa_execution_admissions",
        sa.Column(
            "active_slot",
            sa.Integer(),
            sa.Computed("CASE WHEN state IN ('reserved', 'running') THEN 1 ELSE NULL END", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        ACTIVE_ADMISSION_INDEX,
        "qa_execution_admissions",
        ["qa_run_id", "suite_id", "active_slot"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(ACTIVE_ADMISSION_INDEX, table_name="qa_execution_admissions")
    op.drop_column("qa_execution_admissions", "active_slot")
