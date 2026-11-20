"""Add user lifecycle metadata and explicit permission overrides."""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "b14c4e28a901"
down_revision: Union[str, None] = "2d1d3667fbec"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(length=80), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(length=80), nullable=True))
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("password_expires_at", sa.DateTime(timezone=False), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=False), nullable=True))
    op.create_table(
        "user_permission_overrides",
        sa.Column("user_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("permission_id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("effect", sa.String(10), nullable=False),
        sa.Column("id", sa.Uuid(native_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # Matches TimestampMixin: the server default initializes this column;
        # its ORM onupdate expression advances it whenever the row is modified.
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "permission_id", name="uq_user_permission_override"),
        sa.CheckConstraint("effect in ('allow', 'deny')", name="ck_user_permission_override_effect"),
    )
    op.create_index("ix_user_permission_overrides_user_id", "user_permission_overrides", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_permission_overrides_user_id", table_name="user_permission_overrides")
    op.drop_table("user_permission_overrides")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "password_expires_at")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
