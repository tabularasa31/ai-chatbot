from __future__ import annotations

import logging
import re
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.core.openai_retry import call_openai_with_retry
from backend.observability.metrics import capture_event

logger = logging.getLogger(__name__)

_DETECT_CACHE_MAX_INPUT_CHARS = 512
_DETECT_CACHE_SIZE = 1024
STICKY_WINDOW = 3
_STICKY_WEIGHTS = [3, 2, 1]
_STICKY_SWITCH_MARGIN = 2

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


_RESOLUTION_REASON_TO_SOURCE = {
    "bootstrap_user_locale": "default",
    "browser_locale": "default",
    "bootstrap_default_english": "default",
    "sticky_no_signal": "sticky",
    "sticky_retained": "sticky",
    "sticky_switched": "sticky",
    "detected": "detector",
    "detector_unknown": "detector",
    "detector_unreliable": "detector",
    "detector_failure": "detector",
}


def _metrics_distinct_id(
    bot_id: str | None,
    tenant_id: str | None,
) -> str:
    return bot_id or tenant_id or "unknown"


def _emit_language_resolved_event(
    *,
    context: ResolvedLanguageContext,
    text_length: int,
    tenant_id: str | None,
    bot_id: str | None,
    chat_id: str | None,
) -> None:
    capture_event(
        "language.resolved",
        distinct_id=_metrics_distinct_id(bot_id, tenant_id),
        tenant_id=tenant_id,
        bot_id=bot_id,
        properties={
            "detected": context.detected_language,
            "final": context.response_language,
            "source": _RESOLUTION_REASON_TO_SOURCE.get(
                context.response_language_resolution_reason,
                "default",
            ),
            "resolution_reason": context.response_language_resolution_reason,
            "confidence": context.confidence,
            "text_length": text_length,
            "chat_id": chat_id,
        },
    )


