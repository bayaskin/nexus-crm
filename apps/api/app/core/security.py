import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

password_hash = PasswordHash.recommended()
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return password_hash.verify(password, encoded_hash)
    except Exception:
        return False


def create_access_token(user_id: UUID, session_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> tuple[UUID, UUID]:
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])

    if (
        payload.get("type") != "access"
        or not payload.get("sub")
        or not payload.get("sid")
    ):
        raise jwt.InvalidTokenError("Invalid access token")

    return UUID(payload["sub"]), UUID(payload["sid"])


def create_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_expiry() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days)).replace(tzinfo=None)


def utcnow_naive() -> datetime:
    # MySQL DATETIME does not preserve timezone information; persist UTC as naive values.
    return datetime.now(timezone.utc).replace(tzinfo=None)
