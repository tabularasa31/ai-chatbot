# ruff: noqa: E402

from __future__ import annotations

from typing import Generator, Optional
import json
import os
import sys
from unittest.mock import Mock, patch

# Set test env before any backend imports
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:?check_same_thread=False")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("INJECTION_SEMANTIC_ENABLED", "false")
# Valid Fernet key for tests (generate with Fernet.generate_key())
os.environ.setdefault("ENCRYPTION_KEY", "7b4_zUZivxPZWzIkXbVf3dpQX9Ab22HB51H9Qcrjya8=")

# Prevent Langfuse from initialising and hitting real network during tests.
# Pop any shell-level vars so settings.langfuse_* comes up as None.
for _lf_var in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(_lf_var, None)

# Patch create_engine for SQLite (pool_size/max_overflow not supported)
import sqlalchemy as _sa
_original_create_engine = _sa.create_engine


def _patched_create_engine(url, **kwargs):
    if "sqlite" in str(url):
        kwargs.pop("max_overflow", None)
        kwargs.pop("pool_size", None)
        url_str = str(url)
        if "check_same_thread" not in url_str:
            url = url_str + ("&" if "?" in url_str else "?") + "check_same_thread=False"
    return _original_create_engine(url, **kwargs)


_sa.create_engine = _patched_create_engine

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# добавляем корень проекта в PYTHONPATH
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.models import Base


@pytest.fixture(autouse=True)
def clear_detect_language_cache() -> Generator[None, None, None]:
    from backend.chat.language import _detect_language_cached

    _detect_language_cached.cache_clear()
    yield
    _detect_language_cached.cache_clear()


@pytest.fixture(autouse=True)
def reset_gap_analyzer_job_runner_state() -> Generator[None, None, None]:
    import backend.gap_analyzer.jobs as gap_jobs

    gap_jobs._shutdown_event.clear()
    gap_jobs._clear_active_job()
    gap_jobs._job_runner_state = gap_jobs._GapJobRunnerState()
    try:
        yield
    finally:
        gap_jobs._shutdown_event.set()
        thread = gap_jobs._job_runner_state.current_thread()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        gap_jobs._clear_active_job()
        gap_jobs._job_runner_state = gap_jobs._GapJobRunnerState()
        gap_jobs._shutdown_event.clear()


@pytest.fixture(scope="function")
def engine() -> Generator[Engine, None, None]:
    """Создаёт движок SQLite для тестов."""
    import tempfile
    import os as _os
    fd, path = tempfile.mkstemp(suffix=".db")
    _os.close(fd)
    url = f"sqlite:///{path}?check_same_thread=False"
    os.environ["DATABASE_URL"] = url
    engine_ = create_engine(url, echo=False, future=True)

    @event.listens_for(engine_, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[override]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine_)
    try:
        yield engine_
    finally:
        engine_.dispose()
        os.environ["DATABASE_URL"] = "sqlite:///:memory:?check_same_thread=False"
        try:
            _os.unlink(path)
        except OSError:
            pass


@pytest.fixture(scope="function")
def db_session(engine: Engine) -> Generator[Session, None, None]:
    """Предоставляет сессию БД для каждого теста."""
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=Session,
        future=True,
    )
    session = TestingSessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


