"""OpenAI structured completions for escalation UX (FI-ESC)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel

from backend.chat.language import (
    async_localize_text_to_language_result,
    log_llm_tokens,
)
from backend.core.config import settings
from backend.core.openai_client import get_async_openai_client, is_reasoning_model
from backend.core.openai_retry import async_call_openai_with_retry
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
# Pre-confirm content used to be rendered from these canonical templates
# alone. That fixed the prompt-mixing bug but made the offer read as a canned
# "forward your request?" even after the user spent four turns describing a
# concrete problem — the bot looked like it gave up without listening, and
# support received a ticket with no problem summary (eval fail in
# loop_doc_upload_ru_003). Bot-initiated variants therefore now go through a
# NARROW context-aware generation call (``_generate_context_pre_confirm``)
# whose prompt only knows how to write the confirmation offer: acknowledge
# the specific problem from the transcript, say what will be summarized for
# the support team, ask for confirmation. It shares nothing with the handoff
# phases' prompt, so the original phase-mixing failure mode ("Ваш запрос
# передан … Хотите, чтобы я передал?") stays structurally impossible; the
# prompt additionally forbids claiming the request was already forwarded and
# inventing ticket numbers / emails. On any failure the render degrades to
# the canonical-template + ``async_localize_text_to_language_result``
# pipeline below, which remains the path for the administrative variants
# (clarify / declined) and for callers without a transcript. The yes/no
# classification stays in its own narrow call
# (``classify_pre_confirm_reply``) whose only output is the decision label —
# the model has no way to leak a handoff-style ``message_to_user`` because
# the function doesn't ask for one.
# ---------------------------------------------------------------------------

PRE_CONFIRM_QUESTION_EN = (
    "Would you like me to forward your request to our support team "
    "so they can reply by email?"
)
# Used when escalation is triggered by a failed knowledge-base lookup (the
# bot has no answer), as opposed to an explicit "connect me to support"
# request. Leads with a brief "I couldn't find an answer" so the handoff
# question doesn't appear out of nowhere.
PRE_CONFIRM_NO_ANSWER_EN = (
    "I couldn't find an answer to this in the available information. "
    "Would you like me to forward your request to our support team "
    "so they can reply by email?"
)
# Used when the user asks HOW to contact support (an informational question)
# and the knowledge base has no contact page. The bot itself is the support
# channel, so we answer about that capability instead of leading with "I
# couldn't find an answer", which wrongly frames the handoff as a failure.
PRE_CONFIRM_SUPPORT_CONTACT_EN = (
    "You can reach our support team right here. "
    "Would you like me to forward your request to our support team "
    "so they can reply by email?"
)
# Used when the relevance guard classified the message as a complaint about
# support being unresponsive (waiting on a reply, being ignored). Leads with
# an apology so the handoff offer reads as a reaction to the frustration, not
# a canned refusal.
PRE_CONFIRM_SUPPORT_COMPLAINT_EN = (
    "I'm sorry you're still waiting for a reply. "
    "Would you like me to forward your request to our support team "
    "so they can follow up by email?"
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


PreConfirmVariant = Literal[
    "initial", "no_answer", "support_contact", "support_complaint", "clarify", "declined"
]

_PRE_CONFIRM_CANONICALS: dict[str, str] = {
    "initial": PRE_CONFIRM_QUESTION_EN,
    "no_answer": PRE_CONFIRM_NO_ANSWER_EN,
    "support_contact": PRE_CONFIRM_SUPPORT_CONTACT_EN,
    "support_complaint": PRE_CONFIRM_SUPPORT_COMPLAINT_EN,
    "clarify": PRE_CONFIRM_CLARIFY_EN,
    "declined": PRE_CONFIRM_DECLINED_EN,
}

# The canonical templates are fixed strings, so their localization for a given
# target language never changes — cache it and pay the localization LLM call at
# most once per (variant, language) per process. The templates are
# tenant-agnostic, so the cache is safely shared across tenants. Bounded by
# variants x languages; no TTL needed.
_PRE_CONFIRM_RENDER_CACHE: dict[tuple[str, str], str] = {}
_PRE_CONFIRM_RENDER_CACHE_MAX = 2048


def pre_confirm_fallback_result(variant: PreConfirmVariant) -> EscalationLlmResult:
    """Canonical (English) pre_confirm text, used when localization cannot run.

    Degrading to the canonical template mirrors the existing missing-api-key
    behaviour of ``async_localize_text_to_language_result`` and keeps the escalation
    FSM armed instead of dropping the handoff on a slow OpenAI call.
    """
    return EscalationLlmResult(
        message_to_user=_PRE_CONFIRM_CANONICALS[variant],
        followup_decision=None,
        tokens_used=0,
    )


# Bot-initiated escalation offers: the user was mid-conversation with the bot
# when the bot decided it cannot help, so there is real dialog context worth
# reflecting back. The administrative variants (initial / clarify / declined)
# are direct reactions to the user's own escalation answer and stay templated.
_PRE_CONFIRM_CONTEXT_VARIANTS: frozenset[str] = frozenset(
    {"no_answer", "support_contact", "support_complaint"}
)

_PRE_CONFIRM_CONTEXT_SYSTEM = """You write the single next assistant message in an embedded support chat.
The assistant could not resolve the user's issue and is about to offer to hand the
conversation over to the human support team. Draft that confirmation offer.

