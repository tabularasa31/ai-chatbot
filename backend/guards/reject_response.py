from __future__ import annotations

import enum

from backend.chat.language import (
    LocalizationResult,
    localize_text_result,
    localize_text_to_question_language,
    localize_text_to_question_language_result,
)
from backend.models import TenantProfile as TenantProfileModel


class RejectReason(enum.Enum):
    INJECTION_DETECTED = "injection"
    NOT_RELEVANT = "not_relevant"
    LOW_RETRIEVAL_SCORE = "low_retrieval"
    INSUFFICIENT_CONFIDENCE = "insufficient_confidence"


def _build_canonical_reject_response(
    *,
    reason: RejectReason,
    profile: TenantProfileModel | None,
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

    # NOT_RELEVANT and LOW_RETRIEVAL_SCORE — out-of-domain bucket
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
    )
    if response_language is None:
        return localize_text_to_question_language(
            canonical_text=canonical_text,
            question=question,
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
    )
    if response_language is None:
        return localize_text_to_question_language_result(
            canonical_text=canonical_text,
            question=question,
            api_key=api_key,
            fallback_locale=fallback_locale,
        )
    return localize_text_result(
        canonical_text=canonical_text,
        response_language=response_language,
        api_key=api_key,
    )
