"""Shared SSRF guard: reject hostnames that resolve to private/local/reserved addresses."""

from __future__ import annotations

import ipaddress
import socket


class SSRFValidationError(ValueError):
    """Base error for a hostname that failed public-address validation."""


class MissingHostnameError(SSRFValidationError):
    pass


class HostnameUnresolvedError(SSRFValidationError):
    pass


class ForbiddenAddressError(SSRFValidationError):
    pass


def is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
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


def resolve_hostname(hostname: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HostnameUnresolvedError(f"Couldn't resolve hostname {hostname!r}") from exc

    resolved: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for family, _, _, _, sockaddr in infos:
        if family in (socket.AF_INET, socket.AF_INET6):
            resolved.add(ipaddress.ip_address(sockaddr[0]))
    if not resolved:
        raise HostnameUnresolvedError(f"Couldn't resolve hostname {hostname!r}")
    return resolved


def validate_public_hostname(hostname: str) -> None:
    """Raise an ``SSRFValidationError`` subclass if ``hostname`` isn't a safe public target."""
    if not hostname:
        raise MissingHostnameError("Missing or empty hostname")

    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        candidates = resolve_hostname(hostname)
    else:
        candidates = {parsed_ip}

    if any(is_forbidden_ip(candidate) for candidate in candidates):
        raise ForbiddenAddressError(
            f"Hostname {hostname!r} resolves to a private, local, or reserved address"
        )
