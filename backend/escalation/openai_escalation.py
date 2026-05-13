"""OpenAI structured completions for escalation UX (FI-ESC)."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel

from backend.chat.language import (
    localize_text_to_language_result,
    log_llm_tokens,
)
from backend.core.config import settings
from backend.core.openai_client import get_openai_client, is_reasoning_model
from backend.core.openai_retry import call_openai_with_retry
from backend.models import EscalationPhase

logger = logging.getLogger(__name__)

FALLBACK_EN_GENERIC = (
    "We could not load a full reply right now. Your support request is recorded; "
    "the team will follow up by email when possible."
)

# ---------------------------------------------------------------------------
# Pre-confirm phase — static canonical templates.
#
# The pre_confirm phase was previously rendered through ``complete_escalation_
# openai_turn`` together with the handoff phases. That gave the model enough
# rope to (a) ignore "Do NOT mention a ticket number / email", (b) compromise
# between the global handoff rule and the pre_confirm-only rule and emit BOTH
# in one message ("Ваш запрос передан … Хотите, чтобы я передал?"). The two
# bugs in PR-feedback / production screenshots both traced back to that
# overlap.
#
# Pre-confirm content has essentially zero variation by case — it's the same
# one-sentence question every time. We render it via the same canonical-text
# + ``localize_text_to_language_result`` pipeline used for the fallback
# message, which kills the prompt-mixing problem at the root. The yes/no
# classification stays on the LLM, but in a separate narrow call
# (``classify_pre_confirm_reply``) whose only output is the decision label —
# the model has no way to leak a handoff-style ``message_to_user`` because
# the function doesn't ask for one.
# ---------------------------------------------------------------------------

PRE_CONFIRM_QUESTION_EN = (
    "Would you like me to forward your request to our support team "
    "so they can reply by email?"
)
PRE_CONFIRM_CLARIFY_EN = (
    "Just to confirm — should I forward your request to our support team "
    "for an email reply?"
)
PRE_CONFIRM_DECLINED_EN = (
    "Got it, I won't forward this to support. Let me know if you need "
    "anything else here in the chat."
)


class EscalationLlmResult(BaseModel):
    message_to_user: str
    followup_decision: Literal["yes", "no", "unclear"] | None = None
    tokens_used: int = 0


def render_pre_confirm_text(
    *,
    variant: Literal["initial", "clarify", "declined"],
    response_language: str,
    api_key: str,
    tenant_id: str | None = None,
    bot_id: str | None = None,
    chat_id: str | None = None,
) -> EscalationLlmResult:
    """Localize one of the canonical pre_confirm templates.

    Always emits an ``EscalationLlmResult`` with ``followup_decision=None``
    so callers that store ``out.message_to_user`` and ``out.tokens_used``
    keep working unchanged. The variant chooses which canonical phrase
    gets localized — initial question, repeat after an ambiguous reply,
    or polite acknowledgement of a decline.
    """
    canonical = {
        "initial": PRE_CONFIRM_QUESTION_EN,
        "clarify": PRE_CONFIRM_CLARIFY_EN,
        "declined": PRE_CONFIRM_DECLINED_EN,
    }[variant]
    localization = localize_text_to_language_result(
        canonical_text=canonical,
        target_language=response_language,
        api_key=api_key,
        operation=f"pre_confirm_{variant}",
        tenant_id=tenant_id,
        bot_id=bot_id,
        chat_id=chat_id,
    )
    return EscalationLlmResult(
        message_to_user=localization.text,
        followup_decision=None,
        tokens_used=localization.tokens_used,
    )


_PRE_CONFIRM_CLASSIFIER_SYSTEM = (
    "You are a strict classifier. The chat assistant just asked the user "
    "whether to forward their request to a human support team. Decide "
    "whether the latest user message is a direct answer to THAT confirmation "
    "question.\n"
    "\n"
    "Return JSON with a single key `decision`, whose value is one of:\n"
    '  - "yes"     — user clearly accepts the handoff. Apply the same '
    "rule across languages: short affirmatives, polite acceptances, or "
    'explicit "please escalate / forward me to support" all count.\n'
    '  - "no"      — user clearly declines. Apply across languages: '
    'short negatives, "no thanks", "I\'ll figure it out", and the like.\n'
    '  - "unclear" — user is hesitating or asking a meta-question about '
    "the handoff itself.\n"
    "  - null      — user said something substantive that is NOT a yes/no "
    "answer (e.g. described a new problem, asked an unrelated question). "
    "Use null whenever the message is not addressed to the confirmation "
    "question.\n"
    "\n"
    'Output ONLY the JSON object, e.g. {"decision": "yes"}. No prose, '
    "no extra fields, no localised text."
)


def classify_pre_confirm_reply(
    *,
    latest_user_text: str,
    api_key: str,
    model: str | None = None,
    langfuse_observation: Any | None = None,
) -> tuple[Literal["yes", "no", "unclear"] | None, int]:
    """Narrow LLM call: only returns the yes/no/unclear/null decision.

    Returns ``(decision, tokens_used)``. Never raises — defaults to
    ``(None, 0)`` on any failure so the caller can fall through to the
    re-ask path.
    """
    model_name = model or settings.escalation_model
    _reasoning = is_reasoning_model(model_name)
    messages = [
        {"role": "system", "content": _PRE_CONFIRM_CLASSIFIER_SYSTEM},
        {
            "role": "user",
            "content": f"LATEST_USER_MESSAGE:\n{latest_user_text}",
        },
    ]
    try:
        client = get_openai_client(api_key)
        response = call_openai_with_retry(
            "classify_pre_confirm_reply",
            lambda: client.chat.completions.create(
                model=model_name,
                messages=messages,
                **({} if _reasoning else {"temperature": 0}),
                max_completion_tokens=20,
                **({} if _reasoning else {"response_format": {"type": "json_object"}}),
            ),
            langfuse_observation=langfuse_observation,
        )
        raw = response.choices[0].message.content or "{}"
        decision_raw = json.loads(raw).get("decision")
        tokens = response.usage.total_tokens if response.usage else 0
        if decision_raw in ("yes", "no", "unclear"):
            return decision_raw, tokens  # type: ignore[return-value]
        return None, tokens
    except Exception as exc:
        logger.warning("classify_pre_confirm_reply failed: %s", exc)
        return None, 0


ESCALATION_SYSTEM = """You are the same assistant as in the embedded support chat.
You must output a single JSON object with keys:
- "message_to_user" (string): what the user sees in the chat widget.
- "followup_decision" (string or null): set only for phases that need a yes/no classification.

