"""Shared URL canonicalization for the crawler, sitemap discovery, and quick answers.

Callers needing scheme/credential/public-host validation layer it on top
(see ``documents.url_service._normalize_source_url``).
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def canonical_url(url: str, *, strip_trailing_slash: bool = True) -> str:
    """Lower-case scheme/host, drop query/fragment, and normalize the trailing slash.

    Root path (``/``) is always kept as-is. A non-root trailing slash is
    stripped by default so the same page is never keyed under two strings
    (e.g. ``/docs`` and ``/docs/``) depending on which link discovered it.
    """
    parsed = urlparse(url.strip())
    path = parsed.path or "/"
    if strip_trailing_slash and path.endswith("/") and path != "/":
        path = path[:-1]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))
