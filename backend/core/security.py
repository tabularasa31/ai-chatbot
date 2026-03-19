from __future__ import annotations

import datetime as dt
from typing import Any, Dict

import bcrypt
import jwt

from .config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


def hash_password(password: str) -> str:
    """Хеширует пароль с помощью bcrypt."""
    if not isinstance(password, str):
        raise TypeError("password must be a string")

    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Проверяет пароль по его bcrypt-хешу."""
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
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.PyJWTError:
        return None


def create_access_token(data: Dict[str, Any]) -> str:
    """Создаёт JWT-токен с payload и сроком жизни 24 часа."""
    to_encode = data.copy()
    now = dt.datetime.now(dt.timezone.utc)
    expire = now + dt.timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire, "iat": now})

    token = jwt.encode(to_encode, settings.jwt_secret, algorithm=ALGORITHM)
    return token

