from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.exc import IntegrityError

from backend.models import (
    Chat,
    Client,
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


def _create_client(db_session, user: User, name: str = "Test Client") -> Client:
    client = Client(
        user_id=user.id,
        name=name,
        api_key=f"{uuid.uuid4().hex[:32]}",
        settings={"language": "en"},
    )
    db_session.add(client)
    db_session.commit()
    db_session.refresh(client)
    return client


def _create_document(
    db_session,
    client: Client,
    status: DocumentStatus = DocumentStatus.processing,
) -> Document:
    document = Document(
        client_id=client.id,
        filename="test.pdf",
        file_type=DocumentType.pdf,
        parsed_text="Hello world",
        status=status,
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    return document


def _create_chat(db_session, client: Client) -> Chat:
    chat = Chat(
        client_id=client.id,
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
    """Клиент должен создаваться с уникальным API-ключом длиной 32 символа."""
    user = _create_user(db_session)
    client = _create_client(db_session, user)
    assert client.id is not None
    assert isinstance(client.api_key, str)
    assert len(client.api_key) == 32


def test_client_user_relationship(db_session) -> None:
    """У клиента должна корректно работать связь с пользователем."""
    user = _create_user(db_session)
    client = _create_client(db_session, user)
    assert client.user.id == user.id
    assert user.clients[0].id == client.id


def test_document_creation_default_status_processing(db_session) -> None:
    """Документ по умолчанию должен иметь статус processing."""
    user = _create_user(db_session)
    client = _create_client(db_session, user)
    document = _create_document(db_session, client)
    assert document.status == DocumentStatus.processing


def test_document_status_flow(db_session) -> None:
    """Статус документа должен переходить из processing в ready."""
    user = _create_user(db_session)
    client = _create_client(db_session, user)
    document = _create_document(db_session, client)
    document.status = DocumentStatus.ready
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)
    assert document.status == DocumentStatus.ready


def test_cascade_delete_client_deletes_documents(db_session) -> None:
    """Удаление клиента должно каскадно удалять документы."""
    user = _create_user(db_session)
    client = _create_client(db_session, user)
    _create_document(db_session, client)
    _create_document(db_session, client)
    client_id = client.id

    db_session.delete(client)
    db_session.commit()

    remaining_docs = (
        db_session.query(Document)
        .filter(Document.client_id == client_id)
        .all()
    )
    assert remaining_docs == []


def test_chat_creation_and_relationship(db_session) -> None:
    """Чат должен быть связан с клиентом и иметь session_id."""
    user = _create_user(db_session)
    client = _create_client(db_session, user)
    chat = _create_chat(db_session, client)

    assert chat.client_id == client.id
    assert chat.session_id is not None
    assert client.chats[0].id == chat.id


def test_message_creation(db_session) -> None:
    """Сообщение должно корректно связываться с чатом."""
    user = _create_user(db_session)
    client = _create_client(db_session, user)
    chat = _create_chat(db_session, client)
    message = _create_message(db_session, chat)

    assert message.chat_id == chat.id
    assert message.role == MessageRole.user
    assert message.content == "Hello assistant"
    assert chat.messages[0].id == message.id


def test_cascade_delete_chat_deletes_messages(db_session) -> None:
    """Удаление чата должно каскадно удалять сообщения."""
    user = _create_user(db_session)
    client = _create_client(db_session, user)
    chat = _create_chat(db_session, client)
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
    client = _create_client(db_session, user)
    chat = _create_chat(db_session, client)
    message = _create_message(db_session, chat)

    assert message.feedback == MessageFeedback.none


def test_timestamps_are_utc(db_session) -> None:
    """Все временные метки должны задаваться с использованием datetime.utcnow."""
    user = _create_user(db_session)
    client = _create_client(db_session, user)
    document = _create_document(db_session, client)
    chat = _create_chat(db_session, client)
    message = _create_message(db_session, chat)

    now = dt.datetime.utcnow()
    assert document.created_at <= now
    assert chat.created_at <= now
    assert message.created_at <= now


def test_multiple_clients_for_one_user(db_session) -> None:
    """Один пользователь может иметь несколько клиентов."""
    user = _create_user(db_session)
    c1 = _create_client(db_session, user, name="Client 1")
    c2 = _create_client(db_session, user, name="Client 2")

    db_session.refresh(user)
    client_names = {c.name for c in user.clients}
    assert client_names == {"Client 1", "Client 2"}
    assert c1.user_id == user.id
    assert c2.user_id == user.id


def test_multiple_messages_in_chat_order(db_session) -> None:
    """Сообщения в чате должны сохраняться и выбираться в порядке создания."""
    user = _create_user(db_session)
    client = _create_client(db_session, user)
    chat = _create_chat(db_session, client)

    msg1 = _create_message(db_session, chat, role=MessageRole.user)
    msg2 = _create_message(db_session, chat, role=MessageRole.assistant)

    messages = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    assert [m.id for m in messages] == [msg1.id, msg2.id]

