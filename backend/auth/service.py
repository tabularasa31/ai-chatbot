"""Business logic for authentication."""

from __future__ import annotations

import uuid
from datetime import timedelta

import jwt
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.security import ALGORITHM, create_access_token, hash_password, verify_password
from backend.models import User
from backend.models.base import _utcnow

ACCESS_TOKEN_EXPIRE_SECONDS = 24 * 60 * 60  # 24 hours


def register_user(email: str, password: str, db: Session) -> User:
    """
    Register a new user.

    Validates password strength, checks email uniqueness, hashes password,
    creates user in DB. Raises HTTPException on conflict or validation error.
    """
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    password_hash = hash_password(password)
    user = User(email=email, password_hash=password_hash)
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered") from None
    return user


def authenticate_user(email: str, password: str, db: Session) -> User | None:
    """
    Authenticate user by email and password.

    Returns User if credentials are valid, None otherwise.
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_current_user_from_token(token: str, db: Session) -> User:
    """
    Decode JWT token, extract user_id, query DB for user.

    Raises HTTPException 401 if token invalid or expired.
    """
    from fastapi import HTTPException

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
        )
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        user_id = uuid.UUID(user_id_str)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired") from None
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token") from None

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def create_token_for_user(user: User) -> tuple[str, int]:
    """Create JWT token for user. Returns (token, expires_in_seconds)."""
    token = create_access_token(data={"sub": str(user.id)})
    return token, ACCESS_TOKEN_EXPIRE_SECONDS


def create_reset_token(email: str, db: Session) -> str | None:
    """
    Generate reset token for user. Returns token or None if email not found.

    Always returns generic message (don't reveal if email exists).
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None  # Silently fail (security: don't reveal if email exists)

    token = uuid.uuid4().hex
    user.reset_password_token = token
    # Naive UTC — column is ``DateTime`` (no ``timezone=True``); both the
    # write and the later ``>= now`` comparison must be naive to keep the
    # async path (asyncpg) happy. See ``models/base._utcnow``.
    user.reset_password_expires_at = _utcnow() + timedelta(hours=1)
    db.commit()
    return token


def reset_password(token: str, new_password: str, db: Session) -> bool:
    """
    Validate reset token and update password.

    Returns True if successful, False if token invalid/expired.
    """
    now = _utcnow()
    user = (
        db.query(User)
        .filter(
            User.reset_password_token == token,
            User.reset_password_expires_at >= now,
        )
        .first()
    )
    if not user:
        return False

    user.password_hash = hash_password(new_password)
    user.reset_password_token = None
    user.reset_password_expires_at = None
    # Password reset via email link proves ownership — mark as verified
    user.is_verified = True
    user.verification_token = None
    user.verification_expires_at = None
    db.commit()
    return True
