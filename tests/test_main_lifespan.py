from __future__ import annotations

from fastapi.testclient import TestClient


def test_fastapi_lifespan_triggers_graceful_shutdown(monkeypatch) -> None:
    import backend.main as backend_main

    calls: list[object] = []
    monkeypatch.setattr(
        backend_main,
        "gap_graceful_shutdown",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with TestClient(backend_main.app):
        pass

    assert calls == [((), {})]