@pytest.fixture(scope="function")
def tenant(engine: Engine, db_session: Session) -> Generator[TestClient, None, None]:
    """Test tenant with auth routes, using test database."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
    from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker
    from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

    from backend.core import db as core_db
    from backend.core.db import get_async_db, get_db
    from backend.main import app

    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=Session,
        future=True,
    )

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    # Build an async engine pointing to the same SQLite file so async routes
    # (e.g. the search route) see the same test data as sync fixtures.
    sync_url = str(engine.url)
    async_url = sync_url.replace("sqlite:///", "sqlite+aiosqlite:///").split("?")[0]
    _async_test_engine = _create_async_engine(async_url, future=True)
    _async_testing_session_factory = _async_sessionmaker(
        bind=_async_test_engine,
        class_=_AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    async def override_get_async_db():
        async with _async_testing_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_async_db] = override_get_async_db
    original_engine = core_db.engine
    original_session_local = core_db.SessionLocal
    core_db.engine = engine
    core_db.SessionLocal = TestingSessionLocal

    try:
        with TestClient(app) as c:
            yield c
    finally:
        core_db.engine = original_engine
        core_db.SessionLocal = original_session_local
        app.dependency_overrides.clear()
        try:
            asyncio.get_event_loop().run_until_complete(_async_test_engine.dispose())
        except Exception:
            pass


@pytest.fixture(autouse=True)
def mock_openai_client():
    """Mock get_openai_client for all tests — no real API calls."""
    from unittest.mock import AsyncMock

    mock_client = Mock()

    def _embeddings_create(*args: object, **kwargs: object) -> Mock:
        # OpenAI embeddings returns one embedding per input element.
        inp = kwargs.get("input")
        if isinstance(inp, str):
            count = 1
        elif isinstance(inp, list):
            count = len(inp)
        else:
            count = 1
        configured_data = getattr(
            mock_client.embeddings.create.return_value,
            "data",
            None,
        )

        # If a test configured explicit return_value.data, respect it.
        if isinstance(configured_data, list) and configured_data:
            if len(configured_data) == count:
                data_out = configured_data
            elif len(configured_data) == 1:
                data_out = [configured_data[0] for _ in range(count)]
            else:
                # Best-effort: cycle/trim to match the expected count.
                data_out = (configured_data * (count // len(configured_data) + 1))[:count]
            return Mock(data=data_out)

        return Mock(data=[Mock(embedding=[0.1] * 1536) for _ in range(count)])

    mock_client.embeddings.create.side_effect = _embeddings_create
    mock_client.embeddings.create.return_value = Mock(
        data=[Mock(embedding=[0.1] * 1536)]
    )
    def _as_stream(response: Mock) -> list[Mock]:
        """Convert a non-stream mock response into an iterable of stream chunks."""
        try:
            content = response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError):
            content = ""
        try:
            total_tokens = response.usage.total_tokens
        except AttributeError:
            total_tokens = 0
        chunks: list[Mock] = []
        if content:
            chunks.append(
                Mock(
                    choices=[Mock(delta=Mock(content=content), finish_reason=None)],
                    usage=None,
                )
            )
        chunks.append(
            Mock(
                choices=[Mock(delta=Mock(content=None), finish_reason="stop")],
                usage=Mock(total_tokens=total_tokens, prompt_tokens=0, completion_tokens=total_tokens),
            )
        )
        return chunks

    def _chat_completions_create(*args: object, **kwargs: object) -> Mock | list[Mock]:
        messages = kwargs.get("messages") or []
        stream = bool(kwargs.get("stream"))
        response: Mock
        if isinstance(messages, list) and messages:
            system_content = ""
            user_content = ""
            if len(messages) >= 1 and isinstance(messages[0], dict):
                system_content = str(messages[0].get("content", ""))
            if len(messages) >= 2 and isinstance(messages[1], dict):
                user_content = str(messages[1].get("content", ""))
            if "You localize assistant messages." in system_content:
                marker = "Assistant message to localize:\n"
                canonical = user_content.split(marker, 1)[1] if marker in user_content else "AI response"
                response = Mock(
                    choices=[Mock(message=Mock(content=canonical.strip()))],
                    usage=Mock(total_tokens=20),
                )
                return _as_stream(response) if stream else response
        response = mock_client.chat.completions.create.return_value
        return _as_stream(response) if stream else response

    mock_client.chat.completions.create.side_effect = _chat_completions_create
    mock_client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content="AI response"))],
        usage=Mock(total_tokens=100),
    )
    mock_esc_client = Mock()
    mock_esc_client.chat.completions.create.return_value = Mock(
        choices=[
            Mock(
                message=Mock(
                    content=json.dumps(
                        {
                            "message_to_user": "A support ticket was created for you.",
                            "followup_decision": None,
                        }
                    )
                )
            )
        ],
        usage=Mock(total_tokens=15),
    )

    # Async-capable mock that delegates to the same sync side_effects so tests
    # that configure mock_openai_client.embeddings.create.return_value.data work
    # transparently for both sync and async code paths.
    async_mock_client = AsyncMock()

    async def _async_embeddings_create(*args: object, **kwargs: object) -> Mock:
        # Call through the mock so tests can override side_effect / return_value.
        return mock_client.embeddings.create(*args, **kwargs)

    async def _async_chat_completions_create(*args: object, **kwargs: object) -> Mock:
        # Call through the mock so tests can override side_effect / return_value.
        return mock_client.chat.completions.create(*args, **kwargs)

    async_mock_client.embeddings.create.side_effect = _async_embeddings_create
    async_mock_client.chat.completions.create.side_effect = _async_chat_completions_create

    # Patch where get_openai_client is used (not where defined) so imports see the mock
    with patch("backend.embeddings.service.get_openai_client", return_value=mock_client, create=True), \
         patch("backend.search.service.get_openai_client", return_value=mock_client), \
         patch("backend.search.service.get_async_openai_client", return_value=async_mock_client), \
         patch("backend.search.contradiction_adjudication.get_openai_client", return_value=mock_client), \
         patch("backend.chat.language.get_openai_client", return_value=mock_client), \
         patch("backend.chat.service.get_openai_client", return_value=mock_client), \
         patch("backend.documents.service.get_openai_client", return_value=mock_client, create=True), \
         patch("backend.gap_analyzer.prompts.get_openai_client", return_value=mock_client), \
         patch("backend.knowledge.routes.get_openai_client", return_value=mock_client), \
         patch("backend.tenant_knowledge.extract_tenant_knowledge.get_openai_client", return_value=mock_client), \
         patch("backend.tenant_knowledge.faq_service.get_openai_client", return_value=mock_client), \
         patch("backend.guards.relevance_checker.get_openai_client", return_value=mock_client), \
         patch(
             "backend.escalation.openai_escalation.get_openai_client",
             return_value=mock_esc_client,
         ), \
         patch(
             "backend.search.service._rewrite_query_for_retrieval",
             return_value=None,
         ), \
         patch(
             "backend.search.service._async_rewrite_query_for_retrieval",
             new=AsyncMock(return_value=None),
         ):
        yield mock_client


@pytest.fixture
def escalation_openai_override(monkeypatch: pytest.MonkeyPatch):
    """Override escalation LLM response for tests that need specific decisions."""

    def _apply(
        *,
        message_to_user: str = "A support ticket was created for you.",
        followup_decision: Optional[str] = None,
        tokens_used: int = 15,
    ) -> Mock:
        esc_client = Mock()
        esc_client.chat.completions.create.return_value = Mock(
            choices=[
                Mock(
                    message=Mock(
                        content=json.dumps(
                            {
                                "message_to_user": message_to_user,
                                "followup_decision": followup_decision,
                            }
                        )
                    )
                )
            ],
            usage=Mock(total_tokens=tokens_used),
        )
        monkeypatch.setattr(
            "backend.escalation.openai_escalation.get_openai_client",
            lambda _api_key: esc_client,
        )
        return esc_client

    return _apply


@pytest.fixture(autouse=True)
def _reset_widget_rate_limit_key_override():
    """Clear widget rate-limit test hook so a failed test cannot leak state."""
    yield
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    set_widget_public_rate_limit_key_override(None)


@pytest.fixture(autouse=True)
def _reset_rate_limiter_state():
    """Reset shared slowapi in-memory counters so tests do not leak 429 state."""
    from backend.core.limiter import limiter

    limiter.reset()
    if hasattr(limiter, "_storage") and hasattr(limiter._storage, "reset"):
        limiter._storage.reset()
    yield
    limiter.reset()
    if hasattr(limiter, "_storage") and hasattr(limiter._storage, "reset"):
        limiter._storage.reset()


@pytest.fixture(autouse=True)
def mock_langfuse():
    """Block all Langfuse network calls for every test.

    Belt-and-suspenders on top of the env-var clearing above: patches the
    Langfuse constructor so tests stay offline even if creds leak in from the
    shell.  Also resets the module-level _service singleton so each test starts
    from a clean disabled state.
    """
    from unittest.mock import MagicMock

    import backend.observability.service as _obs

    _obs._service.reset()
    mock_lf_cls = MagicMock(name="Langfuse")
    mock_lf_cls.return_value = MagicMock(name="LangfuseInstance")
    with patch("langfuse.Langfuse", mock_lf_cls, create=True):
        yield mock_lf_cls
    _obs._service.reset()


def set_client_openai_key(test_client: TestClient, token: str, key: str = "sk-test") -> None:
    """Set OpenAI API key for current user's tenant. Call after creating tenant."""
    r = test_client.patch(
        "/tenants/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"openai_api_key": key},
    )
    assert r.status_code == 200, f"Failed to set OpenAI key: {r.json()}"


