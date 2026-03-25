from __future__ import annotations

import socket

import httpx
import pytest
from fastapi import HTTPException

from backend.documents import url_service
from backend.documents.schemas import SOURCE_TYPE_URL, UrlSourceCreateRequest, UrlSourceUpdateRequest


def test_validate_public_hostname_rejects_private_ip() -> None:
    with pytest.raises(HTTPException) as exc_info:
        url_service._validate_public_hostname("127.0.0.1")

    assert exc_info.value.status_code == 400
    assert "not allowed" in str(exc_info.value.detail).lower()


def test_validate_public_hostname_rejects_private_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, port: int | None, type: int = 0):  # type: ignore[override]
        assert host == "internal.example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(url_service.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(HTTPException) as exc_info:
        url_service._validate_public_hostname("internal.example.com")

    assert exc_info.value.status_code == 400
    assert "not allowed" in str(exc_info.value.detail).lower()


def test_request_with_safe_redirects_blocks_local_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(host: str, port: int | None, type: int = 0):  # type: ignore[override]
        if host == "docs.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        if host == "localhost":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        raise socket.gaierror(host)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://docs.example.com/start"
        return httpx.Response(
            302,
            headers={"location": "http://localhost:8080/admin"},
            request=request,
        )

    monkeypatch.setattr(url_service.socket, "getaddrinfo", fake_getaddrinfo)
    transport = httpx.MockTransport(handler)

    with httpx.Client(transport=transport, follow_redirects=False, trust_env=False) as client:
        with pytest.raises(HTTPException) as exc_info:
            url_service._request_with_safe_redirects(
                client,
                "GET",
                "https://docs.example.com/start",
                context=url_service.FetchContext(stage="test", url="https://docs.example.com/start"),
            )

    assert exc_info.value.status_code == 400
    assert "not allowed" in str(exc_info.value.detail).lower()


def test_fetch_reachable_page_returns_404_for_missing_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(host: str, port: int | None, type: int = 0):  # type: ignore[override]
        assert host == "docs.example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    monkeypatch.setattr(url_service.socket, "getaddrinfo", fake_getaddrinfo)
    transport = httpx.MockTransport(handler)

    original_client_factory = url_service._http_client
    monkeypatch.setattr(
        url_service,
        "_http_client",
        lambda timeout_seconds: httpx.Client(
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    try:
        with pytest.raises(HTTPException) as exc_info:
            url_service._fetch_reachable_page("https://docs.example.com/missing", 5.0)
    finally:
        monkeypatch.setattr(url_service, "_http_client", original_client_factory)

    assert exc_info.value.status_code == 404
    assert "404" in str(exc_info.value.detail)


def test_url_source_create_request_validates_http_url_and_schedule() -> None:
    payload = UrlSourceCreateRequest(url="https://docs.example.com", schedule="manual")

    assert str(payload.url) == "https://docs.example.com/"
    assert payload.schedule == "manual"

    with pytest.raises(Exception):
        UrlSourceCreateRequest(url="ftp://docs.example.com", schedule="weekly")

    with pytest.raises(Exception):
        UrlSourceCreateRequest(url="https://docs.example.com", schedule="monthly")


def test_url_source_update_request_accepts_only_supported_schedules() -> None:
    payload = UrlSourceUpdateRequest(schedule="daily")
    assert payload.schedule == "daily"

    with pytest.raises(Exception):
        UrlSourceUpdateRequest(schedule="monthly")


def test_url_source_response_constant_exposed() -> None:
    assert SOURCE_TYPE_URL == "url"
