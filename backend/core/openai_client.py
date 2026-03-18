"""OpenAI client factory — uses per-client API key."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from openai import OpenAI


def get_openai_client(api_key: Optional[str]) -> OpenAI:
    """
    Create OpenAI client with given API key.

    Raises HTTPException 400 if key is not configured.
    """
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        )
    return OpenAI(api_key=api_key)
