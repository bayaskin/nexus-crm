from datetime import timedelta
from email.message import EmailMessage
import secrets
import smtplib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import require_permission
from app.core.config import settings
from app.core.database import get_db
from app.core.security import hash_password, utcnow_naive
from app.models.identity import Permission, Role, User, UserPermissionOverride
from app.models.sessions import UserSession
from app.services.permissions import effective_permissions, has_permission

router = APIRouter(prefix="/v1/users", tags=["users"])


class UserInput(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    email: EmailStr
    password: SecretStr | None = None
    role_names: list[str] = Field(default_factory=list)
    permission_overrides: dict[str, str] = Field(default_factory=dict)
    is_active: bool = True
    send_credentials: bool = True
    require_password_change: bool = True

    @model_validator(mode="after")
    def validate_name(self):
        if not self.full_name and (not self.first_name or not self.last_name):
            raise ValueError("First and last name are required")
        return self

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not 12 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Password must be 12 to 128 characters")
        return value


class UserUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    email: EmailStr | None = None
    role_names: list[str] | None = None
    permission_overrides: dict[str, str] | None = None
    is_active: bool | None = None


def _serialize(user: User) -> dict:
    overrides = {f"{o.permission.resource}:{o.permission.action}": o.effect for o in user.permission_overrides}
    effective = effective_permissions(user)
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name,
        "first_name": user.first_name or (user.full_name.rsplit(" ", 1)[0] if " " in user.full_name else user.full_name),
        "last_name": user.last_name if user.last_name is not None else (user.full_name.rsplit(" ", 1)[1] if " " in user.full_name else ""),
        "is_active": user.is_active, "is_superuser": user.is_superuser,
        "roles": sorted(r.name for r in user.roles), "permission_overrides": overrides,
        "effective_permissions": sorted(effective), "must_change_password": user.must_change_password,
        "password_expires_at": user.password_expires_at, "last_login_at": user.last_login_at,
        "created_at": user.created_at, "updated_at": user.updated_at,
    }


def _load_user(db: Session, user_id: UUID) -> User:
    user = db.scalar(select(User).options(selectinload(User.roles).selectinload(Role.permissions), selectinload(User.permission_overrides).selectinload(UserPermissionOverride.permission)).where(User.id == user_id))
    if user is None:
        raise HTTPException(404, "User not found")
    return user


def _set_roles(db: Session, names: list[str]) -> list[Role]:
    roles = list(db.scalars(select(Role).where(Role.name.in_(names))).all()) if names else []
    missing = sorted(set(names) - {r.name for r in roles})
    if missing:
        raise HTTPException(422, {"unknown_roles": missing})
    return roles


def _set_overrides(db: Session, user: User, overrides: dict[str, str]) -> None:
    if any(effect not in {"allow", "deny"} for effect in overrides.values()):
        raise HTTPException(422, "Permission effects must be allow or deny")
    all_permissions = list(db.scalars(select(Permission)).all())
    by_name = {f"{p.resource}:{p.action}": p for p in all_permissions}
    missing = sorted(set(overrides) - set(by_name))
    if missing:
        raise HTTPException(422, {"unknown_permissions": missing})
    user.permission_overrides = [UserPermissionOverride(permission=by_name[name], effect=effect) for name, effect in overrides.items()]


def _send_email(email: str, subject: str, body: str) -> bool:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = settings.smtp_from, email, subject
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as smtp:
            smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        return False


