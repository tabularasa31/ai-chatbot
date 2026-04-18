from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass
from functools import lru_cache

from openai import APIError

from backend.core.config import settings
from backend.core.openai_client import get_openai_client

logger = logging.getLogger(__name__)

_DETECT_CACHE_MAX_INPUT_CHARS = 512
_DETECT_CACHE_SIZE = 1024

try:
    import langdetect

    DetectorFactory = langdetect.DetectorFactory
    LangDetectError = langdetect.LangDetectException
    detect_langs = langdetect.detect_langs
    DetectorFactory.seed = 0
except ImportError:  # pragma: no cover - optional runtime dependency
    DetectorFactory = None

    class LangDetectError(Exception):  # type: ignore[no-redef]
        """Sentinel raised only by the langdetect library; defined here so that
        ``except LangDetectError`` works even when langdetect is not installed
        without accidentally swallowing unrelated exceptions (as ``Exception`` would)."""

    detect_langs = None


_URL_ONLY_RE = re.compile(r"^(https?://\S+|www\.\S+)$", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u0400-\u04FF\u0600-\u06FF\u3040-\u30FF\u4E00-\u9FFF]+")
_LOG_HINT_RE = re.compile(
    r"(traceback|exception|stack trace|error:|warn(?:ing)?:|INFO\b|DEBUG\b|SELECT\b|INSERT\b|UPDATE\b|DELETE\b)",
    re.IGNORECASE,
)
_LATIN_WORD_HINTS: dict[str, tuple[str, float]] = {
    "bonjour": ("fr", 0.95),
    "merci": ("fr", 0.95),
    "hola": ("es", 0.95),
    "gracias": ("es", 0.95),
    "hallo": ("de", 0.95),
    "guten": ("de", 0.95),
    "olá": ("pt", 0.95),
    "obrigado": ("pt", 0.95),
    "pricing": ("en", 0.95),
    "hello": ("en", 0.95),
    "thanks": ("en", 0.95),
}


@dataclass(frozen=True)
class LanguageDetectionResult:
    detected_language: str
    confidence: float
    is_reliable: bool


@dataclass(frozen=True)
class ResolvedLanguageContext:
    detected_language: str
    confidence: float
    is_reliable: bool
    response_language: str
    response_language_resolution_reason: str
    escalation_language: str
    escalation_language_source: str


@dataclass(frozen=True)
class LocalizationResult:
    text: str
    tokens_used: int = 0


def _threshold() -> float:
    return float(settings.language_detection_reliability_threshold)


def _log_llm_tokens(
    *,
    operation: str,
    target_language: str,
    tokens: int,
    model: str | None = None,
) -> None:
    logger.info(
        "llm_tokens_used",
        extra={
            "operation": operation,
            "target_language": target_language,
            "tokens": int(tokens),
            "model": model or settings.localization_model,
        },
    )


