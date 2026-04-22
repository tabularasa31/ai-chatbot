from __future__ import annotations

import enum

from backend.chat.language import (
    LocalizationResult,
    detect_language,
    localize_text_result,
    localize_text_to_language,
    localize_text_to_language_result,
)
from backend.models import TenantProfile as TenantProfileModel

_SOFT_REJECT_MAX_WORDS = 2  # short inputs get an invite instead of a blunt refusal


class RejectReason(enum.Enum):
    INJECTION_DETECTED = "injection"
    NOT_RELEVANT = "not_relevant"
    LOW_RETRIEVAL_SCORE = "low_retrieval"
    INSUFFICIENT_CONFIDENCE = "insufficient_confidence"


def _resolve_reject_target_language(
    *,
    question: str | None,
    fallback_locale: str | None,
) -> str | None:
    if question and question.strip():
        detection = detect_language(question)
        if detection.is_reliable and detection.detected_language != "unknown":
            return detection.detected_language
    return fallback_locale


def _build_canonical_reject_response(
    *,
    reason: RejectReason,
    profile: TenantProfileModel | None,
    question: str | None = None,
) -> str:
    product_name = (
        profile.product_name if profile and profile.product_name else None
    ) or "this product"

    topic_hint = ""
    if profile is not None:
        modules = profile.modules or []
        if isinstance(modules, list) and modules:
            topic_hint = ", ".join([str(m) for m in modules[:3] if str(m).strip()])

    if reason == RejectReason.INJECTION_DETECTED:
        return (
            f"Sorry, but I can't help with that request. "
            f"I can answer questions about {product_name} if helpful."
        )

    if reason == RejectReason.INSUFFICIENT_CONFIDENCE:
        if topic_hint:
            return (
                "I don't have enough information to answer reliably right now. "
                f"Please clarify your question or ask it another way, for example about {topic_hint}."
            )
        return (
            "I don't have enough information to answer reliably right now. "
            "Please clarify your question or ask it another way."
        )

    # NOT_RELEVANT and LOW_RETRIEVAL_SCORE — out-of-domain bucket.
    # Short inputs (≤ 2 words) are likely greetings or vague prompts that slipped past
    # the small-talk early exit; use a soft invite rather than a blunt refusal.
    if question is not None and len(question.split()) <= _SOFT_REJECT_MAX_WORDS:
        return f"Hi! I'm here to help with {product_name} questions. What would you like to know?"
    if topic_hint:
        return (
            f"Sorry, but I can't help with that question. "
            f"I can answer questions about {product_name} or its settings, "
            f"for example about {topic_hint}."
        )
    return (
        f"Sorry, but I can't help with that question. "
        f"I can answer questions about {product_name} or its settings."
    )


def build_reject_response(
    *,
    reason: RejectReason,
    profile: TenantProfileModel | None,
    response_language: str | None = None,
    api_key: str | None = None,
    question: str | None = None,
    fallback_locale: str | None = None,
) -> str:
    canonical_text = _build_canonical_reject_response(
        reason=reason,
        profile=profile,
        question=question,
    )
    if response_language is None:
        target_language = _resolve_reject_target_language(
            question=question,
            fallback_locale=fallback_locale,
        )
        return localize_text_to_language(
            canonical_text=canonical_text,
            target_language=target_language,
            api_key=api_key,
            fallback_locale=fallback_locale,
        )
    return localize_text_result(
        canonical_text=canonical_text,
        response_language=response_language,
        api_key=api_key,
    ).text


def build_reject_response_result(
    *,
    reason: RejectReason,
    profile: TenantProfileModel | None,
    response_language: str | None = None,
    api_key: str | None = None,
    question: str | None = None,
    fallback_locale: str | None = None,
) -> LocalizationResult:
    canonical_text = _build_canonical_reject_response(
        reason=reason,
        profile=profile,
        question=question,
    )
    if response_language is None:
        target_language = _resolve_reject_target_language(
            question=question,
            fallback_locale=fallback_locale,
        )
        result = localize_text_to_language_result(
            canonical_text=canonical_text,
            target_language=target_language,
            api_key=api_key,
            fallback_locale=fallback_locale,
            operation="reject_guard",
        )
        return result
    result = localize_text_result(
        canonical_text=canonical_text,
        response_language=response_language,
        api_key=api_key,
        operation="reject_guard",
    )
    return result
