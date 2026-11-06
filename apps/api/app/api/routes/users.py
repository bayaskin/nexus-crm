from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.security import hash_password
from app.models.identity import Role, User
from app.schemas.auth import UserCreate, UserListItem

router = APIRouter(prefix="/v1/users", tags=["users"])


@router.get("", response_model=list[UserListItem])
def list_users(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("users", "read")),
):
    users = db.scalars(select(User).options(selectinload(User.roles)).order_by(User.created_at.desc())).all()
    return [
        UserListItem(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            roles=sorted(role.name for role in user.roles),
        )
        for user in users
    ]


@router.post("", response_model=UserListItem, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("users", "create")),
):
    email = payload.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    roles = []
    if payload.role_names:
        if not actor.is_superuser:
            can_assign_roles = any(
                permission.resource == "roles" and permission.action == "assign"
                for role in actor.roles
                for permission in role.permissions
            )
            if not can_assign_roles:
                raise HTTPException(status_code=403, detail="Insufficient permissions to assign roles")
        roles = list(db.scalars(select(Role).where(Role.name.in_(payload.role_names))).all())
        found = {role.name for role in roles}
        missing = sorted(set(payload.role_names) - found)
        if missing:
            raise HTTPException(status_code=422, detail={"unknown_roles": missing})

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        is_active=True,
        is_superuser=False,
        roles=roles,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserListItem(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        roles=sorted(role.name for role in user.roles),
    )