def _normalize_language_tag(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = text.replace("_", "-")
    parts = [part for part in text.split("-") if part]
    if not parts:
        return None

    primary = parts[0].lower()
    if not re.fullmatch(r"[a-z]{2,3}", primary):
        return None

    normalized = [primary]
    for part in parts[1:]:
        if re.fullmatch(r"[a-zA-Z]{4}", part):
            normalized.append(part.title())
            continue
        if re.fullmatch(r"[a-zA-Z]{2}|\d{3}", part):
            normalized.append(part.upper())
            continue
        return "-".join(normalized)

    return "-".join(normalized)


def _language_root(tag: str) -> str:
    return tag.split("-", 1)[0].lower()


def _language_matches(left: str, right: str) -> bool:
    return _language_root(left) == _language_root(right)


def _normalize_config_language(raw: str | None) -> str | None:
    return _normalize_language_tag(raw)


def _looks_undetectable(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if _URL_ONLY_RE.fullmatch(stripped):
        return True
    if not any(ch.isalpha() for ch in stripped):
        return True

    tokens = _TOKEN_RE.findall(stripped)
    if not tokens:
        return True
    if len(tokens) == 1 and len(tokens[0]) <= 3:
        return True
    if len(tokens) <= 2 and max(len(token) for token in tokens) <= 3:
        return True

    punctuation_ratio = sum(1 for ch in stripped if not ch.isalnum() and not ch.isspace()) / max(
        len(stripped), 1
    )
    if punctuation_ratio >= 0.35 and _LOG_HINT_RE.search(stripped):
        return True
    return False


def _heuristic_language_detection(text: str) -> LanguageDetectionResult:
    tokens = _TOKEN_RE.findall(text)
    # Use the token set for word-boundary matching so that "hallo" does not
    # spuriously match inside "halloway" and "pricing" does not match "pricingpage".
    token_set = {t.casefold() for t in tokens}
    for hint, (language, confidence) in _LATIN_WORD_HINTS.items():
        if hint in token_set:
            return LanguageDetectionResult(
                detected_language=language,
                confidence=confidence,
                is_reliable=confidence >= _threshold(),
            )

    if re.search(r"[\u3040-\u30FF]", text):
        return LanguageDetectionResult("ja", 0.99, True)
    if re.search(r"[\u0600-\u06FF]", text):
        return LanguageDetectionResult("ar", 0.99, True)
    if re.search(r"[\u0400-\u04FF]", text):
        return LanguageDetectionResult("ru", 0.95, True)
    if re.search(r"[\u4E00-\u9FFF]", text):
        confidence = 0.85
        return LanguageDetectionResult("zh", confidence, confidence >= _threshold())

    if tokens and all(token.isascii() for token in tokens) and (
        len(tokens) >= 2 or max(len(token) for token in tokens) >= 5
    ):
        confidence = 0.92
        return LanguageDetectionResult("en", confidence, confidence >= _threshold())

    confidence = 0.51
    return LanguageDetectionResult("en", confidence, confidence >= _threshold())


def _detect_language_uncached(text: str) -> LanguageDetectionResult:
    # Guard: reject empty or structurally undetectable input before any heuristic
    # work.  Doing this first avoids running the heuristic on URLs, pure punctuation,
    # log snippets, etc. and prevents those inputs from reaching langdetect.
    if _looks_undetectable(text):
        return LanguageDetectionResult(detected_language="unknown", confidence=0.0, is_reliable=False)

    heuristic = _heuristic_language_detection(text)

    # Fast path: a clearly non-English signal (Cyrillic, CJK, Arabic, known hint words).
    if heuristic.detected_language != "en" and heuristic.is_reliable:
        return heuristic

    # A single ASCII token is too short for langdetect to be reliable; return unknown
    # so the caller falls back to English rather than guessing.
    ascii_tokens = _TOKEN_RE.findall(text)
    if ascii_tokens and all(token.isascii() for token in ascii_tokens) and len(ascii_tokens) == 1:
        return LanguageDetectionResult(detected_language="unknown", confidence=0.0, is_reliable=False)

    # Trust the heuristic for pure-ASCII text that it assessed as English.
    # langdetect can badly misclassify short ASCII phrases at low token counts —
    # e.g. "Reset password" → af (Afrikaans), "question about product" → fr (French) —
    # because it lacks sufficient signal at that length.  Non-ASCII text is still
    # passed to langdetect since the extended character set gives it a reliable signal.
    if (
        heuristic.detected_language == "en"
        and heuristic.is_reliable
        and ascii_tokens
        and all(token.isascii() for token in ascii_tokens)
    ):
        return heuristic

    if detect_langs is not None:
        detections = detect_langs(text)
        if detections:
            top = detections[0]
            normalized = _normalize_language_tag(getattr(top, "lang", None))
            if normalized is None:
                return LanguageDetectionResult(detected_language="unknown", confidence=0.0, is_reliable=False)
            confidence = float(getattr(top, "prob", 0.0) or 0.0)
            return LanguageDetectionResult(
                detected_language=normalized,
                confidence=confidence,
                is_reliable=confidence >= _threshold(),
            )

    # langdetect not installed — reuse the already-computed heuristic result.
    return heuristic


@lru_cache(maxsize=_DETECT_CACHE_SIZE)
def _detect_language_cached(text: str) -> LanguageDetectionResult:
    return _detect_language_uncached(text)


def detect_language(text: str | None) -> LanguageDetectionResult:
    stripped = (text or "").strip()
    if not stripped:
        return LanguageDetectionResult(detected_language="unknown", confidence=0.0, is_reliable=False)
    if len(stripped) > _DETECT_CACHE_MAX_INPUT_CHARS:
        return _detect_language_uncached(stripped)
    return _detect_language_cached(stripped)


def resolve_language_context(
    *,
    current_turn_text: str | None,
    is_bootstrap_turn: bool,
    bootstrap_user_locale: str | None,
    browser_locale: str | None,
    tenant_escalation_language: str | None,
) -> ResolvedLanguageContext:
    escalation_language = _normalize_config_language(tenant_escalation_language) or "en"
    escalation_language_source = "tenant" if _normalize_config_language(tenant_escalation_language) else "default"

    if is_bootstrap_turn:
        bootstrap_language = _normalize_config_language(bootstrap_user_locale)
        if bootstrap_language:
            return ResolvedLanguageContext(
                detected_language="unknown",
                confidence=0.0,
                is_reliable=False,
                response_language=bootstrap_language,
                response_language_resolution_reason="bootstrap_user_locale",
                escalation_language=escalation_language,
                escalation_language_source=escalation_language_source,
            )
        browser_language = _normalize_config_language(browser_locale)
        if browser_language:
            return ResolvedLanguageContext(
                detected_language="unknown",
                confidence=0.0,
                is_reliable=False,
                response_language=browser_language,
                response_language_resolution_reason="browser_locale",
                escalation_language=escalation_language,
                escalation_language_source=escalation_language_source,
            )
        return ResolvedLanguageContext(
            detected_language="unknown",
            confidence=0.0,
            is_reliable=False,
            response_language="en",
            response_language_resolution_reason="bootstrap_default_english",
            escalation_language=escalation_language,
            escalation_language_source=escalation_language_source,
        )

    try:
        detection = detect_language(current_turn_text)
    except LangDetectError:
        return ResolvedLanguageContext(
            detected_language="unknown",
            confidence=0.0,
            is_reliable=False,
            response_language="en",
            response_language_resolution_reason="detector_failure",
            escalation_language=escalation_language,
            escalation_language_source=escalation_language_source,
        )

    if detection.detected_language != "unknown" and detection.is_reliable:
        return ResolvedLanguageContext(
            detected_language=detection.detected_language,
            confidence=detection.confidence,
            is_reliable=detection.is_reliable,
            response_language=detection.detected_language,
            response_language_resolution_reason="detected",
            escalation_language=escalation_language,
            escalation_language_source=escalation_language_source,
        )

    reason = "detector_unknown"
    if detection.detected_language != "unknown":
        reason = "detector_unreliable"
    return ResolvedLanguageContext(
        detected_language=detection.detected_language,
        confidence=detection.confidence,
        is_reliable=detection.is_reliable,
        response_language="en",
        response_language_resolution_reason=reason,
        escalation_language=escalation_language,
        escalation_language_source=escalation_language_source,
    )


def localize_text_result(
    *,
    canonical_text: str,
    response_language: str,
    api_key: str | None,
) -> LocalizationResult:
    if not canonical_text.strip():
        return LocalizationResult(text=canonical_text, tokens_used=0)

    normalized_target = _normalize_language_tag(response_language) or "en"
    if not api_key or _language_matches(normalized_target, "en"):
        _log_llm_tokens(operation="localize", target_language=normalized_target, tokens=0)
        return LocalizationResult(text=canonical_text, tokens_used=0)

    if _already_in_target_language(canonical_text, normalized_target):
        _log_llm_tokens(operation="localize", target_language=normalized_target, tokens=0)
        return LocalizationResult(text=canonical_text, tokens_used=0)

    return _invoke_localize_llm(
        canonical_text=canonical_text,
        target_language=normalized_target,
        api_key=api_key,
        operation="localize",
    )


def translate_text_result(
    *,
    source_text: str,
    target_language: str,
    api_key: str | None,
) -> LocalizationResult:
    if not source_text.strip():
        return LocalizationResult(text=source_text, tokens_used=0)

    normalized_target = _normalize_language_tag(target_language) or "en"
    if not api_key:
        _log_llm_tokens(operation="translate", target_language=normalized_target, tokens=0)
        return LocalizationResult(text=source_text, tokens_used=0)

    if _already_in_target_language(source_text, normalized_target):
        _log_llm_tokens(operation="translate", target_language=normalized_target, tokens=0)
        return LocalizationResult(text=source_text, tokens_used=0)

    try:
        client = get_openai_client(api_key)
        response = client.chat.completions.create(
            model=settings.localization_model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You translate support FAQ answers. Translate the FAQ answer strictly "
                        f"into {normalized_target}. Preserve semantic equivalence and do not "
                        "broaden or invent information beyond the provided answer. Preserve links, "
                        "product names, commands, field names, code snippets, quoted config keys, "
                        "identifiers, placeholders, and ticket tokens exactly. Return only the "
                        "translated FAQ answer."
                    ),
                },
                {
                    "role": "user",
                    "content": f"FAQ answer to translate:\n{source_text}",
                },
            ],
        )
        tokens_used = response.usage.total_tokens if response.usage else 0
        _log_llm_tokens(operation="translate", target_language=normalized_target, tokens=tokens_used)
        if not response.choices:
            return LocalizationResult(text=source_text, tokens_used=tokens_used)
        translated = (response.choices[0].message.content or "").strip()
        return LocalizationResult(text=translated or source_text, tokens_used=tokens_used)
    except (APIError, IndexError) as exc:
        logger.warning("FAQ translation failed; using source text: %s", exc)
        return LocalizationResult(text=source_text, tokens_used=0)


