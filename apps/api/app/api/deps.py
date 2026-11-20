from collections.abc import Callable
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.security import decode_access_token, utcnow_naive
from app.models.identity import Role, User
from app.models.sessions import UserSession
from app.services.permissions import has_permission

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing access token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        user_id, session_id = decode_access_token(credentials.credentials)
    except (jwt.PyJWTError, ValueError, TypeError):
        raise unauthorized

    # Verify that the session associated with this access token is still valid.
    session = db.scalar(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow_naive(),
        )
    )

    if session is None:
        raise unauthorized

    user = db.scalar(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == user_id)
    )

    if user is None or not user.is_active or (user.password_expires_at is not None and user.password_expires_at <= utcnow_naive()):
        raise unauthorized

    return user


def require_permission(resource: str, action: str) -> Callable:
    required = f"{resource}:{action}"

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.must_change_password:
            raise HTTPException(status_code=403, detail="Password change required")
        if not has_permission(user, required):
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions",
            )

        return user

    return dependency
