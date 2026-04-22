from __future__ import annotations

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
    MessageFeedback,
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
    tenant = Tenant(
        name=name,
        api_key=f"ck_{uuid.uuid4().hex[:32]}",
        settings={"language": "en"},
    )
    db_session.add(tenant)
    db_session.flush()
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
        feedback=MessageFeedback.none,
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return message


def test_user_creation(db_session) -> None:
    """Создание пользователя должно успешно сохраняться в БД."""
    user = _create_user(db_session)
    assert user.id is not None
    assert user.email == "user@example.com"
    assert user.created_at is not None
    assert user.updated_at is not None


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


def test_client_creation_with_api_key(db_session) -> None:
    """Tenant API key: ck_ prefix + 32 hex chars = 35 total."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    assert tenant.id is not None
    assert isinstance(tenant.api_key, str)
    assert tenant.api_key.startswith("ck_")
    assert len(tenant.api_key) == 35


def test_client_user_relationship(db_session) -> None:
    """Tenant members relationship works correctly."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    assert tenant.members[0].id == user.id
    assert user.tenant.id == tenant.id


def test_document_creation_default_status_processing(db_session) -> None:
    """Документ по умолчанию должен иметь статус processing."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    document = _create_document(db_session, tenant)
    assert document.status == DocumentStatus.processing


def test_document_status_flow(db_session) -> None:
    """Статус документа должен переходить из processing в ready."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    document = _create_document(db_session, tenant)
    document.status = DocumentStatus.ready
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    assert document.status == DocumentStatus.ready


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


def test_chat_creation_and_relationship(db_session) -> None:
    """Чат должен быть связан с клиентом и иметь session_id."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    chat = _create_chat(db_session, tenant)

    assert chat.tenant_id == tenant.id
    assert chat.session_id is not None
    assert tenant.chats[0].id == chat.id


def test_message_creation(db_session) -> None:
    """Сообщение должно корректно связываться с чатом."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    chat = _create_chat(db_session, tenant)
    message = _create_message(db_session, chat)

    assert message.chat_id == chat.id
    assert message.role == MessageRole.user
    assert message.content == "Hello assistant"
    assert chat.messages[0].id == message.id


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


def test_message_feedback_enum_default(db_session) -> None:
    """Сообщение по умолчанию должно иметь feedback = none."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    chat = _create_chat(db_session, tenant)
    message = _create_message(db_session, chat)

    assert message.feedback == MessageFeedback.none


def test_timestamps_are_utc(db_session) -> None:
    """Все временные метки должны задаваться с использованием datetime.utcnow."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    document = _create_document(db_session, tenant)
    chat = _create_chat(db_session, tenant)
    message = _create_message(db_session, chat)

    now = dt.datetime.utcnow()
    assert document.created_at <= now
    assert chat.created_at <= now
    assert message.created_at <= now


def test_multiple_clients_for_one_user_rejected(db_session) -> None:
    """Сервис не позволяет создать второй тенант для одного пользователя."""
    from fastapi import HTTPException
    from backend.tenants.service import create_tenant

    user = _create_user(db_session, email="double-tenant@example.com")
    create_tenant(user.id, "Tenant 1", db_session)

    try:
        create_tenant(user.id, "Tenant 2", db_session)
        assert False, "Ожидался 409 при попытке создать второго тенанта"
    except HTTPException as e:
        assert e.status_code == 409


def test_multiple_messages_in_chat_order(db_session) -> None:
    """Сообщения в чате должны сохраняться и выбираться в порядке создания."""
    user = _create_user(db_session)
    tenant = _create_client(db_session, user)
    chat = _create_chat(db_session, tenant)

    msg1 = _create_message(db_session, chat, role=MessageRole.user)
    msg2 = _create_message(db_session, chat, role=MessageRole.assistant)

    messages = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    assert [m.id for m in messages] == [msg1.id, msg2.id]
