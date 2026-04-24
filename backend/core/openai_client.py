"""OpenAI client factory — uses per-client API key (encrypted in DB)."""

from __future__ import annotations

import httpx
from fastapi import HTTPException
from openai import OpenAI, RateLimitError

from backend.core.config import settings
from backend.core.crypto import decrypt_value

# Fast timeouts for non-data phases — failures here surface quickly.
_CONNECT_TIMEOUT_SECONDS = 10.0
_WRITE_TIMEOUT_SECONDS = 10.0
_POOL_TIMEOUT_SECONDS = 10.0


def get_openai_client(encrypted_key: str | None, *, timeout: float | None = None) -> OpenAI:
    """
    Create OpenAI client with decrypted API key.

    Args:
        encrypted_key: Encrypted value from client.openai_api_key (DB).
        timeout: Optional total read timeout override (seconds). When omitted, uses
            ``OPENAI_REQUEST_TIMEOUT_SECONDS`` as the read timeout.

    Raises:
        HTTPException 400: Key not configured.
        HTTPException 500: Decryption failed.
    """
    if not encrypted_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard.",
        )
    try:
        decrypted_key = decrypt_value(encrypted_key)
    except RuntimeError as e:
        raise HTTPException(
            status_code=500,
            detail="Failed to decrypt OpenAI API key.",
        ) from e
    read_timeout = timeout if timeout is not None else settings.openai_request_timeout_seconds
    # Separate connect vs read: connect failure surfaces fast; slow LLM responses
    # (including the wait for the first streaming chunk) get the full read budget.
    # Note: read_timeout >> openai_user_retry_budget_seconds by design — a timeout
    # error already exhausts the retry budget, so no retry is attempted.
    httpx_timeout = httpx.Timeout(
        connect=_CONNECT_TIMEOUT_SECONDS,
        read=read_timeout,
        write=_WRITE_TIMEOUT_SECONDS,
        pool=_POOL_TIMEOUT_SECONDS,
    )
    return OpenAI(
        api_key=decrypted_key,
        timeout=httpx_timeout,
        max_retries=0,
    )


# Reasoning / o-series models that reject custom temperature and other params.
_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def is_reasoning_model(model: str) -> bool:
    """Return True for OpenAI reasoning models that restrict sampling parameters."""
    m = model.lower()
    return any(m == p or m.startswith(p + "-") or m.startswith(p + "m") for p in _REASONING_MODEL_PREFIXES)


def is_quota_exceeded(exc: RateLimitError) -> bool:
    """Return True when the OpenAI error is an insufficient_quota / billing error."""
    body = getattr(exc, "body", None) or {}
    if isinstance(body, dict):
        error = body.get("error") or {}
        return error.get("code") == "insufficient_quota"
    return "insufficient_quota" in str(body)