def register_and_verify_user(
    test_client: TestClient,
    db_session: Session,
    email: str = "verified@example.com",
    password: str = "SecurePass1!",
) -> str:
    """Register user, mark them verified directly, and return JWT for generic test setup."""
    with patch("backend.auth.routes.send_email"):
        resp = test_client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.json()
    from backend.auth.service import create_token_for_user
    from backend.models import User

    user = db_session.query(User).filter(User.email == email).first()
    assert user is not None
    user.is_verified = True
    user.verification_token = None
    user.verification_expires_at = None
    db_session.commit()
    db_session.refresh(user)
    token, _ = create_token_for_user(user)
    return token


# ---------------------------------------------------------------------------
# Async fixtures (parallel to the sync contour above).
#
# New async services use ``get_async_db`` from backend.core.db; tests for them
# get an ``AsyncSession`` here. Sync tests are unaffected.
# ---------------------------------------------------------------------------

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest_asyncio.fixture(scope="function")
async def async_engine_fx():
    """Per-test async SQLite engine with all tables created."""
    engine_ = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )
    async with engine_.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine_
    finally:
        await engine_.dispose()


@pytest.fixture(autouse=True)
def _reset_escalation_rate_window() -> Generator[None, None, None]:
    """Reset the module-level escalation sliding-window deque before every test.

    The deque accumulates timestamps across the entire process lifetime.  Without
    this reset, tests that run after many escalation-triggering tests see the counter
    already at/above the threshold, causing _check_escalation_rate to fire an extra
    event and break assertions that expect exactly one captured event.
    """
    from backend.chat.events import _reset_escalation_rate_for_tests
    _reset_escalation_rate_for_tests()
    yield


@pytest_asyncio.fixture(scope="function")
async def async_db_session(async_engine_fx) -> "AsyncSession":  # type: ignore[name-defined]
    """AsyncSession bound to the per-test async engine."""
    SessionFactory = async_sessionmaker(
        bind=async_engine_fx,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    async with SessionFactory() as session:
        try:
            yield session
        finally:
            await session.rollback()
