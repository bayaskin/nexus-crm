from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.identity import User
from app.schemas.auth import LoginRequest, LogoutRequest, RefreshRequest, TokenResponse, UserSummary
from app.services.auth import login, logout, rotate_refresh_token

router = APIRouter(prefix="/v1/auth", tags=["authentication"])


def _user_summary(user: User) -> UserSummary:
    roles = sorted(role.name for role in user.roles)
    permissions = sorted(
        {f"{permission.resource}:{permission.action}" for role in user.roles for permission in role.permissions}
    )
    if user.is_superuser:
        permissions = ["*"]
    return UserSummary(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        roles=roles,
        permissions=permissions,
    )


@router.post("/login", response_model=TokenResponse)
def login_endpoint(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    return login(db, request, payload.email, payload.password)


@router.post("/refresh", response_model=TokenResponse)
def refresh_endpoint(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    return rotate_refresh_token(db, request, payload.refresh_token)


@router.post("/logout", status_code=204)
def logout_endpoint(payload: LogoutRequest, db: Session = Depends(get_db)) -> Response:
    logout(db, payload.refresh_token)
    return Response(status_code=204)


@router.get("/me", response_model=UserSummary)
def me_endpoint(user: User = Depends(get_current_user)):
    return _user_summary(user)
