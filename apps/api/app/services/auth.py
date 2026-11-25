from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
    refresh_expiry,
    utcnow_naive,
    verify_password,
)
from app.models.identity import User
from app.models.sessions import UserSession


def _token_pair(user: User, db: Session, request: Request) -> dict[str, object]:
    refresh_token = create_refresh_token()
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        expires_at=refresh_expiry(),
        user_agent=request.headers.get("user-agent", "")[:512] or None,
        ip_address=request.client.host if request.client else None,
    )
    db.add(session)
    db.flush()
    return {
        "access_token": create_access_token(user.id, session.id),
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
    }


def login(db: Session, request: Request, email: str, password: str) -> dict[str, object]:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if user.password_expires_at and user.password_expires_at <= utcnow_naive():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Temporary password expired")
    user.last_login_at = utcnow_naive()
    # Acquire the parent-row write lock before the session insert performs its
    # foreign-key check. Concurrent logins must take these locks in the same
    # order to avoid an InnoDB shared-to-exclusive lock-upgrade deadlock.
    db.flush()
    result = _token_pair(user, db, request)
    db.commit()
    return result


def rotate_refresh_token(db: Session, request: Request, raw_token: str) -> dict[str, object]:
    token_hash = hash_refresh_token(raw_token)
    session = db.scalar(
        select(UserSession).where(UserSession.refresh_token_hash == token_hash).with_for_update()
    )
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    now = utcnow_naive()
    if session.revoked_at is not None:
        # Reuse of a previously rotated token indicates possible token theft.
        db.execute(
            update(UserSession)
            .where(UserSession.user_id == session.user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Refresh token reuse detected; sessions revoked")

    if session.expires_at <= now:
        session.revoked_at = now
        db.commit()
        raise HTTPException(status_code=401, detail="Refresh token expired")

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        session.revoked_at = now
        db.commit()
        raise HTTPException(status_code=401, detail="User is inactive")
    if user.password_expires_at is not None and user.password_expires_at <= now:
        session.revoked_at = now
        db.commit()
        raise HTTPException(status_code=401, detail="Temporary password expired")

    replacement = _token_pair(user, db, request)
    session.revoked_at = now
    # _token_pair flushes the replacement session; identify it by its token hash.
    replacement_session = db.scalar(
        select(UserSession).where(
            UserSession.refresh_token_hash == hash_refresh_token(str(replacement["refresh_token"]))
        )
    )
    if replacement_session:
        session.replaced_by_id = replacement_session.id
    db.commit()
    return replacement


def logout(db: Session, raw_token: str) -> None:
    session = db.scalar(
        select(UserSession).where(UserSession.refresh_token_hash == hash_refresh_token(raw_token))
    )
    if session and session.revoked_at is None:
        session.revoked_at = utcnow_naive()
        db.commit()
