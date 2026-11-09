from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalog import Product
from app.models.inventory import (
    InventoryBalance,
    InventoryMovement,
    Warehouse,
)
from app.schemas.inventory import (
    InventoryAdjustmentCreate,
    InventoryAdjustmentResponse,
    InventoryBalanceResponse,
    InventoryMovementResponse,
)


def get_main_warehouse(db: Session) -> Warehouse:
    warehouse = db.scalar(
        select(Warehouse).where(
            Warehouse.code == "MAIN",
            Warehouse.is_active.is_(True),
        )
    )

    if warehouse is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Main warehouse is unavailable",
        )

    return warehouse


def get_balance(
    db: Session,
    product_id: UUID,
    warehouse_id: UUID,
) -> InventoryBalance:
    balance = db.scalar(
        select(InventoryBalance).where(
            InventoryBalance.product_id == product_id,
            InventoryBalance.warehouse_id == warehouse_id,
        )
    )

    if balance is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventory balance not found",
        )

    return balance


def serialize_balance(
    balance: InventoryBalance,
    product: Product,
    warehouse: Warehouse,
) -> InventoryBalanceResponse:
    return InventoryBalanceResponse(
        product_id=product.id,
        sku=product.sku,
        product_name=product.name,
        warehouse_id=warehouse.id,
        warehouse_code=warehouse.code,
        quantity_on_hand=balance.quantity_on_hand,
        reorder_level=balance.reorder_level,
        is_low_stock=balance.quantity_on_hand <= balance.reorder_level,
        updated_at=balance.updated_at,
    )


def adjust_stock(
    db: Session,
    payload: InventoryAdjustmentCreate,
) -> InventoryAdjustmentResponse:
    warehouse = get_main_warehouse(db)

    product = db.get(Product, payload.product_id)

    if product is None or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active product not found",
        )

    # Lock the balance row to serialize concurrent adjustments.
    balance = db.scalar(
        select(InventoryBalance)
        .where(
            InventoryBalance.product_id == payload.product_id,
            InventoryBalance.warehouse_id == warehouse.id,
        )
        .with_for_update()
    )

    if balance is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inventory balance is missing for this product",
        )

    new_quantity = balance.quantity_on_hand + payload.quantity_change

    if new_quantity < 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Insufficient stock. "
                f"Available: {balance.quantity_on_hand}, "
                f"requested change: {payload.quantity_change}"
            ),
        )

    try:
        balance.quantity_on_hand = new_quantity

        if payload.reorder_level is not None:
            balance.reorder_level = payload.reorder_level

        movement = InventoryMovement(
            warehouse_id=warehouse.id,
            product_id=product.id,
            quantity_change=payload.quantity_change,
            reason=payload.reason,
            reference_id=payload.reference_id,
            notes=payload.notes,
        )

        db.add(movement)
        db.commit()

        db.refresh(balance)
        db.refresh(movement)

    except Exception:
        db.rollback()
        raise

    return InventoryAdjustmentResponse(
        movement=InventoryMovementResponse.model_validate(movement),
        balance=serialize_balance(balance, product, warehouse),
    )