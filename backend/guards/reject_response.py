from __future__ import annotations

import enum
from backend.models import TenantProfile as TenantProfileModel


class RejectReason(enum.Enum):
    INJECTION_DETECTED = "injection"
    NOT_RELEVANT = "not_relevant"
    LOW_RETRIEVAL_SCORE = "low_retrieval"
    INSUFFICIENT_CONFIDENCE = "insufficient_confidence"


def build_reject_response(
    *,
    reason: RejectReason,
    profile: TenantProfileModel | None,
) -> str:
    product_name = (
        profile.product_name if profile and profile.product_name else None
    ) or "данному продукту"

    topic_hint = ""
    if profile is not None:
        modules = profile.modules or []
        if isinstance(modules, list) and modules:
            topic_hint = ", ".join([str(m) for m in modules[:3] if str(m).strip()])

    if reason == RejectReason.INJECTION_DETECTED:
        return (
            f"Извините, но я не могу помочь с этим запросом. "
            f"Я могу ответить на вопросы по {product_name}, если нужно."
        )

    if reason == RejectReason.INSUFFICIENT_CONFIDENCE:
        if topic_hint:
            return (
                f"Сейчас у меня недостаточно информации, чтобы надёжно ответить. "
                f"Попробуйте уточнить вопрос или задать его иначе, "
                f"например про {topic_hint}."
            )
        return (
            "Сейчас у меня недостаточно информации, чтобы надёжно ответить. "
            "Попробуйте уточнить вопрос или задать его иначе."
        )

    # NOT_RELEVANT and LOW_RETRIEVAL_SCORE — out-of-domain bucket
    if topic_hint:
        return (
            f"Извините, но я не могу помочь с этим вопросом. "
            f"Я могу ответить на вопросы по {product_name} или его настройкам, "
            f"например про {topic_hint}."
        )
    return (
        f"Извините, но я не могу помочь с этим вопросом. "
        f"Я могу ответить на вопросы по {product_name} или его настройкам."
    )
