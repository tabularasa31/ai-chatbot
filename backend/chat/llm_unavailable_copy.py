# ruff: noqa: RUF001
"""Static i18n table for LLM-unavailable copy.

Why static (and not localize_text_to_language_result): the localize helper
itself calls the LLM, which is precisely what is broken in this code path.
A small lookup table is the only reliable way to surface a fallback string
when OpenAI is unreachable.

English is the canonical fallback for any unknown language tag.
"""

from __future__ import annotations

CopyKey = str  # "fallback_retryable" | "fallback_not_retryable" | "support_notified"

_TABLE: dict[str, dict[CopyKey, str]] = {
    "en": {
        "fallback_retryable": (
            "I'm having trouble answering right now. Please try again, or "
            "contact support if the issue is urgent."
        ),
        "fallback_not_retryable": (
            "I'm unable to answer right now. You can contact support for help."
        ),
        "support_notified": (
            "Support has been notified. Someone from the team will follow up."
        ),
    },
    "ru": {
        "fallback_retryable": (
            "Сейчас у меня проблемы с ответом. Попробуйте ещё раз или "
            "обратитесь в поддержку, если это срочно."
        ),
        "fallback_not_retryable": (
            "Сейчас я не могу ответить. Вы можете обратиться в поддержку."
        ),
        "support_notified": (
            "Поддержка уведомлена. С вами свяжутся."
        ),
    },
}


def _normalize_lang(language: str | None) -> str:
    if not language:
        return "en"
    primary = language.lower().split("-")[0].split("_")[0]
    return primary if primary in _TABLE else "en"


def fallback_text(*, language: str | None, retryable: bool) -> str:
    lang = _normalize_lang(language)
    key = "fallback_retryable" if retryable else "fallback_not_retryable"
    return _TABLE[lang][key]


def support_notified_text(*, language: str | None) -> str:
    return _TABLE[_normalize_lang(language)]["support_notified"]
