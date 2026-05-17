"""Adapter that sends an eval case to the chat backend.

Hits ``POST /widget/chat?bot_id=...&session_id=...`` because that's the
public, no-auth path real users go through. The endpoint streams SSE
events (``chunk`` deltas and a final ``done`` event); the client
buffers them and surfaces the final text + sources to the runner.

The runner can target either an in-process ``TestClient`` or a remote
uvicorn / Railway deploy via ``httpx.Client``: both expose ``.stream``
with the same shape.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ChatResponse:
    text: str
    sources: list[dict[str, str]]
    chat_ended: bool
    latency_ms: int
    error: dict | None = None
    raw_events: list[dict] = field(default_factory=list)


class HttpStreamLike(Protocol):
    """Subset of httpx.Client / TestClient shared by both backends."""

    def stream(self, method: str, url: str, *, json: dict | None = ..., params: dict | None = ...) -> Any: ...

    def post(self, url: str, *, params: dict | None = ...) -> Any: ...


class ChatClient:
    """Send eval queries to the widget chat SSE endpoint."""

    def __init__(
        self,
        bot_public_id: str,
        http: HttpStreamLike,
        *,
        path: str = "/widget/chat",
        session_init_path: str = "/widget/session/init",
    ) -> None:
        self.bot_public_id = bot_public_id
        self.http = http
        self.path = path
        self.session_init_path = session_init_path

    def start_session(self) -> str:
        """POST /widget/session/init and return the new session_id. Required
        before chain (multi-turn) calls — the widget chat endpoint rejects
        an unknown session_id with 409 ``session_not_found``."""
        resp = self.http.post(self.session_init_path, params={"bot_id": self.bot_public_id})
        status = getattr(resp, "status_code", None)
        if status != 200:
            detail = getattr(resp, "text", "")
            try:
                detail = resp.json()
            except Exception:
                pass
            raise RuntimeError(f"session init HTTP {status}: {detail}")
        body = resp.json()
        return str(body["session_id"])

    def ask(self, question: str, *, session_id: str | None = None) -> ChatResponse:
        # Only forward session_id when the caller explicitly passed one
        # (to continue a multi-turn conversation). Otherwise omit it so the
        # backend creates a fresh session — passing a random UUID is a
        # 409 `session_not_found` against /widget/chat because the server
        # treats it as "resume this existing chat".
        params: dict[str, str] = {"bot_id": self.bot_public_id}
        if session_id is not None:
            params["session_id"] = session_id
        body = {"message": question}
        started = time.perf_counter()

        events: list[dict] = []
        with self.http.stream("POST", self.path, json=body, params=params) as resp:
            status = getattr(resp, "status_code", None)
            if status != 200:
                # Drain body for diagnostics; httpx requires .read() before .text.
                try:
                    if hasattr(resp, "read"):
                        resp.read()
                    detail = resp.json()
                except Exception:
                    detail = {"raw": getattr(resp, "text", "")}
                raise RuntimeError(f"chat HTTP {status}: {detail}")
            for line in _iter_sse_lines(resp):
                payload = _parse_sse_data(line)
                if payload is not None:
                    events.append(payload)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _aggregate_events(events, elapsed_ms)


def _iter_sse_lines(resp: Any) -> Iterator[str]:
    """Yield raw SSE lines from any httpx-compatible streaming response."""

    if hasattr(resp, "iter_lines"):
        for line in resp.iter_lines():
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            yield line
        return
    # Fallback: try .text on a non-streaming response.
    text = getattr(resp, "text", "") or ""
    for line in text.splitlines():
        yield line


def _parse_sse_data(line: str) -> dict | None:
    if not line or not line.startswith("data:"):
        return None
    raw = line[5:].strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _aggregate_events(events: list[dict], latency_ms: int) -> ChatResponse:
    chunks: list[str] = []
    final_text: str | None = None
    sources: list[dict[str, str]] = []
    chat_ended = False
    error: dict | None = None

    for ev in events:
        kind = ev.get("type")
        if kind == "chunk":
            text = ev.get("text") or ""
            if text:
                chunks.append(text)
        elif kind == "done":
            final_text = ev.get("text") or "".join(chunks)
            chat_ended = bool(ev.get("chat_ended"))
            sources = ev.get("sources") or []
        elif kind == "error":
            error = ev

    text = final_text if final_text is not None else "".join(chunks)
    return ChatResponse(
        text=text,
        sources=sources,
        chat_ended=chat_ended,
        latency_ms=latency_ms,
        error=error,
        raw_events=events,
    )


@contextmanager
def _maybe_open(resource: Any) -> Iterator[Any]:
    """Helper for httpx-style context managers (currently unused; kept
    as a placeholder if the runner moves to async.)"""
    yield resource
