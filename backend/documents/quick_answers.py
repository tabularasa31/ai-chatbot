from __future__ import annotations

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
_TRIAL_RE = re.compile(
    r"[^.?!\n]*(?:free trial|trial period|[0-9]{1,3}[- ]day free|[0-9]{1,3}[- ]day trial)[^.?!\n]*[.?!]?",
    re.IGNORECASE,
)
_PRICING_TEXT_RE = re.compile(r"\b(pricing|plans?|tariff|tarif)\b", re.IGNORECASE)
_DOCS_TEXT_RE = re.compile(
    r"\b(docs|documentation|developer docs|help center|knowledge base)\b",
    re.IGNORECASE,
)
_STATUS_TEXT_RE = re.compile(r"\b(status|status page|system status)\b", re.IGNORECASE)
_STATUS_URL_RE = re.compile(r"(?:^|\.)status\.|statuspage\.io|instatus\.com|/status(?:/|$)", re.IGNORECASE)
_SUPPORT_CHAT_PATTERNS: dict[str, re.Pattern[str]] = {
    "Intercom": re.compile(r"intercom", re.IGNORECASE),
    "Crisp": re.compile(r"crisp", re.IGNORECASE),
    "Drift": re.compile(r"drift", re.IGNORECASE),
}


@dataclass
class QuickAnswerCandidate:
    key: str
    value: str
    source_url: str
    score: int
    metadata: dict[str, str]


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


def _extract_support_email(soup: BeautifulSoup, text: str, page_url: str) -> QuickAnswerCandidate | None:
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        if href.lower().startswith("mailto:"):
            value = href.split(":", 1)[1].strip()
            if value:
                return _candidate("support_email", value, page_url, 100, method="mailto")
    match = _EMAIL_RE.search(text)
    if match:
        return _candidate("support_email", match.group(0), page_url, 70, method="regex")
    return None


def _extract_documentation_url(soup: BeautifulSoup, page_url: str, root_url: str) -> QuickAnswerCandidate | None:
    best: QuickAnswerCandidate | None = _candidate(
        "documentation_url",
        _normalize_url(root_url),
        root_url,
        10,
        method="root_fallback",
    )
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
    match = _TRIAL_RE.search(text)
    if not match:
        return None
    sentence = " ".join(match.group(0).split())
    if not sentence:
        return None
    return _candidate("trial_info", sentence[:400], page_url, 80, method="regex")


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