@router.get("")
def list_users(response: Response, db: Session = Depends(get_db), _actor: User = Depends(require_permission("users", "read")),
               search: str | None = None, role: str | None = None, status_filter: str | None = Query(default=None, alias="status"),
               page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100)):
    filters = []
    if search:
        term = f"%{search.strip()}%"
        filters.append(or_(User.email.ilike(term), User.full_name.ilike(term)))
    if role:
        filters.append(User.roles.any(Role.name == role))
    if status_filter in {"active", "inactive"}:
        filters.append(User.is_active == (status_filter == "active"))
    query = select(User).options(selectinload(User.roles), selectinload(User.permission_overrides).selectinload(UserPermissionOverride.permission)).where(*filters)
    total = db.scalar(select(func.count(User.id)).where(*filters)) or 0
    users = db.scalars(query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Page-Size"] = str(page_size)
    # Keep the established list response shape for existing CRM clients; page/page_size
    # parameters bound each response and make browsing deterministic.
    return [_serialize(u) for u in users]


@router.get("/metadata")
def metadata(db: Session = Depends(get_db), _roles_actor: User = Depends(require_permission("roles", "read")), _permissions_actor: User = Depends(require_permission("permissions", "read"))):
    return _metadata(db)


def _metadata(db: Session):
    roles = db.scalars(select(Role).options(selectinload(Role.permissions)).order_by(Role.name)).all()
    permissions = db.scalars(select(Permission).order_by(Permission.resource, Permission.action)).all()
    return {"roles": [{"name": r.name, "description": r.description, "permissions": sorted(f"{p.resource}:{p.action}" for p in r.permissions)} for r in roles],
            "permissions": [f"{p.resource}:{p.action}" for p in permissions]}


@router.get("/roles")
def list_roles(db: Session = Depends(get_db), _actor: User = Depends(require_permission("roles", "read"))):
    return _metadata(db)["roles"]


@router.get("/permissions")
def list_permissions(db: Session = Depends(get_db), _actor: User = Depends(require_permission("permissions", "read"))):
    return _metadata(db)["permissions"]


@router.post("", status_code=201)
def create_user(payload: UserInput, db: Session = Depends(get_db), _actor: User = Depends(require_permission("users", "create"))):
    email = str(payload.email).strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "A user with this email already exists")
    supplied_password = payload.password.get_secret_value() if payload.password is not None else None
    temp_password = supplied_password or secrets.token_urlsafe(18)
    if payload.role_names and not _can(_actor, "roles:assign"):
        raise HTTPException(403, "Insufficient permissions to assign roles")
    if payload.permission_overrides and not _can(_actor, "permissions:manage"):
        raise HTTPException(403, "Insufficient permissions to manage permission overrides")
    full_name = payload.full_name.strip() if payload.full_name else f"{payload.first_name.strip()} {payload.last_name.strip()}"
    first_name = payload.first_name.strip() if payload.first_name else full_name.split(" ", 1)[0]
    last_name = payload.last_name.strip() if payload.last_name else (full_name.split(" ", 1)[1] if " " in full_name else "")
    if not full_name.strip():
        raise HTTPException(422, "First and last name are required")
    require_change = payload.require_password_change if supplied_password is None else False
    expires_at = utcnow_naive() + timedelta(hours=settings.temporary_password_hours) if supplied_password is None else None
    user = User(email=email, first_name=first_name, last_name=last_name, full_name=f"{first_name} {last_name}".strip(), password_hash=hash_password(temp_password),
                is_active=payload.is_active, is_superuser=False, must_change_password=require_change,
                password_expires_at=expires_at, roles=_set_roles(db, payload.role_names))
    _set_overrides(db, user, payload.permission_overrides)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A user with this email already exists")
    user = _load_user(db, user.id)
    email_sent = False if not payload.send_credentials else _send_email(email, "Your Nexus account", f"Hello {user.full_name},\n\nAccount: {email}\nTemporary password: {temp_password}\nLogin: {settings.app_login_url}\nChange this password at first login. It expires in {settings.temporary_password_hours} hours.\n\nContact an administrator if you did not expect this account.")
    result = _serialize(user)
    result.update({
        "email_sent": email_sent,
        "credentials_sent": email_sent,
        "credentials_email_requested": payload.send_credentials,
    })
    if not email_sent and settings.expose_temporary_passwords:
        result["temporary_password"] = temp_password
    if payload.send_credentials and not email_sent:
        result["message"] = "User created, but credential email delivery failed. No temporary password was returned. Check SMTP configuration and retry with Reset password."
    elif not payload.send_credentials:
        result["message"] = "User created without emailing credentials. Use Reset password to email a temporary password."
    return result


@router.get("/{user_id}")
def get_user(user_id: UUID, db: Session = Depends(get_db), _actor: User = Depends(require_permission("users", "read"))):
    return _serialize(_load_user(db, user_id))


@router.patch("/{user_id}")
def update_user(user_id: UUID, payload: UserUpdate, db: Session = Depends(get_db), _actor: User = Depends(require_permission("users", "update"))):
    user = _load_user(db, user_id)
    data = payload.model_dump(exclude_unset=True)
    if "is_active" in data and data["is_active"] != user.is_active and not _can(_actor, "users:deactivate"):
        raise HTTPException(403, "Insufficient permissions to activate or deactivate users")
    if "role_names" in data and not _can(_actor, "roles:assign"):
        raise HTTPException(403, "Insufficient permissions to assign roles")
    if "permission_overrides" in data and not _can(_actor, "permissions:manage"):
        raise HTTPException(403, "Insufficient permissions to manage permission overrides")
    if "email" in data and data["email"]:
        email = str(data["email"]).lower()
        if db.scalar(select(User.id).where(User.email == email, User.id != user.id)):
            raise HTTPException(409, "A user with this email already exists")
        user.email = email
    first, last = data.pop("first_name", None), data.pop("last_name", None)
    if first is not None or last is not None:
        current_first = user.first_name or user.full_name.split(" ", 1)[0]
        current_last = user.last_name if user.last_name is not None else (user.full_name.split(" ", 1)[1] if " " in user.full_name else "")
        user.first_name = first.strip() if first is not None else current_first
        user.last_name = last.strip() if last is not None else current_last
        user.full_name = f"{user.first_name} {user.last_name}".strip()
    if "role_names" in data:
        user.roles = _set_roles(db, data.pop("role_names") or [])
    if "permission_overrides" in data:
        _set_overrides(db, user, data.pop("permission_overrides") or {})
    for key, value in data.items():
        setattr(user, key, value)
    if data.get("is_active") is False:
        db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({UserSession.revoked_at: utcnow_naive()})
    db.commit()
    return _serialize(_load_user(db, user.id))


@router.post("/{user_id}/reset-password")
def reset_password(user_id: UUID, db: Session = Depends(get_db), _actor: User = Depends(require_permission("users", "reset_password"))):
    user = _load_user(db, user_id)
    password = secrets.token_urlsafe(18)
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.password_expires_at = utcnow_naive() + timedelta(hours=settings.temporary_password_hours)
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({UserSession.revoked_at: utcnow_naive()})
    db.commit()
    sent = _send_email(user.email, "Your Nexus password was reset", f"Hello {user.full_name},\n\nTemporary password: {password}\nLogin: {settings.app_login_url}\nIt expires in {settings.temporary_password_hours} hours and must be changed at next login. Contact an administrator if you did not request this reset.")
    result = {"message": "Temporary password generated", "email_sent": sent, "password_expires_at": user.password_expires_at}
    if not sent:
        result["message"] = "Password reset was saved, but the email failed. Check SMTP configuration and retry Reset password. No password was returned."
    if not sent and settings.expose_temporary_passwords:
        result["temporary_password"] = password
    return result


def _can(user: User, permission: str) -> bool:
    return has_permission(user, permission)