def log_llm_tokens(
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
    # For multi-token inputs apply all hints.  For single-token inputs, apply
    # only non-English hints: "bonjour" or "hola" are strong first-turn signals
    # even alone, but "hello" / "pricing" / "thanks" are too common in any
    # language context and cause false positives in established conversations.
    if tokens:
        token_set = {t.casefold() for t in tokens}
        single_token = len(tokens) == 1
        for hint, (language, confidence) in _LATIN_WORD_HINTS.items():
            if single_token and language == "en":
                continue
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


def _weighted_vote(texts: list[str]) -> tuple[str | None, dict[str, int]]:
    votes: dict[str, int] = defaultdict(int)
    for text, weight in zip(texts[:STICKY_WINDOW], _STICKY_WEIGHTS, strict=False):
        try:
            detection = detect_language(text)
        except LangDetectError:
            continue
        if not detection.is_reliable or detection.detected_language == "unknown":
            continue
        votes[_language_root(detection.detected_language)] += weight
    if not votes:
        return None, {}
    winner = max(votes.items(), key=lambda item: item[1])[0]
    return winner, dict(votes)


def resolve_language_context(
    *,
    current_turn_text: str | None,
    is_bootstrap_turn: bool,
    bootstrap_user_locale: str | None,
    browser_locale: str | None,
    tenant_escalation_language: str | None,
    previous_response_language: str | None = None,
    recent_user_turn_texts: list[str] | None = None,
    tenant_id: str | None = None,
    bot_id: str | None = None,
    chat_id: str | None = None,
) -> ResolvedLanguageContext:
    context = _resolve_language_context_inner(
        current_turn_text=current_turn_text,
        is_bootstrap_turn=is_bootstrap_turn,
        bootstrap_user_locale=bootstrap_user_locale,
        browser_locale=browser_locale,
        tenant_escalation_language=tenant_escalation_language,
        previous_response_language=previous_response_language,
        recent_user_turn_texts=recent_user_turn_texts,
        tenant_id=tenant_id,
        bot_id=bot_id,
        chat_id=chat_id,
    )
    _emit_language_resolved_event(
        context=context,
        text_length=len(current_turn_text or ""),
        tenant_id=tenant_id,
        bot_id=bot_id,
        chat_id=chat_id,
    )
    return context


def _resolve_language_context_inner(
    *,
    current_turn_text: str | None,
    is_bootstrap_turn: bool,
    bootstrap_user_locale: str | None,
    browser_locale: str | None,
    tenant_escalation_language: str | None,
    previous_response_language: str | None = None,
    recent_user_turn_texts: list[str] | None = None,
    tenant_id: str | None = None,
    bot_id: str | None = None,
    chat_id: str | None = None,
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
        capture_event(
            "language.detect_fallback",
            distinct_id=_metrics_distinct_id(bot_id, tenant_id),
            tenant_id=tenant_id,
            bot_id=bot_id,
            properties={
                "reason": "langdetect_error",
                "text_length": len(current_turn_text or ""),
                "chat_id": chat_id,
            },
        )
        return ResolvedLanguageContext(
            detected_language="unknown",
            confidence=0.0,
            is_reliable=False,
            response_language="en",
            response_language_resolution_reason="detector_failure",
            escalation_language=escalation_language,
            escalation_language_source=escalation_language_source,
        )

    if detection.detected_language == "unknown":
        capture_event(
            "language.detect_fallback",
            distinct_id=_metrics_distinct_id(bot_id, tenant_id),
            tenant_id=tenant_id,
            bot_id=bot_id,
            properties={
                "reason": "detector_returned_unknown",
                "text_length": len(current_turn_text or ""),
                "chat_id": chat_id,
            },
        )

    recent_turns = [text for text in (recent_user_turn_texts or [current_turn_text or ""]) if str(text or "").strip()]
    winner, votes = _weighted_vote(recent_turns)

    if winner is None:
        if previous_response_language:
            return ResolvedLanguageContext(
                detected_language=detection.detected_language,
                confidence=detection.confidence,
                is_reliable=detection.is_reliable,
                response_language=previous_response_language,
                response_language_resolution_reason="sticky_no_signal",
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

    previous_root = _language_root(previous_response_language) if previous_response_language else None
    if previous_root and previous_root != winner:
        previous_score = votes.get(previous_root, 0)
        winner_score = votes.get(winner, 0)
        if winner_score - previous_score < _STICKY_SWITCH_MARGIN:
            return ResolvedLanguageContext(
                detected_language=detection.detected_language,
                confidence=detection.confidence,
                is_reliable=detection.is_reliable,
                response_language=previous_response_language,
                response_language_resolution_reason="sticky_retained",
                escalation_language=escalation_language,
                escalation_language_source=escalation_language_source,
            )

    resolution_reason = "detected"
    if previous_root and previous_root != winner:
        resolution_reason = "sticky_switched"
        capture_event(
            "language.switched",
            distinct_id=_metrics_distinct_id(bot_id, tenant_id),
            tenant_id=tenant_id,
            bot_id=bot_id,
            properties={
                "from": previous_response_language,
                "to": winner,
                "window_weights": votes,
                "margin": votes.get(winner, 0) - votes.get(previous_root, 0),
                "chat_id": chat_id,
            },
        )
    response_language = winner
    for text in recent_turns[:STICKY_WINDOW]:
        try:
            recent_detection = detect_language(text)
        except LangDetectError:
            continue
        if not recent_detection.is_reliable or recent_detection.detected_language == "unknown":
            continue
        if _language_root(recent_detection.detected_language) == winner:
            response_language = recent_detection.detected_language
            break
    return ResolvedLanguageContext(
        detected_language=detection.detected_language,
        confidence=detection.confidence,
        is_reliable=detection.is_reliable,
        response_language=response_language,
        response_language_resolution_reason=resolution_reason,
        escalation_language=escalation_language,
        escalation_language_source=escalation_language_source,
    )


def localize_text_result(
    *,
    canonical_text: str,
    response_language: str,
    api_key: str | None,
    operation: str = "localize",
    tenant_id: str | None = None,
    bot_id: str | None = None,
    chat_id: str | None = None,
) -> LocalizationResult:
    if not canonical_text.strip():
        return LocalizationResult(text=canonical_text, tokens_used=0)

    normalized_target = _normalize_language_tag(response_language) or "en"
    if not api_key or _language_matches(normalized_target, "en"):
        log_llm_tokens(operation=operation, target_language=normalized_target, tokens=0)
        return LocalizationResult(text=canonical_text, tokens_used=0)

    if _already_in_target_language(canonical_text, normalized_target):
        log_llm_tokens(operation=operation, target_language=normalized_target, tokens=0)
        return LocalizationResult(text=canonical_text, tokens_used=0)

    return _invoke_localize_llm(
        canonical_text=canonical_text,
        target_language=normalized_target,
        api_key=api_key,
        operation=operation,
        tenant_id=tenant_id,
        bot_id=bot_id,
        chat_id=chat_id,
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
        log_llm_tokens(operation="translate", target_language=normalized_target, tokens=0)
        return LocalizationResult(text=source_text, tokens_used=0)

    if _already_in_target_language(source_text, normalized_target):
        log_llm_tokens(operation="translate", target_language=normalized_target, tokens=0)
        return LocalizationResult(text=source_text, tokens_used=0)

    try:
        client = get_openai_client(api_key)
        response = call_openai_with_retry(
            "chat_translate",
            lambda: client.chat.completions.create(
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
            ),
        )
        tokens_used = response.usage.total_tokens if response.usage else 0
        log_llm_tokens(operation="translate", target_language=normalized_target, tokens=tokens_used)
        if not response.choices:
            return LocalizationResult(text=source_text, tokens_used=tokens_used)
        translated = (response.choices[0].message.content or "").strip()
        return LocalizationResult(text=translated or source_text, tokens_used=tokens_used)
    except Exception as exc:
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
    operation: str = "localize_to_language",
    tenant_id: str | None = None,
    bot_id: str | None = None,
    chat_id: str | None = None,
) -> LocalizationResult:
    if not canonical_text.strip():
        return LocalizationResult(text=canonical_text, tokens_used=0)

    normalized_target = (
        _normalize_language_tag(target_language)
        or _normalize_language_tag(fallback_locale)
        or "en"
    )
    if not api_key:
        log_llm_tokens(operation=operation, target_language=normalized_target, tokens=0)
        return LocalizationResult(text=canonical_text, tokens_used=0)
    if _language_matches(normalized_target, "en"):
        log_llm_tokens(operation=operation, target_language=normalized_target, tokens=0)
        return LocalizationResult(text=canonical_text, tokens_used=0)
    if _already_in_target_language(canonical_text, normalized_target):
        log_llm_tokens(operation=operation, target_language=normalized_target, tokens=0)
        return LocalizationResult(text=canonical_text, tokens_used=0)

    return _invoke_localize_llm(
        canonical_text=canonical_text,
        target_language=normalized_target,
        api_key=api_key,
        operation=operation,
        tenant_id=tenant_id,
        bot_id=bot_id,
        chat_id=chat_id,
    )


def localize_text_to_language(
    *,
    canonical_text: str,
    target_language: str | None,
    api_key: str | None,
    fallback_locale: str | None = None,
    tenant_id: str | None = None,
    bot_id: str | None = None,
    chat_id: str | None = None,
) -> str:
    return localize_text_to_language_result(
        canonical_text=canonical_text,
        target_language=target_language,
        api_key=api_key,
        fallback_locale=fallback_locale,
        tenant_id=tenant_id,
        bot_id=bot_id,
        chat_id=chat_id,
    ).text


def localize_text_to_question_language_result(
    *,
    canonical_text: str,
    question: str | None,
    api_key: str | None,
    fallback_locale: str | None = None,
    tenant_id: str | None = None,
    bot_id: str | None = None,
    chat_id: str | None = None,
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
        tenant_id=tenant_id,
        bot_id=bot_id,
        chat_id=chat_id,
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
    tenant_id: str | None = None,
    bot_id: str | None = None,
    chat_id: str | None = None,
) -> LocalizationResult:
    started_at = time.monotonic()
    try:
        client = get_openai_client(api_key)
        response = call_openai_with_retry(
            operation,
            lambda: client.chat.completions.create(
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
            ),
        )
        tokens_used = response.usage.total_tokens if response.usage else 0
        log_llm_tokens(operation=operation, target_language=target_language, tokens=tokens_used)
        if not response.choices:
            return LocalizationResult(text=canonical_text, tokens_used=tokens_used)
        localized = (response.choices[0].message.content or "").strip()
        output_text = localized or canonical_text
        _emit_localized_event_safely(
            canonical_text=canonical_text,
            output_text=output_text,
            target_language=target_language,
            operation=operation,
            started_at=started_at,
            tenant_id=tenant_id,
            bot_id=bot_id,
            chat_id=chat_id,
        )
        return LocalizationResult(text=output_text, tokens_used=tokens_used)
    except Exception as exc:
        logger.warning("Localization failed; using canonical text: %s", exc)
        return LocalizationResult(text=canonical_text, tokens_used=0)


def _emit_localized_event_safely(
    *,
    canonical_text: str,
    output_text: str,
    target_language: str,
    operation: str,
    started_at: float,
    tenant_id: str | None,
    bot_id: str | None,
    chat_id: str | None,
) -> None:
    # Skip when neither identifier is known — emitting would collapse all
    # such events under distinct_id="unknown" and pollute per-tenant rollups.
    # Real production callers gain identifiers in a follow-up PR.
    if tenant_id is None and bot_id is None:
        return
    try:
        try:
            source_lang = detect_language(canonical_text).detected_language
        except Exception:
            source_lang = "unknown"
        capture_event(
            "language.localized",
            distinct_id=_metrics_distinct_id(bot_id, tenant_id),
            tenant_id=tenant_id,
            bot_id=bot_id,
            properties={
                "source_lang": source_lang,
                "target_lang": target_language,
                "input_chars": len(canonical_text),
                "output_chars": len(output_text),
                "latency_ms": int((time.monotonic() - started_at) * 1000),
                "operation": operation,
                "chat_id": chat_id,
            },
        )
    except Exception:
        logger.warning("Failed to emit language.localized event", exc_info=True)
