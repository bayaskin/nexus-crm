from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


ALLOWED_REASONS = {
    "purchase_receipt",
    "customer_return",
    "damaged",
    "lost",
    "correction",
    "initial_stock",
    "other",
}


class InventoryAdjustmentCreate(BaseModel):
    product_id: UUID
    quantity_change: int = Field(
        description="Positive to add stock, negative to remove stock. Zero is invalid.",
    )
    reason: str = Field(min_length=1, max_length=80)
    reference_id: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=500)
    reorder_level: int | None = Field(default=None, ge=0)

    @field_validator("quantity_change")
    @classmethod
    def validate_quantity_change(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Quantity change cannot be zero")
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ALLOWED_REASONS:
            raise ValueError(
                f"Reason must be one of: {', '.join(sorted(ALLOWED_REASONS))}"
            )
        return value

    @field_validator("reference_id", "notes")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class InventoryReorderLevelUpdate(BaseModel):
    reorder_level: int = Field(ge=0)


class InventoryBalanceResponse(BaseModel):
    product_id: UUID
    sku: str
    product_name: str
    warehouse_id: UUID
    warehouse_code: str
    quantity_on_hand: int
    reorder_level: int
    is_low_stock: bool
    updated_at: datetime


class InventoryListResponse(BaseModel):
    items: list[InventoryBalanceResponse]
    total: int
    skip: int
    limit: int


class InventoryMovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    warehouse_id: UUID
    product_id: UUID
    quantity_change: int
    reason: str
    reference_id: str | None
    notes: str | None
    created_at: datetime


class InventoryAdjustmentResponse(BaseModel):
    movement: InventoryMovementResponse
    balance: InventoryBalanceResponse