Rules:
- Write message_to_user ONLY in the requested RESPONSE_LANGUAGE tag (this is the language the user is writing in).
- Use only facts from the JSON block: ticket_number, sla_hours, user_email, trigger, phase, clarify_round. Never invent ticket numbers, emails, or SLA.
- For handoff phases (NOT pre_confirm): explain the request has been forwarded to the support team who will reply by email. When user_email is present, confirm the reply will go to that address. When user_email is absent, explain that an email is needed to send the reply and politely ask the user to provide it.
- Do not promise exact response times; you may mention approximate SLA hours from facts.
- When phase requires it, end by asking if you can help with anything else in chat.
- Keep message_to_user concise and calm.

When phase is "pre_confirm": ask the user in one short sentence whether they would like their
request forwarded to the human support team. Do NOT ask for an email address. Do NOT create or
mention a ticket number. Set followup_decision to "yes", "no", or "unclear" ONLY if the latest
user message is a direct answer to this confirmation question — otherwise set it to null so the
question is asked fresh.

When phase is "followup_awaiting_yes_no" or "pre_confirm": you MUST attempt to set
followup_decision from the user's latest message ("yes", "no", or "unclear").
For all other phases, set followup_decision to null.
"""


def _format_thread(chat_messages: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for m in chat_messages:
        role = m.get("role", "")
        content = m.get("content", "")
        lines.append(f"{role.upper()}: {content}")
    return "\n".join(lines)


def complete_escalation_openai_turn(
    *,
    phase: EscalationPhase,
    chat_messages: list[dict[str, str]],
    fact_json: dict[str, Any],
    latest_user_text: str | None,
    api_key: str,
    response_language: str = "en",
    model: str | None = None,
    langfuse_observation: Any | None = None,
) -> EscalationLlmResult:
    """One OpenAI JSON-object completion; never raises on API errors.

    ``response_language`` is the language ``message_to_user`` must be written
    in. Always pass the user's response_language here so the escalation reply
    stays in the language the user is writing in. The tenant-side
    ``escalation_language`` (ticket / support team artifact language) is a
    separate concern and must not be passed to this function.
    """
    model_name = model or settings.escalation_model
    facts = {**fact_json, "phase": phase.value}
    user_block = (
        f"RESPONSE_LANGUAGE:\n{response_language}\n\n"
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
        _esc_reasoning = is_reasoning_model(model_name)
        _esc_max_tokens = (
            settings.chat_response_max_tokens_reasoning
            if _esc_reasoning
            else settings.escalation_max_completion_tokens
        )
        response = call_openai_with_retry(
            "escalation_complete_turn",
            lambda: client.chat.completions.create(
                model=model_name,
                messages=messages,
                **({} if _esc_reasoning else {"temperature": 0.3}),
                max_completion_tokens=_esc_max_tokens,
                **({} if _esc_reasoning else {"response_format": {"type": "json_object"}}),
            ),
            langfuse_observation=langfuse_observation,
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        tokens = response.usage.total_tokens if response.usage else 0
        log_llm_tokens(
            operation="escalate_draft",
            target_language=response_language,
            tokens=tokens,
            model=model_name,
        )
        msg = (data.get("message_to_user") or "").strip()
        if not msg:
            localization = localize_text_to_language_result(
                canonical_text=FALLBACK_EN_GENERIC,
                target_language=response_language,
                api_key=api_key,
            )
            msg = localization.text
            tokens += localization.tokens_used
        fd = data.get("followup_decision")
        followup: Literal["yes", "no", "unclear"] | None = None
        if fd in ("yes", "no", "unclear"):
            followup = fd  # type: ignore[assignment]
        return EscalationLlmResult(
            message_to_user=msg,
            followup_decision=followup,
            tokens_used=tokens,
        )
    except Exception as e:
        logger.exception("complete_escalation_openai_turn failed: %s", e)
        log_llm_tokens(
            operation="escalate_draft",
            target_language=response_language,
            tokens=0,
            model=model_name,
        )
        localization = localize_text_to_language_result(
            canonical_text=FALLBACK_EN_GENERIC,
            target_language=response_language,
            api_key=api_key,
        )
        return EscalationLlmResult(
            message_to_user=localization.text,
            followup_decision=None,
            tokens_used=localization.tokens_used,
        )
