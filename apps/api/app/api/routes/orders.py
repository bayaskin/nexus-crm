from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.identity import User
from app.models.sales import Order
from app.schemas.orders import (
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderUpdate,
)
from app.services.orders import (
    cancel_order,
    confirm_order,
    create_order,
    deliver_order,
    get_order,
    ship_order,
    update_order,
)

router = APIRouter(prefix="/v1/orders", tags=["orders"])

VALID_ORDER_STATUSES = {
    "draft",
    "confirmed",
    "shipped",
    "delivered",
    "cancelled",
}


@router.get("", response_model=OrderListResponse)
def list_orders(
    search_status: str | None = Query(
        default=None,
        alias="status",
        max_length=30,
    ),
    customer_id: UUID | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("orders", "read")),
):
    filters = []

    if search_status:
        if search_status not in VALID_ORDER_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid order status",
            )
        filters.append(Order.status == search_status)

    if customer_id:
        filters.append(Order.customer_id == customer_id)

    total = db.scalar(
        select(func.count())
        .select_from(Order)
        .where(*filters)
    ) or 0

    orders = db.scalars(
        select(Order)
        .options(selectinload(Order.items))
        .where(*filters)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    return OrderListResponse(
        items=orders,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_order_endpoint(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("orders", "create")),
):
    return create_order(db, payload)


@router.get("/{order_id}", response_model=OrderResponse)
def get_order_endpoint(
    order_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("orders", "read")),
):
    return get_order(db, order_id)


@router.patch("/{order_id}", response_model=OrderResponse)
def update_order_endpoint(
    order_id: UUID,
    payload: OrderUpdate,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("orders", "update")),
):
    return update_order(db, order_id, payload)


@router.post("/{order_id}/confirm", response_model=OrderResponse)
def confirm_order_endpoint(
    order_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("orders", "update")),
):
    return confirm_order(db, order_id)


@router.post("/{order_id}/ship", response_model=OrderResponse)
def ship_order_endpoint(
    order_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("orders", "update")),
):
    return ship_order(db, order_id)


@router.post("/{order_id}/deliver", response_model=OrderResponse)
def deliver_order_endpoint(
    order_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("orders", "update")),
):
    return deliver_order(db, order_id)


@router.post("/{order_id}/cancel", response_model=OrderResponse)
def cancel_order_endpoint(
    order_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("orders", "update")),
):
    return cancel_order(db, order_id)
