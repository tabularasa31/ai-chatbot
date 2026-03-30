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
os.environ.setdefault("EVAL_JWT_SECRET", "test-eval-jwt-secret-min-32-chars!!")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
# Valid Fernet key for tests (generate with Fernet.generate_key())
os.environ.setdefault("ENCRYPTION_KEY", "7b4_zUZivxPZWzIkXbVf3dpQX9Ab22HB51H9Qcrjya8=")

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
def client(engine: Engine, db_session: Session) -> Generator[TestClient, None, None]:
    """Test client with auth routes, using test database."""
    from backend.core import db as core_db
    from backend.core.db import get_db
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

    app.dependency_overrides[get_db] = override_get_db
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


@pytest.fixture(autouse=True)
def mock_openai_client():
    """Mock get_openai_client for all tests — no real API calls."""
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
    # Patch where get_openai_client is used (not where defined) so imports see the mock
    with patch("backend.embeddings.service.get_openai_client", return_value=mock_client), \
         patch("backend.search.service.get_openai_client", return_value=mock_client), \
         patch("backend.search.contradiction_adjudication.get_openai_client", return_value=mock_client), \
         patch("backend.chat.service.get_openai_client", return_value=mock_client), \
         patch("backend.documents.service.get_openai_client", return_value=mock_client), \
         patch("backend.knowledge.routes.get_openai_client", return_value=mock_client), \
         patch("backend.tenant_knowledge.extract_tenant_knowledge.get_openai_client", return_value=mock_client), \
         patch("backend.tenant_knowledge.faq_service.get_openai_client", return_value=mock_client), \
         patch("backend.guards.relevance_checker.get_openai_client", return_value=mock_client), \
         patch(
             "backend.escalation.openai_escalation.get_openai_client",
             return_value=mock_esc_client,
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


def set_client_openai_key(test_client: TestClient, token: str, key: str = "sk-test") -> None:
    """Set OpenAI API key for current user's client. Call after creating client."""
    r = test_client.patch(
        "/clients/me",
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
    """Register user, verify email, return JWT. Use for tests that need mutating actions."""
    with patch("backend.auth.routes.send_email"):
        resp = test_client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.json()
    from backend.models import User

    user = db_session.query(User).filter(User.email == email).first()
    assert user is not None
    verify_resp = test_client.post("/auth/verify-email", json={"token": user.verification_token})
    assert verify_resp.status_code == 200, verify_resp.json()
    token = verify_resp.json()["token"]
    return token

