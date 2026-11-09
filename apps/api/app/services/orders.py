from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.catalog import Product
from app.models.crm import Customer
from app.models.inventory import InventoryBalance, InventoryMovement
from app.models.inventory import Warehouse
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


def get_locked_order(db: Session, order_id: UUID) -> Order:
    order = db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
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

    if any(not product.is_active for product in products):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inactive products cannot be added to an order",
        )

    order_items = []
    total = Decimal("0.00")

    for requested in requested_items:
        product = products_by_id[requested.product_id]
        unit_price = product.price
        total += unit_price * requested.quantity

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
        order = get_locked_order(db, order_id)

        if order.status != "draft":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only draft orders can be edited",
            )

        # Load items after locking the order row.
        _ = order.items

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


def lock_order_inventory(
    db: Session,
    order: Order,
    warehouse_id: UUID,
    require_active_products: bool = True,
) -> list[tuple[OrderItem, InventoryBalance]]:
    """
    Lock all balances in deterministic product ID order.

    The caller must already hold the order row lock.
    """
    items = list(order.items)

    if not items:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An order without items cannot be fulfilled",
        )

    product_ids = sorted(
        {item.product_id for item in items},
        key=str,
    )

    # Lock product rows first, preventing a concurrent product
    # deactivation from racing with order confirmation.
    products = db.scalars(
        select(Product)
        .where(Product.id.in_(product_ids))
        .order_by(Product.id)
        .with_for_update()
    ).all()

    products_by_id = {product.id: product for product in products}

    if len(products_by_id) != len(product_ids):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An order contains a product that no longer exists",
        )

    if require_active_products and any(
        not product.is_active for product in products
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An order contains an inactive product",
        )

    balances = db.scalars(
        select(InventoryBalance)
        .where(
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.product_id.in_(product_ids),
        )
        .order_by(InventoryBalance.product_id)
        .with_for_update()
    ).all()

    balances_by_product = {
        balance.product_id: balance for balance in balances
    }

    if len(balances_by_product) != len(product_ids):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inventory balance is missing for an order product",
        )

    return [
        (item, balances_by_product[item.product_id])
        for item in items
    ]


def confirm_order(db: Session, order_id: UUID) -> Order:
    try:
        order = get_locked_order(db, order_id)

        if order.status != "draft":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only draft orders can be confirmed",
            )

        warehouse = get_main_warehouse(db)
        locked_items = lock_order_inventory(
            db,
            order,
            warehouse.id,
            require_active_products=False,
        )

        # Validate every item before mutating any balance.
        for item, balance in locked_items:
            if balance.quantity_on_hand < item.quantity:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Insufficient stock for product {item.product_id}. "
                        f"Available: {balance.quantity_on_hand}, "
                        f"requested: {item.quantity}"
                    ),
                )

        # All stock is available. Apply deductions and movements.
        for item, balance in locked_items:
            balance.quantity_on_hand -= item.quantity

            db.add(
                InventoryMovement(
                    warehouse_id=warehouse.id,
                    product_id=item.product_id,
                    quantity_change=-item.quantity,
                    reason="order_fulfillment",
                    reference_id=str(order.id),
                    notes=f"Stock deducted for order {order.id}",
                )
            )

        order.status = "confirmed"
        db.commit()

        return get_order(db, order.id)

    except Exception:
        db.rollback()
        raise


def ship_order(db: Session, order_id: UUID) -> Order:
    try:
        order = get_locked_order(db, order_id)

        if order.status != "confirmed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only confirmed orders can be shipped",
            )

        order.status = "shipped"
        db.commit()
        return get_order(db, order.id)

    except Exception:
        db.rollback()
        raise


def deliver_order(db: Session, order_id: UUID) -> Order:
    try:
        order = get_locked_order(db, order_id)

        if order.status != "shipped":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only shipped orders can be delivered",
            )

        order.status = "delivered"
        db.commit()
        return get_order(db, order.id)

    except Exception:
        db.rollback()
        raise


def cancel_order(db: Session, order_id: UUID) -> Order:
    try:
        order = get_locked_order(db, order_id)

        if order.status not in {"draft", "confirmed"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only draft or confirmed orders can be cancelled",
            )

        if order.status == "confirmed":
            warehouse = get_main_warehouse(db)
            locked_items = lock_order_inventory(
                db,
                order,
                warehouse.id,
                require_active_products=False,
            )

            # Restore every item in the same transaction.
            for item, balance in locked_items:
                balance.quantity_on_hand += item.quantity

                db.add(
                    InventoryMovement(
                        warehouse_id=warehouse.id,
                        product_id=item.product_id,
                        quantity_change=item.quantity,
                        reason="order_cancellation",
                        reference_id=str(order.id),
                        notes=f"Stock restored for cancelled order {order.id}",
                    )
                )

        order.status = "cancelled"
        db.commit()

        return get_order(db, order.id)

    except Exception:
        db.rollback()
        raise
