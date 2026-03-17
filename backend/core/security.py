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


def create_access_token(data: Dict[str, Any]) -> str:
    """Создаёт JWT-токен с payload и сроком жизни 24 часа."""
    to_encode = data.copy()
    now = dt.datetime.utcnow()
    expire = now + dt.timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire, "iat": now})

    token = jwt.encode(to_encode, settings.jwt_secret, algorithm=ALGORITHM)
    return token