Output a single JSON object: {"message_to_user": "<string>"}. No other keys, no prose.

Rules for message_to_user:
- Write ONLY in the requested RESPONSE_LANGUAGE (the language the user is writing in).
- Ground the message in CHAT_TRANSCRIPT: briefly acknowledge the user's specific
  problem and, when present, what they already tried.
- Say what you will pass along — a one-clause summary of their case (the symptom,
  key details, steps already tried) — so the offer reads as informed, not canned.
- End with ONE short yes/no question asking whether to forward the request to the
  support team so they can reply by email.
- If the transcript contains no concrete problem (e.g. a single short question),
  keep the message general, but still explicitly acknowledge that you could not
  find an answer before asking — never open with the bare forwarding question.
- VARIANT adjustments:
  - support_complaint: open with a brief apology that the user is still waiting.
  - support_contact: the user asked how to reach support — say they can reach the
    team right here, and do NOT frame the handoff as a failure to find an answer.
  - no_answer: no special framing beyond the rules above.
- NEVER: claim the request was already forwarded or a ticket already created,
  invent or mention ticket numbers, ask for or mention email addresses, promise
  response times, or attempt further troubleshooting steps yourself.
- At most three short sentences plus the confirmation question. Calm tone, no lists.
"""


async def _generate_context_pre_confirm(
    *,
    variant: PreConfirmVariant,
    chat_messages: list[dict[str, str]],
    response_language: str,
    api_key: str,
    model: str | None = None,
) -> tuple[str | None, int]:
    """Narrow LLM call: draft a context-aware pre_confirm offer.

    Returns ``(text, tokens_used)``; ``text`` is ``None`` on any failure
    (API error, malformed JSON, empty message) so the caller degrades to
    the canonical-template path. Never raises.
    """
    model_name = model or settings.escalation_model
    _reasoning = is_reasoning_model(model_name)
    user_block = (
        f"RESPONSE_LANGUAGE:\n{response_language}\n\n"
        f"VARIANT:\n{variant}\n\n"
        "CHAT_TRANSCRIPT:\n" + _format_thread(chat_messages)
    )
    messages = [
        {"role": "system", "content": _PRE_CONFIRM_CONTEXT_SYSTEM},
        {"role": "user", "content": user_block},
    ]
    try:
        client = get_async_openai_client(
            api_key, timeout=settings.escalation_openai_timeout_seconds
        )
        response = await async_call_openai_with_retry(
            f"pre_confirm_context_{variant}",
            lambda: client.chat.completions.create(
                model=model_name,
                messages=messages,
                **({} if _reasoning else {"temperature": 0.3}),
                max_completion_tokens=(
                    settings.chat_response_max_tokens_reasoning
                    if _reasoning
                    else settings.escalation_max_completion_tokens
                ),
                **({} if _reasoning else {"response_format": {"type": "json_object"}}),
            ),
        )
        raw = response.choices[0].message.content or "{}"
        text = (json.loads(raw).get("message_to_user") or "").strip()
        tokens = response.usage.total_tokens if response.usage else 0
        log_llm_tokens(
            operation=f"pre_confirm_context_{variant}",
            target_language=response_language,
            tokens=tokens,
            model=model_name,
        )
        return (text or None), tokens
    except Exception as exc:
        logger.warning("context-aware pre_confirm generation failed: %s", exc)
        return None, 0


async def render_pre_confirm_text(
    *,
    variant: PreConfirmVariant,
    response_language: str,
    api_key: str,
    tenant_id: str | None = None,
    bot_id: str | None = None,
    chat_id: str | None = None,
    chat_messages: list[dict[str, str]] | None = None,
) -> EscalationLlmResult:
    """Render the pre_confirm offer, context-aware when a transcript is given.

    Always emits an ``EscalationLlmResult`` with ``followup_decision=None``
    so callers that store ``out.message_to_user`` and ``out.tokens_used``
    keep working unchanged.

    When ``chat_messages`` is provided and the variant is a bot-initiated
    offer (``no_answer`` / ``support_contact`` / ``support_complaint``), the
    text is drafted by a narrow LLM call that summarizes the user's problem
    from the transcript before asking for confirmation. On any failure it
    degrades to the canonical-template path below. The administrative
    variants — bare initial question (explicit human request), repeat after
    an ambiguous reply, polite acknowledgement of a decline — always localize
    their canonical phrase.
    """
    if chat_messages and variant in _PRE_CONFIRM_CONTEXT_VARIANTS:
        text, tokens = await _generate_context_pre_confirm(
            variant=variant,
            chat_messages=chat_messages,
            response_language=response_language,
            api_key=api_key,
        )
        if text:
            # Dialog-specific by construction — never cached.
            return EscalationLlmResult(
                message_to_user=text,
                followup_decision=None,
                tokens_used=tokens,
            )
    canonical = _PRE_CONFIRM_CANONICALS[variant]
    cache_key = (variant, (response_language or "en").lower())
    cached = _PRE_CONFIRM_RENDER_CACHE.get(cache_key)
    if cached is not None:
        return EscalationLlmResult(
            message_to_user=cached,
            followup_decision=None,
            tokens_used=0,
        )
    localization = await async_localize_text_to_language_result(
        canonical_text=canonical,
        target_language=response_language,
        api_key=api_key,
        operation=f"pre_confirm_{variant}",
        tenant_id=tenant_id,
        bot_id=bot_id,
        chat_id=chat_id,
    )
    # Only cache real localizations: a 0-token result for a non-English target
    # means the helper degraded (missing key / detection skip / failure) and
    # should be retried on the next call rather than pinned for the process
    # lifetime.
    if localization.text and (
        localization.tokens_used > 0 or (response_language or "en").lower().startswith("en")
    ):
        if len(_PRE_CONFIRM_RENDER_CACHE) >= _PRE_CONFIRM_RENDER_CACHE_MAX:
            _PRE_CONFIRM_RENDER_CACHE.clear()
        _PRE_CONFIRM_RENDER_CACHE[cache_key] = localization.text
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


async def classify_pre_confirm_reply(
    *,
    latest_user_text: str,
    api_key: str,
    model: str | None = None,
    langfuse_observation: Any | None = None,
) -> tuple[Literal["yes", "no", "unclear"] | None, int]:
    """Narrow LLM call: only returns the yes/no/unclear/null decision.

    Returns ``(decision, tokens_used)``. ``None`` is reserved for a
    *successfully classified* substantive reply that is neither yes/no nor
    a handoff meta-question (the caller drops the pre_confirm gate and
    falls through to RAG on it). Any failure — API error or malformed
    output — returns ``("unclear", 0)`` instead, so a transient outage
    re-asks for confirmation and keeps the gate rather than silently
    dropping it. Never raises.
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
        client = get_async_openai_client(
            api_key, timeout=settings.escalation_openai_timeout_seconds
        )
        response = await async_call_openai_with_retry(
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
        # Fail safe to the re-ask path: never let a transient outage drop the
        # pre_confirm gate (which would ignore a real yes/no and skip handoff).
        return "unclear", 0


_FOLLOWUP_CLASSIFIER_SYSTEM = (
    "You are a strict classifier. The chat assistant just told the user their "
    "request was forwarded to the human support team and asked whether it can "
    "help with anything else in the chat. Classify the latest user message.\n"
    "\n"
    "Return JSON with a single key `decision`, whose value is one of:\n"
    '  - "new_question" — the user asks the assistant a new substantive '
    "question or raises a new topic they expect the assistant to answer in "
    "chat, even if loosely related to the forwarded request. Apply the same "
    "rule across languages.\n"
    '  - "yes"     — a bare affirmative ("yes", "sure", "I have another '
    'question") that contains no actual question content yet.\n'
    '  - "no"      — the user declines, says goodbye, or thanks the '
    "assistant and closes the conversation.\n"
    '  - "unclear" — the message only adds details, corrections, or context '
    "to the request that was already forwarded to support, or hesitates / "
    "asks a meta-question about the handoff itself.\n"
    "\n"
    'Output ONLY the JSON object, e.g. {"decision": "new_question"}. No '
    "prose, no extra fields, no localised text."
)


async def classify_followup_reply(
    *,
    latest_user_text: str,
    api_key: str,
    model: str | None = None,
    langfuse_observation: Any | None = None,
) -> tuple[Literal["yes", "no", "unclear", "new_question"], int]:
    """Narrow LLM gate for the post-handoff follow-up turn.

    Returns ``(decision, tokens_used)``. ``"new_question"`` → the caller
    clears the follow-up gate and falls through to RAG. Any failure or
    unrecognized output returns ``("unclear", 0)`` so the gate is never
    dropped on a transient outage. Never raises.
    """
    model_name = model or settings.escalation_model
    _reasoning = is_reasoning_model(model_name)
    messages = [
        {"role": "system", "content": _FOLLOWUP_CLASSIFIER_SYSTEM},
        {
            "role": "user",
            "content": f"LATEST_USER_MESSAGE:\n{latest_user_text}",
        },
    ]
    try:
        client = get_async_openai_client(
            api_key, timeout=settings.escalation_openai_timeout_seconds
        )
        response = await async_call_openai_with_retry(
            "classify_followup_reply",
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
        if decision_raw in ("yes", "no", "unclear", "new_question"):
            return decision_raw, tokens  # type: ignore[return-value]
        return "unclear", tokens
    except Exception as exc:
        logger.warning("classify_followup_reply failed: %s", exc)
        # Fail safe to the existing follow-up flow: never let a transient
        # outage drop the gate (which would skip closing the chat on a real
        # "no" or lose the ticket-context forwarding on real clarifications).
        return "unclear", 0


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


async def _localized_generic_fallback(
    *, response_language: str, api_key: str
) -> tuple[str, int]:
    """Localize ``FALLBACK_EN_GENERIC`` within the escalation deadline.

    The fallback localization is itself an OpenAI call; when the primary
    completion just timed out, the provider is likely slow across the board,
    so an unbounded localization here would stall the turn a second time
    (for up to the general 60s client timeout). Bound it with the same
    escalation deadline and degrade to the canonical English text — the
    same trade-off ``pre_confirm_fallback_result`` makes. Never raises.
    """
    try:
        localization = await asyncio.wait_for(
            async_localize_text_to_language_result(
                canonical_text=FALLBACK_EN_GENERIC,
                target_language=response_language,
                api_key=api_key,
            ),
            timeout=settings.escalation_openai_timeout_seconds,
        )
        return localization.text, localization.tokens_used
    except Exception:
        # Realistically only wait_for's TimeoutError: the localization helper
        # itself degrades to canonical text instead of raising.
        return FALLBACK_EN_GENERIC, 0


async def complete_escalation_openai_turn(
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
        client = get_async_openai_client(
            api_key, timeout=settings.escalation_openai_timeout_seconds
        )
        _esc_reasoning = is_reasoning_model(model_name)
        _esc_max_tokens = (
            settings.chat_response_max_tokens_reasoning
            if _esc_reasoning
            else settings.escalation_max_completion_tokens
        )
        response = await async_call_openai_with_retry(
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
            msg, fallback_tokens = await _localized_generic_fallback(
                response_language=response_language, api_key=api_key
            )
            tokens += fallback_tokens
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
        msg, fallback_tokens = await _localized_generic_fallback(
            response_language=response_language, api_key=api_key
        )
        return EscalationLlmResult(
            message_to_user=msg,
            followup_decision=None,
            tokens_used=fallback_tokens,
        )