def render_direct_faq_answer_result(
    *,
    answer_text: str,
    response_language: str,
    api_key: str | None,
) -> LocalizationResult:
    normalized_target = _normalize_language_tag(response_language) or "en"
    if _already_in_target_language(answer_text, normalized_target):
        return LocalizationResult(text=answer_text, tokens_used=0)

    return translate_text_result(
        source_text=answer_text,
        target_language=normalized_target,
        api_key=api_key,
    )


def localize_text_to_language_result(
    *,
    canonical_text: str,
    target_language: str | None,
    api_key: str | None,
    fallback_locale: str | None = None,
) -> LocalizationResult:
    if not canonical_text.strip():
        return LocalizationResult(text=canonical_text, tokens_used=0)

    normalized_target = (
        _normalize_language_tag(target_language)
        or _normalize_language_tag(fallback_locale)
        or "en"
    )
    if not api_key:
        _log_llm_tokens(operation="localize_to_language", target_language=normalized_target, tokens=0)
        return LocalizationResult(text=canonical_text, tokens_used=0)
    if _language_matches(normalized_target, "en"):
        _log_llm_tokens(operation="localize_to_language", target_language=normalized_target, tokens=0)
        return LocalizationResult(text=canonical_text, tokens_used=0)
    if _already_in_target_language(canonical_text, normalized_target):
        _log_llm_tokens(operation="localize_to_language", target_language=normalized_target, tokens=0)
        return LocalizationResult(text=canonical_text, tokens_used=0)

    return _invoke_localize_llm(
        canonical_text=canonical_text,
        target_language=normalized_target,
        api_key=api_key,
        operation="localize_to_language",
    )


