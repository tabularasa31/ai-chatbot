"""Business logic for authentication."""

from __future__ import annotations

import uuid

import jwt
from sqlalchemy.orm import Session

from backend.core.security import ALGORITHM, create_access_token, hash_password, verify_password
from backend.core.config import settings
from backend.models import User


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
        raise HTTPException(status_code=409, detail="Email already registered")
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
        raise HTTPException(status_code=401, detail="Token expired")
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def create_token_for_user(user: User) -> tuple[str, int]:
    """Create JWT token for user. Returns (token, expires_in_seconds)."""
    token = create_access_token(data={"sub": str(user.id)})
    return token, ACCESS_TOKEN_EXPIRE_SECONDS
