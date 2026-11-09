import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey


class Warehouse(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(
        String(40),
        unique=True,
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    balances: Mapped[list["InventoryBalance"]] = relationship(
        back_populates="warehouse",
    )


class InventoryBalance(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_balances"

    __table_args__ = (
        UniqueConstraint(
            "warehouse_id",
            "product_id",
            name="uq_inventory_balance_warehouse_product",
        ),
        CheckConstraint(
            "quantity_on_hand >= 0",
            name="ck_inventory_balance_nonnegative",
        ),
        CheckConstraint(
            "reorder_level >= 0",
            name="ck_inventory_balance_reorder_nonnegative",
        ),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=False),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=False),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity_on_hand: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    reorder_level: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    warehouse: Mapped[Warehouse] = relationship(
        back_populates="balances",
    )


class InventoryMovement(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "inventory_movements"

    __table_args__ = (
        CheckConstraint(
            "quantity_change != 0",
            name="ck_inventory_movement_nonzero",
        ),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=False),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True, native_uuid=False),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity_change: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(String(500))