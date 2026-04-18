from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

SUPPORTED_QUICK_ANSWER_KEYS = {
    "support_email",
    "documentation_url",
    "pricing_url",
    "trial_info",
    "status_page_url",
    "support_chat",
}

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_TRIAL_CORE_RE = re.compile(
    r"(?:free trial|trial period|(?P<days>[0-9]{1,3})[- ]day (?:free )?trial)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_PRICING_TEXT_RE = re.compile(r"\b(pricing|plans?|tariff|tarif)\b", re.IGNORECASE)
_DOCS_TEXT_RE = re.compile(
    r"\b(docs|documentation|developer docs|help center|knowledge base)\b",
    re.IGNORECASE,
)
_STATUS_TEXT_RE = re.compile(r"\b(status|status page|system status)\b", re.IGNORECASE)
_STATUS_URL_RE = re.compile(r"(?:^|\.)status\.|statuspage\.io|instatus\.com|/status(?:/|$)", re.IGNORECASE)
_SUPPORT_EMAIL_CONTEXT_RE = re.compile(r"\b(support|help|customer care|customer success)\b", re.IGNORECASE)
_SUPPORT_CHAT_PATTERNS: dict[str, re.Pattern[str]] = {
    "Intercom": re.compile(r"intercom", re.IGNORECASE),
    "Crisp": re.compile(r"\$crisp|crisp\.chat|crisp-client", re.IGNORECASE),
    "Drift": re.compile(r"drift", re.IGNORECASE),
}
_EMAIL_LOCAL_BLOCKLIST = frozenset(
    {
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "notifications",
        "notification",
        "mailer-daemon",
        "postmaster",
        "bounce",
        "bounces",
        "privacy",
        "legal",
        "compliance",
        "gdpr",
        "dpo",
        "dmca",
        "abuse",
        "security",
        "press",
        "pr",
        "media",
        "investors",
        "ir",
        "jobs",
        "careers",
        "recruiting",
        "hr",
        "talent",
        "marketing",
        "newsletter",
        "subscribe",
        "unsubscribe",
        "webmaster",
        "admin",
        "root",
    }
)
_EMAIL_MAX_LOCAL = 40
_EMAIL_MAX_TOTAL = 120
_TRIAL_MAX_LEN = 240
logger = logging.getLogger(__name__)
EMAIL_MAX_LOCAL = _EMAIL_MAX_LOCAL
EMAIL_MAX_TOTAL = _EMAIL_MAX_TOTAL
TRIAL_MAX_LEN = _TRIAL_MAX_LEN


@dataclass
class QuickAnswerCandidate:
    key: str
    value: str
    source_url: str
    score: int
    metadata: dict[str, str]


def _log_rejection(*, key: str, reason: str, source_url: str, value: str) -> None:
    logger.info(
        "quick_answer_rejected",
        extra={
            "key": key,
            "reason": reason,
            "source_url": source_url,
            "value_preview": value[:50],
        },
    )


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path or "/"
    if path.endswith("/") and path != "/":
        path = path[:-1]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _same_host(url: str, root_url: str) -> bool:
    return urlparse(url).netloc.lower() == urlparse(root_url).netloc.lower()


def _candidate(
    key: str,
    value: str,
    source_url: str,
    score: int,
    **metadata: str,
) -> QuickAnswerCandidate:
    return QuickAnswerCandidate(
        key=key,
        value=value.strip(),
        source_url=source_url,
        score=score,
        metadata={k: v for k, v in metadata.items() if v},
    )


def _pick_better(
    current: dict[str, QuickAnswerCandidate],
    candidate: QuickAnswerCandidate | None,
) -> None:
    if candidate is None or not candidate.value:
        return
    existing = current.get(candidate.key)
    if existing is None or candidate.score > existing.score:
        current[candidate.key] = candidate


def _is_acceptable_support_email(value: str, *, page_url: str) -> bool:
    if len(value) > _EMAIL_MAX_TOTAL:
        _log_rejection(
            key="support_email",
            reason="length",
            source_url=page_url,
            value=value,
        )
        return False
    try:
        local, domain = value.split("@", 1)
    except ValueError:
        _log_rejection(
            key="support_email",
            reason="malformed",
            source_url=page_url,
            value=value,
        )
        return False
    if len(local) > _EMAIL_MAX_LOCAL or not local or not domain:
        _log_rejection(
            key="support_email",
            reason="length" if len(local) > _EMAIL_MAX_LOCAL else "malformed",
            source_url=page_url,
            value=value,
        )
        return False
    normalized_local = local.lower()
    if normalized_local in _EMAIL_LOCAL_BLOCKLIST:
        _log_rejection(
            key="support_email",
            reason="local_blocklist",
            source_url=page_url,
            value=value,
        )
        return False
    if ".." in local or local.startswith(".") or local.endswith("."):
        _log_rejection(
            key="support_email",
            reason="malformed",
            source_url=page_url,
            value=value,
        )
        return False
    if ".." in domain or domain.startswith(".") or domain.endswith("."):
        _log_rejection(
            key="support_email",
            reason="malformed",
            source_url=page_url,
            value=value,
        )
        return False
    return True


def is_acceptable_support_email(value: str, *, page_url: str) -> bool:
    return _is_acceptable_support_email(value, page_url=page_url)


def _extract_support_email(soup: BeautifulSoup, text: str, page_url: str) -> QuickAnswerCandidate | None:
    best_mailto: QuickAnswerCandidate | None = None
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        if href.lower().startswith("mailto:"):
            email_match = _EMAIL_RE.search(href)
            if email_match:
                value = email_match.group(0)
                if not _is_acceptable_support_email(value, page_url=page_url):
                    continue
                local_part = value.split("@", 1)[0]
                context_parts = [
                    anchor.get_text(" ", strip=True),
                    anchor.get("title") or "",
                    anchor.get("aria-label") or "",
                ]
                parent = anchor.parent
                if parent is not None:
                    context_parts.append(parent.get_text(" ", strip=True))
                context = " ".join(part for part in context_parts if part)
                score = 100
                if _SUPPORT_EMAIL_CONTEXT_RE.search(local_part):
                    score += 15
                if _SUPPORT_EMAIL_CONTEXT_RE.search(context):
                    score += 20
                candidate = _candidate("support_email", value, page_url, score, method="mailto")
                if best_mailto is None or candidate.score > best_mailto.score:
                    best_mailto = candidate
    if best_mailto is not None:
        return best_mailto
    match = _EMAIL_RE.search(text)
    if match:
        value = match.group(0)
        if _is_acceptable_support_email(value, page_url=page_url):
            return _candidate("support_email", value, page_url, 70, method="regex")
    return None


def _extract_documentation_url(soup: BeautifulSoup, page_url: str, root_url: str) -> QuickAnswerCandidate | None:
    """Returns None if no documentation anchor is found; do not fall back to the site root."""

    best: QuickAnswerCandidate | None = None
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        text = anchor.get_text(" ", strip=True)
        if not href or not _DOCS_TEXT_RE.search(text):
            continue
        joined = urljoin(page_url, href)
        if not _is_http_url(joined):
            continue
        score = 90 if _same_host(joined, root_url) else 40
        candidate = _candidate(
            "documentation_url",
            _normalize_url(joined),
            page_url,
            score,
            method="anchor",
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def _extract_pricing_url(soup: BeautifulSoup, page_url: str, root_url: str) -> QuickAnswerCandidate | None:
    best: QuickAnswerCandidate | None = None
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        text = anchor.get_text(" ", strip=True)
        if not href:
            continue
        joined = urljoin(page_url, href)
        if not _is_http_url(joined):
            continue
        normalized = _normalize_url(joined)
        score = 0
        if _PRICING_TEXT_RE.search(text):
            score += 70
        if "/pricing" in urlparse(normalized).path.lower():
            score += 25
        if score <= 0:
            continue
        if _same_host(normalized, root_url):
            score += 5
        candidate = _candidate("pricing_url", normalized, page_url, score, method="anchor")
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def _extract_trial_info(text: str, page_url: str) -> QuickAnswerCandidate | None:
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        normalized = " ".join(sentence.split())
        if not normalized:
            continue
        match = _TRIAL_CORE_RE.search(normalized)
        if not match:
            continue
        days = match.group("days")
        if days and not (1 <= int(days) <= 90):
            _log_rejection(
                key="trial_info",
                reason="days_out_of_range",
                source_url=page_url,
                value=normalized,
            )
            continue
        value = normalized
        if len(value) > _TRIAL_MAX_LEN:
            cut = value.rfind(" ", 0, _TRIAL_MAX_LEN - 1)
            if cut < 100:
                cut = _TRIAL_MAX_LEN - 1
            value = value[:cut].rstrip(",;:") + "…"
        return _candidate("trial_info", value, page_url, 80, method="regex")
    return None


def _extract_status_page_url(soup: BeautifulSoup, page_url: str) -> QuickAnswerCandidate | None:
    best: QuickAnswerCandidate | None = None
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        text = anchor.get_text(" ", strip=True)
        if not href:
            continue
        joined = urljoin(page_url, href)
        if not _is_http_url(joined):
            continue
        normalized = _normalize_url(joined)
        score = 0
        if _STATUS_URL_RE.search(normalized):
            score += 80
        if _STATUS_TEXT_RE.search(text):
            score += 20
        if score <= 0:
            continue
        candidate = _candidate("status_page_url", normalized, page_url, score, method="anchor")
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def _extract_support_chat(html: str, page_url: str) -> QuickAnswerCandidate | None:
    lowered = html.lower()
    for provider, pattern in _SUPPORT_CHAT_PATTERNS.items():
        if pattern.search(lowered):
            return _candidate("support_chat", provider, page_url, 85, method="script")
    return None


def scan_html_for_quick_answers(
    *,
    html: str,
    page_url: str,
    root_url: str,
) -> dict[str, QuickAnswerCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    picked: dict[str, QuickAnswerCandidate] = {}

    _pick_better(picked, _extract_support_email(soup, text, page_url))
    _pick_better(picked, _extract_documentation_url(soup, page_url, root_url))
    _pick_better(picked, _extract_pricing_url(soup, page_url, root_url))
    _pick_better(picked, _extract_trial_info(text, page_url))
    _pick_better(picked, _extract_status_page_url(soup, page_url))
    _pick_better(picked, _extract_support_chat(html, page_url))

    return picked


def merge_quick_answer_candidates(
    current: dict[str, QuickAnswerCandidate],
    incoming: dict[str, QuickAnswerCandidate],
) -> dict[str, QuickAnswerCandidate]:
    merged = dict(current)
    for candidate in incoming.values():
        _pick_better(merged, candidate)
    return merged
