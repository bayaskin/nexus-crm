from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)


def validate_unique_products(items: list["OrderItemInput"]) -> list["OrderItemInput"]:
    product_ids = [item.product_id for item in items]
    if len(product_ids) != len(set(product_ids)):
        raise ValueError("A product can appear only once in an order")
    return items


class OrderItemInput(BaseModel):
    product_id: UUID
    quantity: int = Field(gt=0, le=1_000_000)


class OrderCreate(BaseModel):
    customer_id: UUID
    items: list[OrderItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_items(self):
        validate_unique_products(self.items)
        return self


class OrderUpdate(BaseModel):
    customer_id: UUID | None = None
    items: list[OrderItemInput] | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")

        if "customer_id" in self.model_fields_set and self.customer_id is None:
            raise ValueError("Customer ID cannot be null")

        if "items" in self.model_fields_set:
            if self.items is None:
                raise ValueError("Items cannot be null")
            validate_unique_products(self.items)

        return self


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    quantity: int
    unit_price: Decimal

    @computed_field
    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    status: str
    total: Decimal
    items: list[OrderItemResponse]
    created_at: datetime
    updated_at: datetime


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    total: int
    skip: int
    limit: int
