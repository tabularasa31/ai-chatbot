"""Shared helpers for chat test modules."""

from __future__ import annotations

from unittest.mock import Mock


def _chat_completion_response(content: str, *, total_tokens: int = 0) -> Mock:
    response = Mock()
    response.choices = [Mock(message=Mock(content=content))]
    response.usage = Mock(total_tokens=total_tokens)
    return response


def _valid_validation_response() -> Mock:
    return _chat_completion_response('{"is_valid": true, "confidence": 0.95, "reason": "grounded"}')


def _as_stream(response: Mock) -> list[Mock]:
    """Convert a non-stream mock response into an iterable of stream chunks,
    the shape the async generation path (`async for chunk in stream`) needs
    — `/widget/chat` always streams, unlike the deleted private `/chat`."""
    try:
        content = response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        content = ""
    try:
        total_tokens = response.usage.total_tokens
    except AttributeError:
        total_tokens = 0
    chunks: list[Mock] = []
    if content:
        chunks.append(
            Mock(choices=[Mock(delta=Mock(content=content), finish_reason=None)], usage=None)
        )
    chunks.append(
        Mock(
            choices=[Mock(delta=Mock(content=None), finish_reason="stop")],
            usage=Mock(total_tokens=total_tokens, prompt_tokens=0, completion_tokens=total_tokens),
        )
    )
    return chunks


def _chat_completion_side_effect(answer: str, *, total_tokens: int = 0):
    def _side_effect(*args, **kwargs):
        messages = kwargs.get("messages") or []
        combined_prompt = "\n".join(str(message.get("content", "")) for message in messages if isinstance(message, dict))
        stream = bool(kwargs.get("stream"))
        if "relevance classifier" in combined_prompt:
            response = _chat_completion_response('{"relevant": true, "reason": "test"}')
        elif "You are a fact-checker for a support chatbot." in combined_prompt:
            response = _valid_validation_response()
        else:
            response = _chat_completion_response(answer, total_tokens=total_tokens)
        return _as_stream(response) if stream else response

    return _side_effect


def _bot_public_id(tenant, token: str) -> str:
    r = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "expected at least one bot after tenant bootstrap"
    return items[0]["public_id"]
