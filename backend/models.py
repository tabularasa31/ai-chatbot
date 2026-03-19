from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# Позволяем использовать UUID и ARRAY в SQLite (для тестов),
# мапя их на совместимые типы.
@compiles(PG_UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw) -> str:  # type: ignore[override]
    return "CHAR(36)"


@compiles(ARRAY, "sqlite")
def compile_array_sqlite(type_, compiler, **kw) -> str:  # type: ignore[override]
    return "TEXT"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class DocumentType(str, enum.Enum):
    pdf = "pdf"
    markdown = "markdown"
    swagger = "swagger"


class DocumentStatus(str, enum.Enum):
    processing = "processing"
    ready = "ready"
    error = "error"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class MessageFeedback(str, enum.Enum):
    none = "none"
    up = "up"
    down = "down"


class User(Base):
    __tablename__ = "users"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False, server_default="false")
    client_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="SET NULL", use_alter=True, name="fk_users_client_id"),
        nullable=True,
        index=True,
    )
    is_verified = Column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    verification_token = Column(String(128), nullable=True, unique=True)
    verification_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    clients = relationship(
        "Client",
        back_populates="user",
        foreign_keys="Client.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Client(Base):
    __tablename__ = "clients"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    api_key = Column(String(32), unique=True, nullable=False, index=True)
    openai_api_key = Column(String(500), nullable=True, default=None)
    settings = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    user = relationship("User", back_populates="clients", foreign_keys="Client.user_id")
    documents = relationship(
        "Document",
        back_populates="client",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    chats = relationship(
        "Chat",
        back_populates="client",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Document(Base):
    __tablename__ = "documents"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(String(255), nullable=False)
    file_type = Column(
        Enum(DocumentType, native_enum=False),
        nullable=False,
    )
    parsed_text = Column(Text, nullable=True)
    status = Column(
        Enum(DocumentStatus, native_enum=False),
        nullable=False,
        default=DocumentStatus.processing,
        index=True,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    client = relationship("Client", back_populates="documents")
    embeddings = relationship(
        "Embedding",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_text = Column(Text, nullable=False)
    # В бою это должен быть pgvector(1536); для миграций/тестов тип уточняется отдельно.
    vector = Column(
        ARRAY(PG_UUID(as_uuid=False)),
        nullable=True,
    )
    # имя атрибута не может быть `metadata` (зарезервировано в SQLAlchemy),
    # поэтому оставляем имя столбца "metadata", но меняем имя Python-атрибута
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    document = relationship("Document", back_populates="embeddings")



class Chat(Base):
    __tablename__ = "chats"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    tokens_used = Column(
        Integer,
        nullable=False,
        server_default="0",
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    client = relationship("Client", back_populates="chats")
    messages = relationship(
        "Message",
        back_populates="chat",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    chat_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(
        Enum(MessageRole, native_enum=False),
        nullable=False,
    )
    content = Column(Text, nullable=False)
    source_documents = Column(
        ARRAY(PG_UUID(as_uuid=True)),
        nullable=True,
    )
    feedback = Column(
        Enum(MessageFeedback, native_enum=False),
        nullable=False,
        default=MessageFeedback.none,
    )
    ideal_answer = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    chat = relationship("Chat", back_populates="messages")


Index(
    "ix_embeddings_vector",
    Embedding.vector,
)

