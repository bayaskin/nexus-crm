from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    is_active: bool
    is_superuser: bool
    roles: list[str]
    permissions: list[str]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=128)
    role_names: list[str] = Field(default_factory=list)


class UserListItem(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str
    is_active: bool
    is_superuser: bool
    roles: list[str]


class BootstrapResult(BaseModel):
    user_id: UUID
    email: EmailStr
    created: bool
