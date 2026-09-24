"""Retry-wrapped chat completion helper that parses the reply as a JSON object."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI, OpenAI

from backend.core.openai_retry import async_call_openai_with_retry, call_openai_with_retry

logger = logging.getLogger(__name__)


def _parse_json_object(operation: str, raw: str, *, strict: bool) -> dict | None:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict):
        logger.warning("%s_malformed_json preview=%r", operation, (raw or "")[:200])
        if strict:
            raise ValueError(f"{operation}: LLM response was not a JSON object")
        return None
    return parsed


def chat_json(
    operation: str,
    client: OpenAI,
    *,
    model: str,
    messages: list[dict],
    strict: bool = False,
    **kw: Any,
) -> dict | None:
    response = call_openai_with_retry(
        operation,
        lambda: client.chat.completions.create(model=model, messages=messages, **kw),
    )
    raw = response.choices[0].message.content or "{}"
    return _parse_json_object(operation, raw, strict=strict)


async def async_chat_json(
    operation: str,
    client: AsyncOpenAI,
    *,
    model: str,
    messages: list[dict],
    strict: bool = False,
    **kw: Any,
) -> dict | None:
    async def _call() -> Any:
        return await client.chat.completions.create(model=model, messages=messages, **kw)

    response = await async_call_openai_with_retry(operation, _call)
    raw = response.choices[0].message.content or "{}"
    return _parse_json_object(operation, raw, strict=strict)
