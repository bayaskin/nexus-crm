from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey

user_roles = Table(
    "user_roles", Base.metadata,
    Column("user_id", Uuid(as_uuid=True, native_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Uuid(as_uuid=True, native_uuid=False), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)
role_permissions = Table(
    "role_permissions", Base.metadata,
    Column("role_id", Uuid(as_uuid=True, native_uuid=False), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", Uuid(as_uuid=True, native_uuid=False), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class User(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(80))
    last_name: Mapped[str | None] = mapped_column(String(80))
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    password_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    roles: Mapped[list["Role"]] = relationship(secondary=user_roles, back_populates="users", lazy="selectin")
    permission_overrides: Mapped[list["UserPermissionOverride"]] = relationship(back_populates="user", cascade="all, delete-orphan", lazy="selectin")


class UserPermissionOverride(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "user_permission_overrides"
    __table_args__ = (UniqueConstraint("user_id", "permission_id", name="uq_user_permission_override"),)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    permission_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False)
    effect: Mapped[str] = mapped_column(String(10), nullable=False)
    user: Mapped[User] = relationship(back_populates="permission_overrides")
    permission: Mapped["Permission"] = relationship(lazy="joined")


class Role(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    users: Mapped[list[User]] = relationship(secondary=user_roles, back_populates="roles")
    permissions: Mapped[list["Permission"]] = relationship(secondary=role_permissions, back_populates="roles", lazy="selectin")


class Permission(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("resource", "action", name="uq_permissions_resource_action"),)
    resource: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    roles: Mapped[list[Role]] = relationship(secondary=role_permissions, back_populates="permissions")
