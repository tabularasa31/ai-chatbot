"""HTTP client with SSRF protection: fetch helpers, redirect follower, response guards."""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

USER_AGENT = "Chat9Bot/1.0 (+https://getchat9.live)"
FETCH_TIMEOUT_SECONDS = 10.0
PREFLIGHT_TIMEOUT_SECONDS = 5.0
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
DISCOVERY_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
SUPPORTED_PAGE_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/markdown",
    "text/plain",
)


@dataclass
class FetchContext:
    stage: str
    url: str


def _log_fetch(level: int, message: str, context: FetchContext, **extra: Any) -> None:
    logger.log(level, "%s [%s] %s", message, context.stage, context.url, extra=extra)


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _resolve_hostname(hostname: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=400,
            detail="Couldn't resolve this URL. Check the address and try again.",
        ) from exc

    resolved: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for family, _, _, _, sockaddr in infos:
        if family in (socket.AF_INET, socket.AF_INET6):
            resolved.add(ipaddress.ip_address(sockaddr[0]))
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail="Couldn't resolve this URL. Check the address and try again.",
        )
    return resolved


def _validate_public_hostname(hostname: str) -> None:
    if not hostname:
        raise HTTPException(status_code=400, detail="Please enter a valid public URL.")

    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        candidates = _resolve_hostname(hostname)
    else:
        candidates = {parsed_ip}

    if any(_is_forbidden_ip(candidate) for candidate in candidates):
        raise HTTPException(
            status_code=400,
            detail="Private, local, and reserved network addresses are not allowed.",
        )


def _http_client(timeout_seconds: float) -> httpx.Client:
    return httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT},
    )


def _enforce_response_size_limit(response: httpx.Response, context: FetchContext) -> None:
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_HTML_BYTES:
                _log_fetch(
                    logging.WARNING,
                    "Response rejected by content-length",
                    context,
                    status_code=response.status_code,
                    content_length=content_length,
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Response is too large. Maximum size is {MAX_HTML_BYTES // (1024 * 1024)}MB.",
                )
        except ValueError:
            pass

    if len(response.content) > MAX_HTML_BYTES:
        _log_fetch(
            logging.WARNING,
            "Response rejected by downloaded size",
            context,
            status_code=response.status_code,
            size=len(response.content),
        )
        raise HTTPException(
            status_code=400,
            detail=f"Response is too large. Maximum size is {MAX_HTML_BYTES // (1024 * 1024)}MB.",
        )


def _raise_for_upstream_status(response: httpx.Response, context: FetchContext) -> None:
    status_code = response.status_code
    if status_code < 400:
        return

    _log_fetch(logging.WARNING, "Upstream returned error status", context, status_code=status_code)
    if status_code == 404:
        raise HTTPException(status_code=404, detail="The URL returned 404 Not Found.")
    if status_code in {401, 403}:
        raise HTTPException(status_code=400, detail="The URL requires access and can't be indexed.")
    if 400 <= status_code < 500:
        raise HTTPException(
            status_code=400,
            detail=f"The URL returned HTTP {status_code}. Check the address and try again.",
        )
    raise HTTPException(
        status_code=502,
        detail=f"The website is temporarily unavailable (HTTP {status_code}). Try again later.",
    )


def _request_with_safe_redirects(
    http_client: httpx.Client,
    method: str,
    url: str,
    *,
    context: FetchContext,
    max_redirects: int = MAX_REDIRECTS,
) -> httpx.Response:
    current_url = url
    for _ in range(max_redirects + 1):
        parsed = urlparse(current_url)
        hostname = parsed.hostname.lower() if parsed.hostname else ""
        _validate_public_hostname(hostname)
        try:
            response = http_client.request(method, current_url)
        except httpx.TimeoutException as exc:
            _log_fetch(logging.WARNING, "Request timed out", context, method=method)
            raise HTTPException(
                status_code=400,
                detail="Couldn't reach this URL. Check the address and try again.",
            ) from exc
        except httpx.HTTPError as exc:
            _log_fetch(logging.WARNING, "Request failed", context, method=method, error=str(exc))
            raise HTTPException(
                status_code=400,
                detail="Couldn't reach this URL. Check the address and try again.",
            ) from exc

        if response.status_code not in {301, 302, 303, 307, 308}:
            _enforce_response_size_limit(response, context)
            return response

        location = response.headers.get("location")
        if not location:
            _log_fetch(logging.WARNING, "Redirect missing location header", context, method=method)
            raise HTTPException(status_code=400, detail="URL redirect is missing a target.")
        current_url = urljoin(current_url, location)
        _log_fetch(logging.INFO, "Following validated redirect", context, method=method, location=current_url)

    raise HTTPException(status_code=400, detail="Too many redirects while fetching this URL.")


def _is_html_like(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    return any(value in content_type for value in DISCOVERY_CONTENT_TYPES)


def _is_supported_page_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    return any(value in content_type for value in SUPPORTED_PAGE_CONTENT_TYPES)


def _fetch_reachable_page(url: str, timeout_seconds: float) -> tuple[str, str | None]:
    from bs4 import BeautifulSoup

    context = FetchContext(stage="preflight:page", url=url)
    with _http_client(timeout_seconds) as client:
        response = _request_with_safe_redirects(client, "HEAD", url, context=context)
        if response.status_code >= 400 or "text/html" not in response.headers.get("content-type", ""):
            response = _request_with_safe_redirects(client, "GET", url, context=context)

        _raise_for_upstream_status(response, context)

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and response.request.method != "GET":
            response = _request_with_safe_redirects(client, "GET", url, context=context)
            _raise_for_upstream_status(response, context)
            content_type = response.headers.get("content-type", "")

        text = response.text
        title = None
        if "text/html" in content_type:
            soup = BeautifulSoup(text, "html.parser")
            title_tag = soup.find("title")
            title = title_tag.get_text(" ", strip=True) if title_tag else None

        return text, title


def _fetch_page_html(url: str) -> str | None:
    context = FetchContext(stage="crawl:page", url=url)
    with _http_client(FETCH_TIMEOUT_SECONDS) as client:
        try:
            response = _request_with_safe_redirects(client, "GET", url, context=context)
            _raise_for_upstream_status(response, context)
        except HTTPException as exc:
            _log_fetch(logging.INFO, "Skipping page after fetch failure", context, detail=exc.detail)
            return None
    if response.status_code >= 400 or not _is_supported_page_response(response):
        _log_fetch(
            logging.INFO,
            "Skipping page with unsupported response",
            context,
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
        )
        return None
    return response.text
