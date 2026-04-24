"""Extract a short company description from a website URL using OpenAI."""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from openai import OpenAI

from backend.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_MAX_CONTENT_CHARS = 4000
_WHITESPACE_RE = re.compile(r"\s{2,}")
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _validate_url(url: str) -> None:
    """Raise ValueError if the URL is not a safe public http/https target."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Missing hostname in URL")
    try:
        addrinfos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve hostname {hostname!r}") from exc
    for *_, (ip_str, *_rest) in addrinfos:
        addr = ipaddress.ip_address(ip_str)
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise ValueError(f"URL resolves to non-public address: {ip_str}")


def _fetch_page_text(url: str) -> str:
    """Validate URL, fetch the page, and return stripped plain text (title + meta + headings)."""
    _validate_url(url)
    resp = httpx.get(
        url,
        timeout=_TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

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
