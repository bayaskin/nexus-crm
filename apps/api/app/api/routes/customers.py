from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.crm import Contact, Customer
from app.models.identity import User
from app.schemas.customers import (
    ContactCreate,
    ContactResponse,
    CustomerCreate,
    CustomerListResponse,
    CustomerResponse,
    CustomerUpdate,
)

router = APIRouter(prefix="/v1/customers", tags=["customers"])


def get_active_customer(db: Session, customer_id: UUID) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None or not customer.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )
    return customer


@router.get("", response_model=CustomerListResponse)
def list_customers(
    search: str | None = Query(default=None, max_length=180),
    customer_status: str | None = Query(default=None, alias="status", max_length=30),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("customers", "read")),
):
    filters = [Customer.is_active.is_(True)]

    if customer_status:
        filters.append(Customer.status == customer_status)

    if search and search.strip():
        term = f"%{search.strip()}%"
        filters.append(
            or_(
                Customer.name.ilike(term),
                Customer.email.ilike(term),
                Customer.phone.ilike(term),
            )
        )

    total = db.scalar(
        select(func.count()).select_from(Customer).where(*filters)
    ) or 0

    customers = db.scalars(
        select(Customer)
        .where(*filters)
        .order_by(Customer.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    return CustomerListResponse(
        items=customers,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_customer(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("customers", "create")),
):
    customer = Customer(
        name=payload.name,
        email=str(payload.email).lower() if payload.email else None,
        phone=payload.phone,
        status=payload.status,
        notes=payload.notes,
        is_active=True,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("customers", "read")),
):
    return get_active_customer(db, customer_id)


@router.patch("/{customer_id}", response_model=CustomerResponse)
def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("customers", "update")),
):
    customer = get_active_customer(db, customer_id)
    changes = payload.model_dump(exclude_unset=True)

    for field, value in changes.items():
        if field == "email" and value is not None:
            value = str(value).lower()
        setattr(customer, field, value)

    if changes:
        db.commit()
        db.refresh(customer)

    return customer


@router.post("/{customer_id}/deactivate", response_model=CustomerResponse)
def deactivate_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("customers", "update")),
):
    customer = get_active_customer(db, customer_id)
    customer.is_active = False
    db.commit()
    db.refresh(customer)
    return customer


@router.get(
    "/{customer_id}/contacts",
    response_model=list[ContactResponse],
)
def list_customer_contacts(
    customer_id: UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("customers", "read")),
):
    get_active_customer(db, customer_id)

    return db.scalars(
        select(Contact)
        .where(Contact.customer_id == customer_id)
        .order_by(Contact.is_primary.desc(), Contact.created_at.asc())
    ).all()


@router.post(
    "/{customer_id}/contacts",
    response_model=ContactResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_customer_contact(
    customer_id: UUID,
    payload: ContactCreate,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("customers", "create")),
):
    get_active_customer(db, customer_id)

    if payload.is_primary:
        existing_primary_contacts = db.scalars(
            select(Contact).where(
                Contact.customer_id == customer_id,
                Contact.is_primary.is_(True),
            )
        ).all()

        for contact in existing_primary_contacts:
            contact.is_primary = False

    contact = Contact(
        customer_id=customer_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=str(payload.email).lower() if payload.email else None,
        phone=payload.phone,
        is_primary=payload.is_primary,
    )

    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact