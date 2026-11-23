"""Add immutable GitHub pull request context to QA runs.

Revision ID: d620a8e4f19b
Revises: c39a8b17d2f4
Create Date: 2026-11-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d620a8e4f19b"
down_revision: Union[str, None] = "c39a8b17d2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_pull_request_states",
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("latest_head_sha", sa.String(length=40), nullable=True),
        sa.Column("latest_base_sha", sa.String(length=40), nullable=True),
        sa.Column("latest_event_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("next_request_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("latest_observation_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("repository_id", "pull_request_number"),
    )
    op.create_table(
        "github_pr_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("base_sha", sa.String(length=40), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("observed_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "github_webhook_deliveries",
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("base_sha", sa.String(length=40), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("qa_run_id", sa.Uuid(native_uuid=False), nullable=True),
        sa.Column("disposition", sa.String(length=32), nullable=False, server_default="accepted"),
        sa.Column("received_at", sa.DateTime(timezone=False), nullable=False),
        sa.ForeignKeyConstraint(["qa_run_id"], ["qa_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("delivery_id"),
    )
    op.add_column("qa_runs", sa.Column("github_repository_id", sa.BigInteger(), nullable=True))
    op.add_column("qa_runs", sa.Column("github_pr_number", sa.Integer(), nullable=True))
    op.add_column("qa_runs", sa.Column("github_head_sha", sa.String(length=40), nullable=True))
    op.add_column("qa_runs", sa.Column("github_delivery_id", sa.String(length=128), nullable=True))
    op.add_column("qa_runs", sa.Column("github_context", sa.JSON(), nullable=True))
    op.add_column("qa_runs", sa.Column("github_revision_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_qa_runs_github_revision_id_github_pr_revisions",
        "qa_runs",
        "github_pr_revisions",
        ["github_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_qa_runs_github_revision_occurrence",
        "qa_runs",
        ["github_revision_id"],
    )
    op.create_unique_constraint("uq_qa_runs_github_delivery", "qa_runs", ["github_delivery_id"])
    op.execute("ALTER TABLE qa_runs DROP CHECK ck_qa_runs_status")
    op.execute(
        "ALTER TABLE qa_runs ADD CONSTRAINT ck_qa_runs_status CHECK "
        "(status in ('pending', 'initializing', 'analyzing', 'planning', 'running_checks', "
        "'generating_report', 'completed', 'failed', 'cancelled', 'timed_out', 'superseded'))"
    )


def downgrade() -> None:
    bind = op.get_bind()
    github_run_count = bind.execute(
        sa.text("SELECT count(*) FROM qa_runs WHERE trigger_type = 'github_pull_request'")
    ).scalar_one()
    delivery_count = bind.execute(sa.text("SELECT count(*) FROM github_webhook_deliveries")).scalar_one()
    revision_count = bind.execute(sa.text("SELECT count(*) FROM github_pr_revisions")).scalar_one()
    state_count = bind.execute(sa.text("SELECT count(*) FROM github_pull_request_states")).scalar_one()
    if github_run_count or delivery_count or revision_count or state_count:
        raise RuntimeError(
            "Cannot downgrade GitHub PR ingestion while run, delivery, revision, or PR state history exists; "
            "export the history before removing it and retrying the downgrade."
        )

    op.execute("UPDATE qa_runs SET status = 'cancelled' WHERE status = 'superseded'")
    op.execute("ALTER TABLE qa_runs DROP CHECK ck_qa_runs_status")
    op.execute(
        "ALTER TABLE qa_runs ADD CONSTRAINT ck_qa_runs_status CHECK "
        "(status in ('pending', 'initializing', 'analyzing', 'planning', 'running_checks', "
        "'generating_report', 'completed', 'failed', 'cancelled', 'timed_out'))"
    )
    op.drop_constraint("uq_qa_runs_github_delivery", "qa_runs", type_="unique")
    op.drop_constraint("fk_qa_runs_github_revision_id_github_pr_revisions", "qa_runs", type_="foreignkey")
    op.drop_constraint("uq_qa_runs_github_revision_occurrence", "qa_runs", type_="unique")
    op.drop_table("github_webhook_deliveries")
    op.drop_column("qa_runs", "github_context")
    op.drop_column("qa_runs", "github_revision_id")
    op.drop_column("qa_runs", "github_delivery_id")
    op.drop_column("qa_runs", "github_head_sha")
    op.drop_column("qa_runs", "github_pr_number")
    op.drop_column("qa_runs", "github_repository_id")
    op.drop_table("github_pull_request_states")
    op.drop_table("github_pr_revisions")
