"""Sitemap parsing, robots.txt loading, link extraction, and URL discovery."""

from __future__ import annotations

import fnmatch
import logging
from collections import deque
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse

import defusedxml.ElementTree as ElementTree
from fastapi import HTTPException

from backend.documents.http_client import (
    PREFLIGHT_TIMEOUT_SECONDS,
    USER_AGENT,
    FetchContext,
    _http_client,
    _log_fetch,
    _request_with_safe_redirects,
    _is_html_like,
)

logger = logging.getLogger(__name__)

MAX_DISCOVERY_DEPTH = 3
DISCOVERY_ESTIMATE_CAP = 200
MAX_SITEMAPS_PER_SOURCE = 20


def _normalize_page_url(url: str, base_domain: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.netloc.lower() != base_domain.lower():
        return None
    path = parsed.path or "/"
    if path.endswith("/") and path != "/":
        path = path[:-1]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def _extract_links(html: str, current_url: str, domain: str) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        joined = urljoin(current_url, href)
        normalized = _normalize_page_url(joined, domain)
        if normalized:
            out.append(normalized)
    return out


def _fetch_sitemap_urls(root_url: str, domain: str) -> list[str]:
    candidates = [urljoin(root_url, "/sitemap.xml"), urljoin(root_url, "/sitemap_index.xml")]
    urls: list[str] = []
    queued = deque(candidates)
    seen_sitemaps: set[str] = set()
    seen_urls: set[str] = set()

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    with _http_client(PREFLIGHT_TIMEOUT_SECONDS) as client:
        sitemaps_fetched = 0
        while queued and sitemaps_fetched < MAX_SITEMAPS_PER_SOURCE:
            candidate = queued.popleft()
            if candidate in seen_sitemaps:
                continue
            seen_sitemaps.add(candidate)
            sitemaps_fetched += 1
            context = FetchContext(stage="preflight:sitemap", url=candidate)
            try:
                response = _request_with_safe_redirects(client, "GET", candidate, context=context)
            except HTTPException:
                continue
            if response.status_code >= 400 or not response.text.strip():
                _log_fetch(
                    logging.INFO,
                    "Skipping sitemap candidate",
                    context,
                    status_code=response.status_code,
                )
                continue
            try:
                root = ElementTree.fromstring(response.text)
            except ElementTree.ParseError:
                _log_fetch(logging.INFO, "Skipping invalid sitemap XML", context)
                continue

            root_name = local_name(root.tag)
            if root_name == "sitemapindex":
                for sitemap_loc in root.findall("{*}sitemap/{*}loc"):
                    if not sitemap_loc.text:
                        continue
                    normalized_sitemap = _normalize_page_url(sitemap_loc.text.strip(), domain)
                    if normalized_sitemap and normalized_sitemap not in seen_sitemaps:
                        queued.append(normalized_sitemap)
                continue

            if root_name != "urlset":
                _log_fetch(logging.INFO, "Skipping unsupported sitemap root", context, root_tag=root.tag)
                continue

            for loc in root.findall("{*}url/{*}loc"):
                if not loc.text:
                    continue
                normalized = _normalize_page_url(loc.text.strip(), domain)
                if normalized and normalized not in seen_urls:
                    seen_urls.add(normalized)
                    urls.append(normalized)
    return urls


def _apply_exclusions(urls: list[str], root_url: str, exclusions: list[str]) -> list[str]:
    if not exclusions:
        return urls
    filtered: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        path = parsed.path or "/"
        if any(fnmatch.fnmatch(path, pattern.strip()) for pattern in exclusions if pattern.strip()):
            continue
        filtered.append(url)
    return filtered


def _load_robots_warning(root_url: str) -> str | None:
    parsed = urlparse(root_url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    rp = robotparser.RobotFileParser()
    context = FetchContext(stage="preflight:robots", url=robots_url)
    with _http_client(PREFLIGHT_TIMEOUT_SECONDS) as client:
        try:
            response = _request_with_safe_redirects(client, "GET", robots_url, context=context)
        except HTTPException:
            return None
    if response.status_code >= 400 or not response.text.strip():
        return None
    try:
        rp.parse(response.text.splitlines())
    except Exception:
        return None
    if not rp.can_fetch(USER_AGENT, root_url):
        return "robots.txt restricts crawling. We'll index only what is allowed."
    return None
