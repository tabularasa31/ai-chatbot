from __future__ import annotations

import enum
from backend.models import TenantProfile as TenantProfileModel


class RejectReason(enum.Enum):
    INJECTION_DETECTED = "injection"
    NOT_RELEVANT = "not_relevant"
    LOW_RETRIEVAL_SCORE = "low_retrieval"


def build_reject_response(
    *,
    reason: RejectReason,
    profile: TenantProfileModel | None,
) -> str:
    topic_hint = ""
    if profile is not None:
        modules = profile.modules or []
        if isinstance(modules, list) and modules:
            topic_hint = ", ".join([str(m) for m in modules[:3] if str(m).strip()])
        elif profile.product_name:
            topic_hint = str(profile.product_name)

    if reason == RejectReason.INJECTION_DETECTED:
        return "Я не могу выполнить этот запрос."

    product_name = (profile.product_name if profile and profile.product_name else None) or "данному продукту"
    if topic_hint:
        return (
            f"Я отвечаю только на вопросы по {product_name}. "
            f"Попробуйте спросить о: {topic_hint}."
        )
    return f"Я отвечаю только на вопросы по {product_name}."

