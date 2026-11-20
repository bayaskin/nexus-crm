from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.security import hash_password, utcnow_naive, verify_password
from app.models.identity import User
from app.models.sessions import UserSession
from app.schemas.auth import LoginRequest, LogoutRequest, PasswordChangeRequest, RefreshRequest, TokenResponse, UserSummary
from app.services.auth import login, logout, rotate_refresh_token
from app.services.permissions import effective_permissions

router = APIRouter(prefix="/v1/auth", tags=["authentication"])


def _user_summary(user: User) -> UserSummary:
    roles = sorted(role.name for role in user.roles)
    permissions = sorted(effective_permissions(user))
    return UserSummary(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        roles=roles,
        permissions=permissions,
        must_change_password=user.must_change_password,
    )


@router.post("/login", response_model=TokenResponse)
def login_endpoint(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    return login(db, request, payload.email, payload.password.get_secret_value())


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


@router.post("/password/change", status_code=204)
def change_password(payload: PasswordChangeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    current = payload.current_password.get_secret_value()
    password = payload.new_password.get_secret_value()
    if not verify_password(current, user.password_hash):
        raise HTTPException(status_code=422, detail="Current password is incorrect")
    user.password_hash = hash_password(password)
    user.must_change_password = False
    user.password_expires_at = None
    db.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=utcnow_naive()))
    db.commit()
    return Response(status_code=204)
