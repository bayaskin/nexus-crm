from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    status: str = Field(default="active", min_length=1, max_length=30)
    notes: str | None = None

    @field_validator("name", "status")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr | None) -> EmailStr | None:
        return value.lower() if value else value

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=180)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, min_length=1, max_length=30)
    notes: str | None = None
    is_active: bool | None = None

    @field_validator("name", "status")
    @classmethod
    def validate_required_text(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("This field cannot be null")
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr | None) -> EmailStr | None:
        return value.lower() if value else value

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: EmailStr | None
    phone: str | None
    status: str
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CustomerListResponse(BaseModel):
    items: list[CustomerResponse]
    total: int
    skip: int
    limit: int


class ContactCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    is_primary: bool = False

    @field_validator("first_name", "last_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr | None) -> EmailStr | None:
        return value.lower() if value else value

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ContactUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    is_primary: bool | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_required_text(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("This field cannot be null")
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr | None) -> EmailStr | None:
        return value.lower() if value else value

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    first_name: str
    last_name: str
    email: EmailStr | None
    phone: str | None
    is_primary: bool
    created_at: datetime
    updated_at: datetime