from __future__ import annotations

import importlib.util
from pathlib import Path

import datetime as dt
import uuid

from sqlalchemy.exc import IntegrityError

from backend.models import (
    Chat,
    Tenant,
    Document,
    DocumentStatus,
    DocumentType,
    Message,
    MessageRole,
    User,
)


def _create_user(db_session, email: str = "user@example.com") -> User:
    user = User(email=email, password_hash="hashed")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _create_client(db_session, user: User, name: str = "Test Tenant") -> Tenant:
    from backend.tenants.api_keys_service import create_initial_api_key

    tenant = Tenant(name=name, settings={"language": "en"})
    db_session.add(tenant)
    db_session.flush()
    create_initial_api_key(tenant.id, db_session, created_by_user_id=user.id)
    user.tenant_id = tenant.id
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


def _create_document(
    db_session,
    tenant: Tenant,
    status: DocumentStatus = DocumentStatus.processing,
) -> Document:
    document = Document(
        tenant_id=tenant.id,
        filename="test.pdf",
        file_type=DocumentType.pdf,
        parsed_text="Hello world",
        status=status,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document


def _create_chat(db_session, tenant: Tenant) -> Chat:
    chat = Chat(
        tenant_id=tenant.id,
        session_id=uuid.uuid4(),
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    return chat


def _create_message(
    db_session,
    chat: Chat,
    role: MessageRole = MessageRole.user,
) -> Message:
    message = Message(
        chat_id=chat.id,
        role=role,
        content="Hello assistant",
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return message


def test_user_duplicate_email_constraint(db_session) -> None:
    """Дубликат email должен приводить к ошибке целостности."""
    _create_user(db_session, email="dup@example.com")
    user2 = User(email="dup@example.com", password_hash="hashed2")
    db_session.add(user2)
    try:
        db_session.commit()
        assert False, "Ожидался IntegrityError при дубликате email"
    except IntegrityError:
        db_session.rollback()


def test_orm_relationships_and_defaults_journey(db_session) -> None:
    """User -> Tenant -> Chat -> Message relationship chain and timestamp
    defaults, in one pass — a migration dropping a backref, a timestamp
    default, or an FK would break one of these steps.
    """
    user = _create_user(db_session)
    assert user.id is not None
    assert user.created_at is not None
    assert user.updated_at is not None

    tenant = _create_client(db_session, user)
    assert tenant.members[0].id == user.id
    assert user.tenant.id == tenant.id

    chat = _create_chat(db_session, tenant)
    assert chat.tenant_id == tenant.id
    assert chat.session_id is not None
    assert tenant.chats[0].id == chat.id

    message = _create_message(db_session, chat)
    assert message.chat_id == chat.id
    assert message.role == MessageRole.user
    assert message.content == "Hello assistant"
    assert chat.messages[0].id == message.id


def test_document_creation_default_status_processing(db_session) -> None:
    """Документ по умолчанию должен иметь статус processing."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    document = _create_document(db_session, tenant)
    assert document.status == DocumentStatus.processing


def test_cascade_delete_client_deletes_documents(db_session) -> None:
    """Удаление клиента должно каскадно удалять документы."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    _create_document(db_session, tenant)
    _create_document(db_session, tenant)
    tenant_id = tenant.id

    db_session.delete(tenant)
    db_session.commit()

    remaining_docs = (
        db_session.query(Document)
        .filter(Document.tenant_id == tenant_id)
        .all()
    )
    assert remaining_docs == []


def test_cascade_delete_chat_deletes_messages(db_session) -> None:
    """Удаление чата должно каскадно удалять сообщения."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    chat = _create_chat(db_session, tenant)
    _create_message(db_session, chat)
    chat_id = chat.id

    db_session.delete(chat)
    db_session.commit()

    remaining_messages = (
        db_session.query(Message)
        .filter(Message.chat_id == chat_id)
        .all()
    )
    assert remaining_messages == []


# ---------------------------------------------------------------------------
# before_flush tzinfo-stripping listener
#
# Defense-in-depth registered in ``backend/models/base.py`` after the prod
# "Internal error" surfaced in Sentry ``PYTHON-FASTAPI-H`` — see PR #680. The
# listener inspects every new/dirty ORM object before flush and normalises
# tz-aware values bound for naive ``DateTime`` columns. Without it, asyncpg
# rejects aware values for ``TIMESTAMP WITHOUT TIME ZONE`` columns and the
# session enters PendingRollback.
# ---------------------------------------------------------------------------


def test_before_flush_strips_tzinfo_on_naive_datetime_columns(db_session) -> None:
    """ORM attribute write of an aware datetime to a naive DateTime column
    must be normalised to naive at flush time.
    """
    user = _create_user(db_session, email="aware-tzinfo@example.com")
    tenant = _create_client(db_session, user)

    chat = Chat(tenant_id=tenant.id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()

    # Assign an aware UTC datetime — listener should strip tzinfo at flush.
    chat.session_ended_event_at = dt.datetime.now(dt.UTC)
    assert chat.session_ended_event_at.tzinfo is not None, "test setup precondition"
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    assert chat.session_ended_event_at is not None
    assert chat.session_ended_event_at.tzinfo is None, (
        "before_flush listener must have stripped tzinfo from naive DateTime "
        "column; aware values crash asyncpg on the widget chat path"
    )


def test_before_flush_leaves_none_datetimes_untouched(db_session) -> None:
    """The listener must not error on objects whose datetime column is
    ``None`` (e.g. newly-inserted chats with optional ``session_ended_event_at``).
    """
    user = _create_user(db_session, email="none-datetime@example.com")
    tenant = _create_client(db_session, user)

    chat = Chat(tenant_id=tenant.id, session_id=uuid.uuid4())
    assert chat.session_ended_event_at is None
    db_session.add(chat)
    db_session.commit()  # No exception expected.
    db_session.refresh(chat)
    assert chat.session_ended_event_at is None


def test_before_flush_leaves_naive_datetimes_unchanged(db_session) -> None:
    """Already-naive values pass through the listener untouched — no
    accidental ``replace(tzinfo=None)`` re-roundtrip that could change
    timestamps.
    """
    user = _create_user(db_session, email="naive-datetime@example.com")
    tenant = _create_client(db_session, user)

    chat = Chat(tenant_id=tenant.id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()

    naive_value = dt.datetime(2026, 5, 13, 12, 34, 56)
    chat.session_ended_event_at = naive_value
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    assert chat.session_ended_event_at == naive_value
    assert chat.session_ended_event_at.tzinfo is None


def test_before_flush_converts_non_utc_aware_to_utc_then_strips_tzinfo(db_session) -> None:
    """Regression for Codex P1 review on PR #682.

    The naive-datetime listener used to ``value.replace(tzinfo=None)``,
    which preserves wall time. A non-UTC aware input like
    ``2026-05-13T10:00:00-04:00`` (i.e. 14:00 UTC) would have been stored
    as naive ``10:00:00`` — interpreted as UTC by every downstream
    comparison, so the recorded instant would be 4 hours behind reality.
    The listener now ``astimezone(UTC)``-first and only then strips
    ``tzinfo``, preserving the instant.
    """
    user = _create_user(db_session, email="non-utc-aware@example.com")
    tenant = _create_client(db_session, user)

    chat = Chat(tenant_id=tenant.id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()

    # 10:00 in a -04:00 zone == 14:00 UTC.
    eastern = dt.timezone(dt.timedelta(hours=-4))
    chat.session_ended_event_at = dt.datetime(2026, 5, 13, 10, 0, 0, tzinfo=eastern)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    assert chat.session_ended_event_at == dt.datetime(2026, 5, 13, 14, 0, 0), (
        "listener must convert to UTC before stripping tzinfo — bare "
        "replace(tzinfo=None) would have stored 10:00 and silently shifted "
        "the instant by 4 hours"
    )
    assert chat.session_ended_event_at.tzinfo is None


# ---------------------------------------------------------------------------
# Alembic migration file checks
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[1] / "backend" / "migrations" / "versions"
)
_MAX_REVISION_LEN = 32


def _load_revision(path: Path) -> str | None:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "revision", None)


def test_alembic_revisions_fit_limit_and_are_unique() -> None:
    seen: dict[str, str] = {}
    duplicates: list[tuple[str, str]] = []
    too_long: list[tuple[str, int]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        revision = _load_revision(path)
        assert revision is not None, f"{path.name} must define revision"
        if len(revision) > _MAX_REVISION_LEN:
            too_long.append((path.name, len(revision)))
        if revision in seen:
            duplicates.append((revision, path.name))
        else:
            seen[revision] = path.name
    assert not too_long, f"Revision ids too long: {too_long}"
    assert not duplicates, f"Duplicate revision ids: {duplicates}"
