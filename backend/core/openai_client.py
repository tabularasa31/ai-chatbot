"""OpenAI client factory — uses per-client API key (encrypted in DB)."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from openai import OpenAI

from backend.core.crypto import decrypt_value
from backend.core.config import settings


def get_openai_client(encrypted_key: Optional[str]) -> OpenAI:
    """
    Create OpenAI client with decrypted API key.

    Args:
        encrypted_key: Encrypted value from client.openai_api_key (DB).

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
    return OpenAI(
        api_key=decrypted_key,
        timeout=settings.openai_request_timeout_seconds,
    )
