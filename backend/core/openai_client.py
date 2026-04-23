"""OpenAI client factory — uses per-client API key (encrypted in DB)."""

from __future__ import annotations

import httpx
from fastapi import HTTPException
from openai import OpenAI

from backend.core.config import settings
from backend.core.crypto import decrypt_value


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
    httpx_timeout = httpx.Timeout(
        connect=10.0,
        read=read_timeout,
        write=10.0,
        pool=10.0,
    )
    return OpenAI(
        api_key=decrypted_key,
        timeout=httpx_timeout,
        max_retries=0,
    )
