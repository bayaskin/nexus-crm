from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.catalog import Product
from app.models.crm import Customer
from app.models.sales import Order, OrderItem
from app.schemas.orders import OrderCreate, OrderUpdate

MAX_ORDER_TOTAL = Decimal("9999999999.99")


def get_active_customer(db: Session, customer_id: UUID) -> Customer:
    customer = db.scalar(
        select(Customer)
        .where(
            Customer.id == customer_id,
            Customer.is_active.is_(True),
        )
        .with_for_update()
    )

    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    return customer


def get_order(db: Session, order_id: UUID) -> Order:
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.id == order_id)
    )

    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    return order


def resolve_order_items(
    db: Session,
    requested_items,
) -> tuple[list[OrderItem], Decimal]:
    """
    Resolve active products and snapshot their current prices.

    Product rows are locked in deterministic ID order to reduce
    deadlock risk when concurrent requests create or edit orders.
    """
    product_ids = sorted(
        {item.product_id for item in requested_items},
        key=str,
    )

    products = db.scalars(
        select(Product)
        .where(Product.id.in_(product_ids))
        .order_by(Product.id)
        .with_for_update()
    ).all()

    products_by_id = {product.id: product for product in products}

    if len(products_by_id) != len(product_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more products were not found",
        )

    inactive_products = [
        product for product in products
        if not product.is_active
    ]

    if inactive_products:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inactive products cannot be added to an order",
        )

    order_items = []
    total = Decimal("0.00")

    for requested in requested_items:
        product = products_by_id[requested.product_id]
        unit_price = product.price
        line_total = unit_price * requested.quantity

        total += line_total

        order_items.append(
            OrderItem(
                product_id=product.id,
                quantity=requested.quantity,
                unit_price=unit_price,
            )
        )

    if total > MAX_ORDER_TOTAL:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Order total exceeds the supported maximum",
        )

    return order_items, total


def create_order(db: Session, payload: OrderCreate) -> Order:
    try:
        get_active_customer(db, payload.customer_id)

        items, total = resolve_order_items(db, payload.items)

        order = Order(
            customer_id=payload.customer_id,
            status="draft",
            total=total,
            items=items,
        )

        db.add(order)
        db.commit()

        return get_order(db, order.id)

    except Exception:
        db.rollback()
        raise


def update_order(
    db: Session,
    order_id: UUID,
    payload: OrderUpdate,
) -> Order:
    try:
        order = db.scalar(
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.id == order_id)
            .with_for_update()
        )

        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Order not found",
            )

        if order.status != "draft":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only draft orders can be edited",
            )

        changes = payload.model_dump(exclude_unset=True)

        if "customer_id" in changes:
            get_active_customer(db, changes["customer_id"])
            order.customer_id = changes["customer_id"]

        if "items" in changes:
            new_items, new_total = resolve_order_items(
                db,
                payload.items,
            )

            order.items.clear()
            order.items.extend(new_items)
            order.total = new_total

        db.commit()

        return get_order(db, order.id)

    except Exception:
        db.rollback()
        raise
