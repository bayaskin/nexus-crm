from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.catalog import Product
from app.models.identity import User
from app.schemas.products import (
    ProductCreate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
)
from app.models.inventory import InventoryBalance, Warehouse

router = APIRouter(prefix="/v1/products", tags=["products"])


def get_active_product(db: Session, product_id: UUID) -> Product:
    product = db.get(Product, product_id)

    if product is None or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    return product


def raise_duplicate_sku() -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A product with this SKU already exists",
    )


@router.get("", response_model=ProductListResponse)
def list_products(
    search: str | None = Query(default=None, max_length=180),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("products", "read")),
):
    filters = [Product.is_active.is_(True)]

    if search and search.strip():
        term = f"%{search.strip()}%"
        filters.append(
            or_(
                Product.sku.ilike(term),
                Product.name.ilike(term),
            )
        )

    total = db.scalar(
        select(func.count()).select_from(Product).where(*filters)
    ) or 0

    products = db.scalars(
        select(Product)
        .where(*filters)
        .order_by(Product.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    return ProductListResponse(
        items=products,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("products", "create")),
):
    product = Product(
        sku=payload.sku,
        name=payload.name,
        description=payload.description,
        price=payload.price,
        is_active=True,
    )

    db.add(product)

    try:
        db.flush()

        warehouse = db.scalar(
            select(Warehouse).where(Warehouse.code == "MAIN")
        )

        if warehouse is None:
            raise RuntimeError("Main warehouse is not configured")

        db.add(
            InventoryBalance(
                warehouse_id=warehouse.id,
                product_id=product.id,
                quantity_on_hand=0,
                reorder_level=0,
            )
        )

        db.commit()

    except IntegrityError:
        db.rollback()
        raise_duplicate_sku()
    except Exception:
        db.rollback()
        raise

    db.refresh(product)
    return product


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("products", "read")),
):
    return get_active_product(db, product_id)


@router.patch("/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: UUID,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("products", "update")),
):
    product = get_active_product(db, product_id)
    changes = payload.model_dump(exclude_unset=True)

    for field, value in changes.items():
        setattr(product, field, value)

    if changes:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise_duplicate_sku()

        db.refresh(product)

    return product


@router.post(
    "/{product_id}/deactivate",
    response_model=ProductResponse,
)
def deactivate_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("products", "update")),
):
    product = get_active_product(db, product_id)
    product.is_active = False

    db.commit()
    db.refresh(product)

    return product