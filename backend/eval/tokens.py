from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Dict

import jwt

from backend.core.config import settings

EVAL_JWT_TYP = "eval_tester"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


def _eval_secret() -> str:
    return settings.eval_jwt_secret or settings.jwt_secret


def create_eval_access_token(tester_id: uuid.UUID) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    expire = now + dt.timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode: Dict[str, Any] = {
        "sub": str(tester_id),
        "typ": EVAL_JWT_TYP,
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(to_encode, _eval_secret(), algorithm=ALGORITHM)


def decode_eval_access_token(token: str) -> uuid.UUID | None:
    """Return tester id if token is a valid eval tester JWT; else None."""
    try:
        payload = jwt.decode(token, _eval_secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != EVAL_JWT_TYP:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str):
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None
