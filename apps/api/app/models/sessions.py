from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey


class UserSession(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_user_id", "user_id"),
        Index("ix_user_sessions_expires_at", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    replaced_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True, native_uuid=False), ForeignKey("user_sessions.id", ondelete="SET NULL")
    )
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip_address: Mapped[str | None] = mapped_column(String(45))
