from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

import jwt

from backend.core.config import settings
from backend.core.jwt_kinds import EVAL_TESTER_JWT_TYP

logger = logging.getLogger(__name__)

EVAL_JWT_TYP = EVAL_TESTER_JWT_TYP
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


class EvalJwtSecretMissingError(Exception):
    """EVAL_JWT_SECRET is unset or blank (misconfiguration)."""


def _eval_secret() -> str:
    s = settings.eval_jwt_secret
    if not isinstance(s, str) or not s.strip():
        logger.error(
            "eval_jwt_misconfigured: EVAL_JWT_SECRET is missing or blank; "
            "eval login and protected /eval routes cannot run (HTTP 503)"
        )
        raise EvalJwtSecretMissingError
    return s.strip()


def create_eval_access_token(tester_id: uuid.UUID) -> str:
    now = dt.datetime.now(dt.UTC)
    expire = now + dt.timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode: dict[str, Any] = {
        "sub": str(tester_id),
        "typ": EVAL_JWT_TYP,
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(to_encode, _eval_secret(), algorithm=ALGORITHM)


def decode_eval_access_token(token: str) -> uuid.UUID | None:
    """Return tester id if token is a valid eval tester JWT; else None."""
    try:
        secret = _eval_secret()
    except EvalJwtSecretMissingError:
        raise
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
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
