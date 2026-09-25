"""Extract a short company description from a website URL using OpenAI."""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from openai import OpenAI

from backend.core.config import settings
from backend.documents.http_client import (
    FETCH_TIMEOUT_SECONDS,
    FetchContext,
    _http_client,
    _raise_for_upstream_status,
    _request_with_safe_redirects,
)

logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 4000
_WHITESPACE_RE = re.compile(r"\s{2,}")
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _fetch_page_text(url: str) -> str:
    """Validate URL, fetch through the crawler's SSRF-guarded HTTP client, and return stripped plain text (title + meta + headings)."""
    scheme = urlparse(url).scheme
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Unsupported URL scheme: {scheme!r}")

    context = FetchContext(stage="onboarding:page", url=url)
    with _http_client(FETCH_TIMEOUT_SECONDS) as client:
        response = _request_with_safe_redirects(client, "GET", url, context=context)
        _raise_for_upstream_status(response, context)

    soup = BeautifulSoup(response.text, "html.parser")

    parts: list[str] = []
    if title := soup.find("title"):
        parts.append(title.get_text(strip=True))
    if meta := soup.find("meta", attrs={"name": "description"}):
        if content := meta.get("content", ""):
            parts.append(str(content).strip())
    for tag in ("h1", "h2"):
        for elem in soup.find_all(tag, limit=5):
            text = elem.get_text(strip=True)
            if text:
                parts.append(text)

    raw = " | ".join(dict.fromkeys(p for p in parts if p))
    return _WHITESPACE_RE.sub(" ", raw)[:_MAX_CONTENT_CHARS]


def extract_company_description(url: str, api_key: str) -> str | None:
    """
    Return a 2-sentence company description extracted from the given URL,
    or None on any error (network, validation, OpenAI).
    """
    try:
        page_text = _fetch_page_text(url)
    except Exception:
        logger.warning("extractor: failed to fetch %s", url, exc_info=True)
        return None

    if not page_text.strip():
        return None

    try:
        client = OpenAI(api_key=api_key, timeout=15.0, max_retries=0)
        resp = client.chat.completions.create(
            model=settings.localization_model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Extract a 2-sentence company description from this webpage content. "
                        "Output only the description, no commentary.\n\n"
                        f"Content: {page_text}"
                    ),
                }
            ],
            max_completion_tokens=120,
            temperature=0,
        )
        return resp.choices[0].message.content.strip() or None
    except Exception:
        logger.warning("extractor: OpenAI call failed for %s", url, exc_info=True)
        return None
