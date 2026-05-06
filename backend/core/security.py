from __future__ import annotations

import datetime as dt
from typing import Any

import bcrypt
import jwt

from .config import settings
from .jwt_kinds import USER_ACCESS_JWT_TYP

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")

    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against its bcrypt hash."""
    if not (isinstance(password, str) and isinstance(hashed, str)):
        return False

    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            hashed.encode("utf-8"),
        )
    except ValueError:
        return False


def decode_access_token(token: str) -> str | None:
    """Decode JWT token and return user_id (sub claim), or None if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != USER_ACCESS_JWT_TYP:
        return None
    return payload.get("sub")


def create_access_token(data: dict[str, Any]) -> str:
    """Create a JWT access token with the given payload (24-hour TTL)."""
    to_encode = data.copy()
    to_encode.setdefault("typ", USER_ACCESS_JWT_TYP)
    now = dt.datetime.now(dt.UTC)
    expire = now + dt.timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire, "iat": now})

    token = jwt.encode(to_encode, settings.jwt_secret, algorithm=ALGORITHM)
    return token


