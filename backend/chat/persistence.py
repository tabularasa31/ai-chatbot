"""Message persistence helpers for the chat pipeline."""

from __future__ import annotations

import logging
import uuid
from time import perf_counter

from sqlalchemy.orm import Session

from backend.chat.language import ResolvedLanguageContext
from backend.chat.language_context import _set_last_response_language
from backend.chat.pii import redact
from backend.contact_sessions.service import record_user_session_turn
from backend.models import Chat, Message, MessageRole, PiiEvent, PiiEventDirection
from backend.observability import TraceHandle

logger = logging.getLogger(__name__)


def _source_docs_for_db(db: Session, document_ids: list[uuid.UUID]) -> list[uuid.UUID] | None:
    return document_ids if "postgresql" in str(db.bind.url) else None


def _create_message(
    db: Session,
    *,
    chat: Chat,
    tenant_id: uuid.UUID,
    role: MessageRole,
    content: str,
    source_documents: list[uuid.UUID] | None = None,
    optional_entity_types: set[str] | None = None,
    operator_user_id: uuid.UUID | None = None,
) -> Message:
    """Persist one turn's message with its ORIGINAL text.

    Redaction is not a storage concern: the row keeps what the user (or bot)
    actually wrote, and consumers that ship the text outside the platform mask
    it at that boundary. The redaction pass here is audit-only — it records
    which entity types will be masked whenever this message is sent to OpenAI
    (as the question or as prompt history) and never rewrites ``content``.

    ``operator_user_id`` is set only on ``MessageRole.operator`` rows whose
    author resolves to a tenant user; it stays NULL for an unattributed
    operator reply and for every user / assistant row.
    """
    message = Message(
        id=uuid.uuid4(),
        chat_id=chat.id,
        role=role,
        content=content,
        source_documents=source_documents,
        operator_user_id=operator_user_id,
    )
    db.add(message)
    redaction = redact(content, optional_entity_types=optional_entity_types)
    if redaction.was_redacted:
        # SQLite enforces FK at row level and SQLAlchemy's UoW does not reorder
        # PiiEvent (FK-only, no relationship) ahead of its parent Message, so
        # flush the Message INSERT before queuing dependent PiiEvent rows.
        db.flush()
        for entity in redaction.entities_found:
            db.add(
                PiiEvent(
                    tenant_id=tenant_id,
                    chat_id=chat.id,
                    message_id=message.id,
                    direction=PiiEventDirection.llm_request,
                    entity_type=entity.type,
                    count=entity.count,
                )
            )
    return message


def _finalize_persisted_messages(
    *,
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    extra_tokens: int,
    set_rephrase_flag: bool = False,
    count_user_turn: bool = True,
) -> None:
    chat.tokens_used = int(chat.tokens_used or 0) + int(extra_tokens)
    # Authoritative single write point for the zero-RAG-hits tracker so the
    # flag stays consistent regardless of which handler (Greeting, Escalation,
    # Rag) persisted this turn. Defaults to False: any successful reply resets
    # the tracker, so two unrelated zero-hits turns separated by an unrelated
    # turn don't collapse into the consecutive-failure path.
    chat.last_reply_was_rephrase_prompt = set_rephrase_flag
    db.add(chat)
    # Flush the turn's own rows (messages + chat update) BEFORE the best-effort
    # session-turn tracking savepoint. ``begin_nested()`` implicitly flushes all
    # pending state when it opens the savepoint, so without this a real failure
    # on *these* rows (e.g. an asyncpg ``DataError`` on a naive/aware datetime,
    # or a constraint violation) would be raised inside the ``try`` below,
    # swallowed and mislabeled by the tracking ``except``, and then re-surface as
    # a masked ``PendingRollbackError`` at commit. Flushing here lets the
    # original exception propagate; the explicit rollback keeps the session from
    # returning to the pool in a broken state.
    try:
        db.flush()
    except Exception:
        db.rollback()
        raise
    # Session-turn tracking is best-effort and isolated in a savepoint: the
    # turn's messages are already flushed above, so a failure here rolls back
    # only the tracking writes and never masks the persisted turn.
    try:
        with db.begin_nested():
            record_user_session_turn(
                db,
                tenant_id=tenant_id,
                user_context=chat.user_context,
                ended_at=chat.ended_at,
                count_turn=count_user_turn,
            )
    except Exception:
        logger.warning(
            "user_session_turn_tracking_failed: tenant_id=%s session_id=%s",
            tenant_id,
            chat.session_id,
            exc_info=True,
        )
    # Defense-in-depth: a failed commit still rolls the session back and
    # re-raises rather than leaving a poisoned transaction for the next
    # statement on the same session (widget source lookup, post-turn analytics).
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _persist_user_only_turn(
    db: Session,
    *,
    chat: Chat,
    tenant_id: uuid.UUID,
    user_content: str,
    optional_entity_types: set[str] | None = None,
) -> Message:
    """Persist a visitor turn that gets no bot reply.

    Used while an operator holds the chat (``OperatorState.live``): the
    message is recorded exactly as any other user turn — same redaction audit,
    same ``PiiEvent`` rows, same commit path — and nothing is generated in
    response. Composed from the shared helpers rather than written out so the
    muted path can never drift from the normal one.

    No token accounting: the turn cost nothing, no LLM was called.
    """
    message = _create_message(
        db,
        chat=chat,
        tenant_id=tenant_id,
        role=MessageRole.user,
        content=user_content,
        optional_entity_types=optional_entity_types,
    )
    _finalize_persisted_messages(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        extra_tokens=0,
    )
    return message


