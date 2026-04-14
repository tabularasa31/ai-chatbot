"""OpenAI structured completions for escalation UX (FI-ESC)."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel

from backend.chat.language import (
    localize_text_result,
    localize_text_to_question_language_result,
)
from backend.core.openai_client import get_openai_client
from backend.models import EscalationPhase

logger = logging.getLogger(__name__)

FALLBACK_EN_GENERIC = (
    "We could not load a full reply right now. Your support request is recorded; "
    "the team will follow up by email when possible."
)


class EscalationLlmResult(BaseModel):
    message_to_user: str
    followup_decision: Literal["yes", "no", "unclear"] | None = None
    tokens_used: int = 0


ESCALATION_SYSTEM = """You are the same assistant as in the embedded support chat.
You must output a single JSON object with keys:
- "message_to_user" (string): what the user sees in the chat widget.
- "followup_decision" (string or null): only when phase in the facts is "followup_awaiting_yes_no".
  Set to "yes", "no", or "unclear" based on the latest user message.

Rules:
- Write message_to_user ONLY in the requested ESCALATION_LANGUAGE tag.
- Use only facts from the JSON block: ticket_number, sla_hours, user_email, trigger, phase, clarify_round. Never invent ticket numbers, emails, or SLA.
- Explain that the request was passed to human support; they will reply by email at the given email when user_email is present; otherwise politely ask for an email address.
- Do not promise exact response times; you may mention approximate SLA hours from facts.
- When phase requires it, end by asking if you can help with anything else in chat.
- Keep message_to_user concise and calm.

When phase is "followup_awaiting_yes_no", you MUST set followup_decision from the user's latest message (yes/no/unclear). For other phases, set followup_decision to null.
"""


def _format_thread(chat_messages: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for m in chat_messages:
        role = m.get("role", "")
        content = m.get("content", "")
        lines.append(f"{role.upper()}: {content}")
    return "\n".join(lines)


def _append_ticket_token(text: str, ticket_number: str | None) -> str:
    if not ticket_number:
        return text
    token = f"[[escalation_ticket:{ticket_number}]]"
    if token in text:
        return text
    return text.rstrip() + "\n\n" + token


def complete_escalation_openai_turn(
    *,
    phase: EscalationPhase,
    chat_messages: list[dict[str, str]],
    fact_json: dict[str, Any],
    latest_user_text: str | None,
    api_key: str,
    escalation_language: str = "en",
    model: str | None = None,
) -> EscalationLlmResult:
    """One OpenAI JSON-object completion; never raises on API errors."""
    model_name = model or "gpt-4o-mini"
    facts = {**fact_json, "phase": phase.value}
    user_block = (
        f"ESCALATION_LANGUAGE:\n{escalation_language}\n\n"
        "ESCALATION_FACTS_JSON:\n"
        + json.dumps(facts, ensure_ascii=False)
        + "\n\nCHAT_TRANSCRIPT:\n"
        + _format_thread(chat_messages)
    )
    if latest_user_text is not None:
        user_block += f"\n\nLATEST_USER_MESSAGE:\n{latest_user_text}"

    messages = [
        {"role": "system", "content": ESCALATION_SYSTEM},
        {"role": "user", "content": user_block},
    ]

    try:
        client = get_openai_client(api_key)
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.3,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        tokens = response.usage.total_tokens if response.usage else 0
        msg = (data.get("message_to_user") or "").strip()
        if not msg:
            localization = localize_text_to_question_language_result(
                canonical_text=FALLBACK_EN_GENERIC,
                question=None,
                fallback_locale=escalation_language,
                api_key=api_key,
            )
            msg = localization.text
            tokens += localization.tokens_used
        fd = data.get("followup_decision")
        followup: Literal["yes", "no", "unclear"] | None = None
        if fd in ("yes", "no", "unclear"):
            followup = fd  # type: ignore[assignment]
        tn = fact_json.get("ticket_number")
        if isinstance(tn, str):
            msg = _append_ticket_token(msg, tn)
        return EscalationLlmResult(
            message_to_user=msg,
            followup_decision=followup,
            tokens_used=tokens,
        )
    except Exception as e:
        logger.exception("complete_escalation_openai_turn failed: %s", e)
        tn = fact_json.get("ticket_number")
        localization = localize_text_to_question_language_result(
            canonical_text=FALLBACK_EN_GENERIC,
            question=None,
            fallback_locale=escalation_language,
            api_key=api_key,
        )
        fb = localization.text
        if isinstance(tn, str):
            fb = _append_ticket_token(fb, tn)
        return EscalationLlmResult(
            message_to_user=fb,
            followup_decision=None,
            tokens_used=localization.tokens_used,
        )