def localize_text_to_language(
    *,
    canonical_text: str,
    target_language: str | None,
    api_key: str | None,
    fallback_locale: str | None = None,
) -> str:
    return localize_text_to_language_result(
        canonical_text=canonical_text,
        target_language=target_language,
        api_key=api_key,
        fallback_locale=fallback_locale,
    ).text


def localize_text_to_question_language_result(
    *,
    canonical_text: str,
    question: str | None,
    api_key: str | None,
    fallback_locale: str | None = None,
) -> LocalizationResult:
    warnings.warn(
        "localize_text_to_question_language_result is deprecated; resolve language via "
        "resolve_language_context and call localize_text_to_language_result",
        DeprecationWarning,
        stacklevel=2,
    )
    detected = detect_language(question or "")
    target_language = (
        detected.detected_language
        if detected.is_reliable and detected.detected_language != "unknown"
        else fallback_locale
    )
    return localize_text_to_language_result(
        canonical_text=canonical_text,
        target_language=target_language,
        api_key=api_key,
        fallback_locale=fallback_locale,
    )


def _already_in_target_language(text: str, target: str) -> bool:
    try:
        detection = detect_language(text)
    except Exception:  # pragma: no cover
        return False
    if not detection.is_reliable or detection.detected_language == "unknown":
        return False
    return _language_matches(detection.detected_language, target)


def _invoke_localize_llm(
    *,
    canonical_text: str,
    target_language: str,
    api_key: str | None,
    operation: str,
) -> LocalizationResult:
    try:
        client = get_openai_client(api_key)
        response = client.chat.completions.create(
            model=settings.localization_model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You localize assistant messages. Rewrite the assistant message strictly "
                        f"in {target_language}. Preserve meaning, tone, product names, module "
                        "names, placeholders, quoted config keys, commands, code snippets, links, "
                        "and ticket tokens exactly. Return only the localized assistant message."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Assistant message to localize:\n{canonical_text}",
                },
            ],
        )
        tokens_used = response.usage.total_tokens if response.usage else 0
        _log_llm_tokens(operation=operation, target_language=target_language, tokens=tokens_used)
        if not response.choices:
            return LocalizationResult(text=canonical_text, tokens_used=tokens_used)
        localized = (response.choices[0].message.content or "").strip()
        return LocalizationResult(text=localized or canonical_text, tokens_used=tokens_used)
    except (APIError, IndexError) as exc:
        logger.warning("Localization failed; using canonical text: %s", exc)
        return LocalizationResult(text=canonical_text, tokens_used=0)
