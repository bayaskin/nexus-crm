from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.catalog import Product
from app.models.identity import User
from app.models.inventory import (
    InventoryBalance,
    InventoryMovement,
)
from app.schemas.inventory import (
    InventoryAdjustmentCreate,
    InventoryAdjustmentResponse,
    InventoryBalanceResponse,
    InventoryListResponse,
    InventoryMovementResponse,
    InventoryReorderLevelUpdate,
)
from app.services.inventory import (
    adjust_stock,
    get_balance,
    get_main_warehouse,
    serialize_balance,
)

router = APIRouter(prefix="/v1/inventory", tags=["inventory"])


@router.get("", response_model=InventoryListResponse)
def list_inventory(
    search: str | None = Query(default=None, max_length=180),
    low_stock_only: bool = False,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("inventory", "read")),
):
    warehouse = get_main_warehouse(db)

    filters = [
        InventoryBalance.warehouse_id == warehouse.id,
        Product.is_active.is_(True),
    ]

    if search and search.strip():
        term = f"%{search.strip()}%"
        filters.append(
            (Product.sku.ilike(term)) | (Product.name.ilike(term))
        )

    if low_stock_only:
        filters.append(
            InventoryBalance.quantity_on_hand
            <= InventoryBalance.reorder_level
        )

    total = db.scalar(
        select(func.count())
        .select_from(InventoryBalance)
        .join(Product, Product.id == InventoryBalance.product_id)
        .where(*filters)
    ) or 0

    rows = db.execute(
        select(InventoryBalance, Product)
        .join(Product, Product.id == InventoryBalance.product_id)
        .where(*filters)
        .order_by(Product.name.asc())
        .offset(skip)
        .limit(limit)
    ).all()

    items = [
        serialize_balance(balance, product, warehouse)
        for balance, product in rows
    ]

    return InventoryListResponse(
        items=items,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/low-stock", response_model=InventoryListResponse)
def list_low_stock(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("inventory", "read")),
):
    warehouse = get_main_warehouse(db)

    filters = [
        InventoryBalance.warehouse_id == warehouse.id,
        InventoryBalance.quantity_on_hand
        <= InventoryBalance.reorder_level,
        Product.is_active.is_(True),
    ]

    total = db.scalar(
        select(func.count())
        .select_from(InventoryBalance)
        .join(Product, Product.id == InventoryBalance.product_id)
        .where(*filters)
    ) or 0

    rows = db.execute(
        select(InventoryBalance, Product)
        .join(Product, Product.id == InventoryBalance.product_id)
        .where(*filters)
        .order_by(Product.name.asc())
        .offset(skip)
        .limit(limit)
    ).all()

    items = [
        serialize_balance(balance, product, warehouse)
        for balance, product in rows
    ]

    return InventoryListResponse(
        items=items,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{product_id}/movements",
    response_model=list[InventoryMovementResponse],
)
def list_product_movements(
    product_id: UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("inventory", "read")),
):
    product = db.get(Product, product_id)

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    warehouse = get_main_warehouse(db)

    return db.scalars(
        select(InventoryMovement)
        .where(
            InventoryMovement.product_id == product_id,
            InventoryMovement.warehouse_id == warehouse.id,
        )
        .order_by(InventoryMovement.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).all()


@router.get(
    "/{product_id}",
    response_model=InventoryBalanceResponse,
)
def get_product_inventory(
    product_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("inventory", "read")),
):
    product = db.get(Product, product_id)

    if product is None or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active product not found",
        )

    warehouse = get_main_warehouse(db)
    balance = get_balance(db, product_id, warehouse.id)

    return serialize_balance(balance, product, warehouse)


@router.post(
    "/adjustments",
    response_model=InventoryAdjustmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_inventory_adjustment(
    payload: InventoryAdjustmentCreate,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("inventory", "adjust")),
):
    return adjust_stock(db, payload)


@router.patch(
    "/{product_id}/reorder-level",
    response_model=InventoryBalanceResponse,
)
def update_reorder_level(
    product_id: UUID,
    payload: InventoryReorderLevelUpdate,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("inventory", "adjust")),
):
    product = db.get(Product, product_id)

    if product is None or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active product not found",
        )

    warehouse = get_main_warehouse(db)
    balance = get_balance(db, product_id, warehouse.id)

    try:
        balance.reorder_level = payload.reorder_level
        db.commit()
        db.refresh(balance)
    except Exception:
        db.rollback()
        raise

    return serialize_balance(balance, product, warehouse)