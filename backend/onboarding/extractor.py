"""Extract a short company description from a website URL using OpenAI."""
from __future__ import annotations

import logging
import re

import httpx
from openai import OpenAI

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_MAX_CONTENT_CHARS = 4000
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s{2,}")


def _fetch_page_text(url: str) -> str:
    """Fetch a webpage and return stripped plain text (title + meta + headings)."""
    resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    html = resp.text

    parts: list[str] = []

    # <title>
    if m := re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL):
        parts.append(_TAG_RE.sub("", m.group(1)).strip())

    # meta description
    if m := re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', html, re.IGNORECASE):
        parts.append(m.group(1).strip())

    # h1 / h2
    for tag in ("h1", "h2"):
        for m in re.finditer(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.IGNORECASE | re.DOTALL):
            text = _TAG_RE.sub("", m.group(1)).strip()
            if text:
                parts.append(text)

    raw = " | ".join(dict.fromkeys(p for p in parts if p))
    return _WHITESPACE_RE.sub(" ", raw)[:_MAX_CONTENT_CHARS]


def extract_company_description(url: str, api_key: str) -> str | None:
    """
    Return a 2-sentence company description extracted from the given URL,
    or None on any error (network, parse, OpenAI).
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
            model="gpt-4o-mini",
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
            max_tokens=120,
            temperature=0,
        )
        return resp.choices[0].message.content.strip() or None
    except Exception:
        logger.warning("extractor: OpenAI call failed for %s", url, exc_info=True)
        return None
