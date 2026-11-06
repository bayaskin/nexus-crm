import uuid

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey


class InventoryMovement(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_movements"
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True, native_uuid=False), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    quantity_change: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(String(500))