def _persist_operator_message(
    db: Session,
    *,
    chat: Chat,
    tenant_id: uuid.UUID,
    content: str,
    operator_user_id: uuid.UUID | None,
    optional_entity_types: set[str] | None = None,
) -> Message:
    """Persist a human operator's reply into the chat thread.

    The thread is the single ledger: an answer written in the console and one
    that arrives by e-mail land as the same kind of row, so every later
    consumer (widget history, the console, the phase-3 knowledge loop) reads
    one shape regardless of which channel produced it.
    """
    message = _create_message(
        db,
        chat=chat,
        tenant_id=tenant_id,
        role=MessageRole.operator,
        content=content,
        optional_entity_types=optional_entity_types,
        operator_user_id=operator_user_id,
    )
    _finalize_persisted_messages(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        extra_tokens=0,
        # ``conversation_turns`` counts *user* turns. An operator's reply is
        # activity on the session — so the session is still touched — but it
        # is not a turn the visitor took, and counting it would inflate the
        # metric precisely on the conversations a human had to step into.
        count_user_turn=False,
    )
    return message


def _persist_turn(
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    user_content: str,
    assistant_content: str,
    document_ids: list[uuid.UUID],
    extra_tokens: int,
    optional_entity_types: set[str] | None = None,
    trace: TraceHandle | None = None,
    set_rephrase_flag: bool = False,
) -> tuple[Message, Message]:
    _persist_start = perf_counter()
    _persist_span = None
    if trace is not None:
        _persist_span = trace.span(
            name="persistence",
            input={"doc_count": len(document_ids), "tokens": extra_tokens},
        )
    user_message = _create_message(
        db,
        chat=chat,
        tenant_id=tenant_id,
        role=MessageRole.user,
        content=user_content,
        optional_entity_types=optional_entity_types,
    )
    assistant_message = _create_message(
        db,
        chat=chat,
        tenant_id=tenant_id,
        role=MessageRole.assistant,
        content=assistant_content,
        source_documents=_source_docs_for_db(db, document_ids),
        optional_entity_types=optional_entity_types,
    )
    _finalize_persisted_messages(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        extra_tokens=extra_tokens,
        set_rephrase_flag=set_rephrase_flag,
    )
    if _persist_span is not None:
        _persist_span.end(
            output={"user_msg_id": str(user_message.id), "asst_msg_id": str(assistant_message.id)},
            metadata={"duration_ms": round((perf_counter() - _persist_start) * 1000, 2)},
        )
    return user_message, assistant_message


def _persist_turn_with_response_language(
    *,
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    response_language: str | None,
    resolution_reason: str | None,
    user_content: str,
    assistant_content: str,
    document_ids: list[uuid.UUID],
    extra_tokens: int,
    optional_entity_types: set[str] | None = None,
    language_context: ResolvedLanguageContext | None = None,
    trace: TraceHandle | None = None,
    set_rephrase_flag: bool = False,
) -> tuple[Message, Message]:
    _set_last_response_language(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        response_language=response_language,
        resolution_reason=resolution_reason,
        language_context=language_context,
    )
    return _persist_turn(
        db,
        chat,
        tenant_id,
        user_content,
        assistant_content,
        document_ids,
        extra_tokens,
        optional_entity_types=optional_entity_types,
        trace=trace,
        set_rephrase_flag=set_rephrase_flag,
    )


def _persist_assistant_message(
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    assistant_content: str,
    extra_tokens: int,
    optional_entity_types: set[str] | None = None,
) -> None:
    _create_message(
        db,
        chat=chat,
        tenant_id=tenant_id,
        role=MessageRole.assistant,
        content=assistant_content,
        source_documents=None,
        optional_entity_types=optional_entity_types,
    )
    _finalize_persisted_messages(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        extra_tokens=extra_tokens,
    )


def _persist_assistant_message_with_response_language(
    *,
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    response_language: str | None,
    resolution_reason: str | None,
    assistant_content: str,
    extra_tokens: int,
    optional_entity_types: set[str] | None = None,
    language_context: ResolvedLanguageContext | None = None,
) -> None:
    _set_last_response_language(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        response_language=response_language,
        resolution_reason=resolution_reason,
        language_context=language_context,
    )
    _persist_assistant_message(
        db,
        chat,
        tenant_id,
        assistant_content,
        extra_tokens,
        optional_entity_types=optional_entity_types,
    )
