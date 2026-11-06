import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey


class Customer(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "customers"
    name: Mapped[str] = mapped_column(String(180), index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    contacts: Mapped[list["Contact"]] = relationship(back_populates="customer", cascade="all, delete-orphan")


class Contact(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "contacts"
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    customer: Mapped[Customer] = relationship(back_populates="contacts")
