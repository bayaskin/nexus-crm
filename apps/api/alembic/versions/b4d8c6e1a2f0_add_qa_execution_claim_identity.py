"""Persist trusted QA execution claim identity.

Revision ID: b4d8c6e1a2f0
Revises: e8f6c2a7145b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4d8c6e1a2f0"
down_revision: Union[str, None] = "e8f6c2a7145b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "qa_execution_admissions",
        sa.Column("execution_attempt_id", sa.Uuid(native_uuid=False), nullable=True),
    )
    op.add_column(
        "qa_execution_admissions",
        sa.Column("worker_id", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_qa_execution_admissions_attempt_identity",
        "qa_execution_admissions",
        "(execution_attempt_id is null and worker_id is null) or "
        "(execution_attempt_id is not null and worker_id is not null and started_at is not null)",
    )
    op.create_index(
        "uq_qa_execution_admissions_attempt_id",
        "qa_execution_admissions",
        ["execution_attempt_id"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    claim_count = bind.execute(
        sa.text(
            "SELECT count(*) FROM qa_execution_admissions "
            "WHERE execution_attempt_id IS NOT NULL OR worker_id IS NOT NULL"
        )
    ).scalar_one()
    if claim_count:
        raise RuntimeError(
            "Cannot downgrade QA execution claim identity while execution attempt history exists. "
            "Export or retain that history before retrying the downgrade."
        )
    op.drop_index("uq_qa_execution_admissions_attempt_id", table_name="qa_execution_admissions")
    op.drop_constraint(
        "ck_qa_execution_admissions_attempt_identity", "qa_execution_admissions", type_="check"
    )
    op.drop_column("qa_execution_admissions", "worker_id")
    op.drop_column("qa_execution_admissions", "execution_attempt_id")
