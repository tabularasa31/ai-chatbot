from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import time
from typing import Any

import bcrypt
import jwt

from .config import settings
from .jwt_kinds import EVAL_TESTER_JWT_TYP, USER_ACCESS_JWT_TYP

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


def hash_password(password: str) -> str:
    """Хеширует пароль с помощью bcrypt."""  # noqa: RUF002
    if not isinstance(password, str):
        raise TypeError("password must be a string")

    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Проверяет пароль по его bcrypt-хешу."""  # noqa: RUF002
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
    # Never treat internal eval tester JWTs as user sessions (even if mis-signed).
    if payload.get("typ") == EVAL_TESTER_JWT_TYP:
        return None
    return payload.get("sub")


def create_access_token(data: dict[str, Any]) -> str:
    """Создаёт JWT-токен с payload и сроком жизни 24 часа."""  # noqa: RUF002
    to_encode = data.copy()
    to_encode.setdefault("typ", USER_ACCESS_JWT_TYP)
    now = dt.datetime.now(dt.UTC)
    expire = now + dt.timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire, "iat": now})

    token = jwt.encode(to_encode, settings.jwt_secret, algorithm=ALGORITHM)
    return token


def generate_kyc_token(user_context: dict, secret_key: str, ttl_seconds: int = 300) -> str:
    """
    Generate a signed identity token for widget initialization.

    Token format: base64(json_payload).<hmac_sha256_hex_signature>
    """
    now = int(time.time())
    payload = {**user_context, "exp": now + ttl_seconds, "iat": now}
    raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    b64 = base64.urlsafe_b64encode(raw_json.encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(
        secret_key.encode("utf-8"),
        b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{b64}.{sig}"


def validate_kyc_token_detail(
    token: str,
    secret_key: str,
) -> tuple[dict | None, str | None]:
    """
    Validate a signed identity token. Returns (user_context_dict, None) or (None, reason).

    reason is one of: malformed, expired, missing_user_id, bad_signature
    """
    if not token or "." not in token:
        return None, "malformed"
    parts = token.split(".", 1)
    if len(parts) != 2:
        return None, "malformed"
    payload_b64, sig_hex = parts[0], parts[1]
    if not payload_b64 or not sig_hex:
        return None, "malformed"
    pad = "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None, "malformed"

    if not isinstance(data, dict):
        return None, "malformed"

    exp = data.get("exp")
    if exp is None:
        return None, "malformed"
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return None, "malformed"
    if int(time.time()) > exp_i:
        return None, "expired"

    uid = data.get("user_id")
    if not isinstance(uid, str) or not uid.strip():
        return None, "missing_user_id"

    expected = hmac.new(
        secret_key.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig_hex):
        return None, "bad_signature"

    out = {k: v for k, v in data.items() if k not in ("exp", "iat")}
    return out, None


def validate_kyc_token(token: str, secret_key: str) -> dict | None:
    """
    Validate a signed identity token.

    Returns UserContext fields dict if valid, None otherwise (never raises).
    """
    ctx, _err = validate_kyc_token_detail(token, secret_key)
    return ctx
