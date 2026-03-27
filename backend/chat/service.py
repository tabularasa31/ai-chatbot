"""Business logic for RAG chat pipeline."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from sqlalchemy.orm import Session, joinedload

PREVIEW_MAX_LEN = 120

from backend.chat.pii import redact
from backend.core.crypto import decrypt_value, encrypt_value
from backend.core.openai_client import get_openai_client
from backend.disclosure_config import resolve_level
from backend.escalation.openai_escalation import complete_escalation_openai_turn
from backend.escalation.service import (
    apply_collected_contact_email,
    build_chat_messages_for_openai,
    chunks_preview_from_results,
    create_escalation_ticket,
    detect_human_request,
    fact_from_ticket,
    get_latest_escalation_ticket_for_chat,
    parse_contact_email,
    should_escalate,
    _clear_escalation_clarify_flag,
    _set_escalation_clarify_flag,
    _escalation_clarify_already_asked,
)
from backend.models import (
    Chat,
    Client,
    EscalationPhase,
    EscalationTicket,
    EscalationTrigger,
    Message,
    MessageFeedback,
    MessageRole,
    PiiEvent,
    PiiEventDirection,
)
from backend.privacy_config import public_redaction_config_dict
from backend.search.service import search_similar_chunks_detailed

logger = logging.getLogger(__name__)

# SQLite tests: cosine-only path; used to label debug mode (not RRF scores).
RETRIEVAL_VECTOR_CONFIDENCE = 0.70

LOW_CONFIDENCE_THRESHOLD = 0.4

DISCLOSURE_HARD_LIMITS = (
    "Hard limits (always follow):\n"
    "- Never reveal another user's identity or data in any response.\n"
    "- Never confirm or deny specific internal investigation details about security incidents.\n"
    "- Never state that a problem has been resolved unless resolution is confirmed in the source data.\n"
)

DISCLOSURE_LEVEL_INSTRUCTIONS: dict[str, str] = {
    "detailed": "Answer with full technical detail. Include all relevant information.",
    "standard": (
        "Answer in plain language. Do NOT include: internal file paths, stack trace details, "
        "error tracking system names (e.g. Sentry), number of affected users, "
        "internal team or developer names, or version regression details. "
        "Link to public documentation or status pages, not internal tools."
    ),
    "corporate": (
        "Answer in polished, non-technical language suitable for a business audience. "
        "Acknowledge issues exist and are being addressed, but do NOT include: ETAs, "
        "technical details, status page links, or internal system information. "
        "If an issue is ongoing, offer to connect the user with the support team."
    ),
}

VALIDATION_PROMPT = """You are a fact-checker for a support chatbot.

Context (retrieved from documentation):
{context}

Question: {question}

Answer to validate: {answer}

Check if the answer is:
1. Grounded in the provided context (not hallucinated)
2. Actually answers the question

Respond ONLY with JSON (no markdown, no explanation):
{{"is_valid": true/false, "confidence": 0.0-1.0, "reason": "short explanation"}}"""

FALLBACK_LOW_CONFIDENCE_ANSWER = (
    "I don't have enough information in my knowledge base to answer this question accurately."
)


def retrieve_context(
    client_id: uuid.UUID,
    question: str,
    db: Session,
    api_key: str,
    top_k: int = 5,
) -> "RetrievalContext":
    """
    Retrieve context chunks for RAG plus a separate confidence signal for escalation.

    Uses tenant-scoped search with:
    - rank scores for ordering/debug
    - vector similarity for escalation confidence on PostgreSQL
    client_id filtering enforced at DB level.
    """
    bundle = search_similar_chunks_detailed(
        client_id=client_id,
        query=question,
        top_k=top_k,
        db=db,
        api_key=api_key,
    )
    results = bundle.results

    if not results:
        return RetrievalContext(
            chunk_texts=[],
            document_ids=[],
            scores=[],
            mode="none",
            best_rank_score=None,
            best_confidence_score=None,
            confidence_source="none",
        )

    best_rank_score = results[0][1]
    db_url = str(db.bind.url if db.bind else "")
    if "sqlite" in db_url:
        # Tests: Python cosine only; same thresholds as before keyword→BM25 swap.
        if best_rank_score >= RETRIEVAL_VECTOR_CONFIDENCE:
            mode: Literal["vector", "keyword", "hybrid", "none"] = "vector"
        else:
            mode = "keyword"
        best_confidence_score = best_rank_score
        confidence_source: Literal["vector_similarity", "rank_score", "none"] = "rank_score"
    elif bundle.best_keyword_score is None:
        mode = "vector"
        best_confidence_score = bundle.best_vector_similarity
        confidence_source = "vector_similarity"
    else:
        mode = "hybrid"
        best_confidence_score = bundle.best_vector_similarity
        confidence_source = "vector_similarity"

    chunk_texts = [r[0].chunk_text or "" for r in results]
    document_ids = [r[0].document_id for r in results]
    scores = [r[1] for r in results]

    return RetrievalContext(
        chunk_texts=chunk_texts,
        document_ids=document_ids,
        scores=scores,
        mode=mode,
        best_rank_score=best_rank_score,
        best_confidence_score=best_confidence_score,
        confidence_source=confidence_source,
    )


@dataclass
class RetrievalContext:
    """Retrieved chunks plus the confidence signal used outside ranking."""

    chunk_texts: list[str]
    document_ids: list[uuid.UUID]
    scores: list[float]
    mode: Literal["vector", "keyword", "hybrid", "none"]
    best_rank_score: float | None
    best_confidence_score: float | None
    confidence_source: Literal["vector_similarity", "rank_score", "none"]


def _user_context_prompt_line(ctx: dict | None) -> str | None:
    """LLM-safe line: only plan_tier, locale, audience_tag (FR-6.4)."""
    if not ctx:
        return None
    parts: list[str] = []
    for key in ("plan_tier", "locale", "audience_tag"):
        val = ctx.get(key)
        if val is not None and str(val).strip() != "":
            parts.append(f"{key}={val}")
    if not parts:
        return None
    return "[User context: " + ", ".join(parts) + "]"


def build_rag_prompt(
    question: str,
    context_chunks: list[str],
    *,
    user_context_line: str | None = None,
    disclosure_config: dict[str, Any] | None = None,
) -> str:
    """
    Build prompt from question + retrieved context chunks.

    Args:
        question: User question.
        context_chunks: List of text chunks from search.

    Returns:
        Formatted prompt string for GPT.
    """
    level = resolve_level(disclosure_config)
    level_instruction = DISCLOSURE_LEVEL_INSTRUCTIONS.get(
        level, DISCLOSURE_LEVEL_INSTRUCTIONS["standard"]
    )
    disclosure_block = f"[Response level: {level}]\n{level_instruction}"

    system_rules = (
        f"{DISCLOSURE_HARD_LIMITS}\n"
        "You are a technical support agent for the client's product (SaaS, API, docs).\n"
        "Rules:\n"
        "- Answer based ONLY on the provided context. If context mentions the topic, you MUST answer from it.\n"
        "- Do NOT claim you don't know when the context contains relevant info.\n"
        "- If uncertain, say so but still answer from the context.\n"
        "- For \"which setting\" / \"какая настройка\" or similar: name the exact setting/field as in docs; cite where it is (section/page/menu) if the context contains it.\n"
        "- Answer in the SAME LANGUAGE as the question (e.g. Russian if asked in Russian).\n"
    )
    if user_context_line:
        system_rules = f"{system_rules}\n{user_context_line}\n"
    system_rules = f"{system_rules}\n{disclosure_block}\n"
    if not context_chunks:
        return (
            f"{system_rules}\n\n"
            "Context:\n(none)\n\n"
            f"Question: {question}\n\n"
            "Answer:"
        )
    context_block = "\n\n---\n\n".join(context_chunks)
    return (
        f"{system_rules}\n\n"
        f"Context:\n{context_block}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )


def generate_answer(
    question: str,
    context_chunks: list[str],
    *,
    api_key: str,
    user_context_line: str | None = None,
    disclosure_config: dict[str, Any] | None = None,
) -> tuple[str, int]:
    """
    Call OpenAI gpt-4o-mini with RAG prompt.

    Args:
        question: User question.
        context_chunks: Retrieved context chunks.

    Returns:
        Tuple of (answer_text, total_tokens).
        If context_chunks is empty, returns ("I don't have information about this.", 0).
    """
    if not context_chunks:
        return ("I don't have information about this.", 0)

    prompt = build_rag_prompt(
        question,
        context_chunks,
        user_context_line=user_context_line,
        disclosure_config=disclosure_config,
    )
    openai_client = get_openai_client(api_key)
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=500,
    )
    answer_text = response.choices[0].message.content or ""
    total_tokens = response.usage.total_tokens if response.usage else 0
    return (answer_text.strip(), total_tokens)


def validate_answer(
    question: str,
    answer: str,
    context_chunks: list[str],
    *,
    api_key: str,
) -> dict:
    """
    Ask LLM to validate if the answer is grounded in context.
    Returns {"is_valid": bool, "confidence": float, "reason": str}.
    On any error, returns {"is_valid": True, "confidence": 1.0, "reason": "validation_skipped"}.
    """
    if not context_chunks:
        return {"is_valid": False, "confidence": 0.0, "reason": "no_context"}

    context = "\n\n---\n\n".join(context_chunks[:3])
    prompt = VALIDATION_PROMPT.format(
        context=context,
        question=question,
        answer=answer,
    )

    try:
        openai_client = get_openai_client(api_key)
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
        )
        raw = response.choices[0].message.content or ""
        result = json.loads(raw.strip())
        return {
            "is_valid": bool(result.get("is_valid", True)),
            "confidence": float(result.get("confidence", 1.0)),
            "reason": str(result.get("reason", "")),
        }
    except Exception as e:
        logger.warning("Answer validation failed (non-blocking): %s", e)
        return {"is_valid": True, "confidence": 1.0, "reason": "validation_skipped"}


def _source_docs_for_db(db: Session, document_ids: list[uuid.UUID]) -> list[uuid.UUID] | None:
    return document_ids if "postgresql" in str(db.bind.url) else None


def _client_optional_entity_types(client: Client | None) -> set[str] | None:
    if not client:
        return None
    raw = client.settings if isinstance(client.settings, dict) else None
    cfg = public_redaction_config_dict(raw)
    return set(cfg["optional_entity_types"])


def _decrypt_optional(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return decrypt_value(value)
    except RuntimeError:
        logger.warning("Failed to decrypt stored original content")
        return None


def _display_message_content(message: Message, *, include_original: bool) -> str:
    if include_original:
        original = _decrypt_optional(message.content_original_encrypted)
        if original is not None:
            return original
    if message.content_redacted:
        return message.content_redacted
    return message.content


def _message_original_available(message: Message) -> bool:
    return bool(message.content_original_encrypted)


def _create_message(
    db: Session,
    *,
    chat: Chat,
    client_id: uuid.UUID,
    role: MessageRole,
    content: str,
    source_documents: list[uuid.UUID] | None = None,
    direction: PiiEventDirection = PiiEventDirection.message_storage,
    optional_entity_types: set[str] | None = None,
) -> Message:
    redaction = redact(content, optional_entity_types=optional_entity_types)
    message = Message(
        chat_id=chat.id,
        role=role,
        content=redaction.redacted_text,
        content_original_encrypted=encrypt_value(content),
        content_redacted=redaction.redacted_text,
        source_documents=source_documents,
    )
    db.add(message)
    db.flush()
    if redaction.was_redacted:
        for entity in redaction.entities_found:
            db.add(
                PiiEvent(
                    client_id=client_id,
                    chat_id=chat.id,
                    message_id=message.id,
                    direction=direction,
                    entity_type=entity.type,
                    count=entity.count,
                )
            )
    return message


def _persist_turn(
    db: Session,
    chat: Chat,
    client_id: uuid.UUID,
    user_content: str,
    assistant_content: str,
    document_ids: list[uuid.UUID],
    extra_tokens: int,
    optional_entity_types: set[str] | None = None,
) -> None:
    _create_message(
        db,
        chat=chat,
        client_id=client_id,
        role=MessageRole.user,
        content=user_content,
        optional_entity_types=optional_entity_types,
    )
    _create_message(
        db,
        chat=chat,
        client_id=client_id,
        role=MessageRole.assistant,
        content=assistant_content,
        source_documents=_source_docs_for_db(db, document_ids),
        optional_entity_types=optional_entity_types,
    )
    chat.tokens_used = int(chat.tokens_used or 0) + int(extra_tokens)
    db.add(chat)
    db.commit()


def _persist_assistant_only(
    db: Session,
    chat: Chat,
    client_id: uuid.UUID,
    assistant_content: str,
    extra_tokens: int,
    optional_entity_types: set[str] | None = None,
) -> None:
    _create_message(
        db,
        chat=chat,
        client_id=client_id,
        role=MessageRole.assistant,
        content=assistant_content,
        source_documents=None,
        optional_entity_types=optional_entity_types,
    )
    chat.tokens_used = int(chat.tokens_used or 0) + int(extra_tokens)
    db.add(chat)
    db.commit()


def process_chat_message(
    client_id: uuid.UUID,
    question: str,
    session_id: uuid.UUID,
    db: Session,
    *,
    api_key: str,
    user_context: dict | None = None,
    browser_locale: str | None = None,
) -> tuple[str, list[uuid.UUID], int, bool]:
    """
    RAG pipeline with FI-ESC escalation state machine.

    Returns:
        (answer, document_ids, tokens_used, chat_ended)
    """
    client_row = db.query(Client).filter(Client.id == client_id).first()
    optional_entity_types = _client_optional_entity_types(client_row)
    redaction = redact(question, optional_entity_types=optional_entity_types)
    redacted_question = redaction.redacted_text

    chat = (
        db.query(Chat)
        .options(joinedload(Chat.messages))
        .filter(Chat.session_id == session_id, Chat.client_id == client_id)
        .first()
    )

    effective_user_ctx: dict | None = None
    if chat and chat.user_context:
        effective_user_ctx = dict(chat.user_context)
    elif user_context:
        effective_user_ctx = dict(user_context)

    if not chat:
        uc: dict | None = None
        if effective_user_ctx:
            uc = dict(effective_user_ctx)
        if browser_locale:
            uc = dict(uc or {})
            uc.setdefault("browser_locale", browser_locale)
        chat = Chat(
            client_id=client_id,
            session_id=session_id,
            user_context=uc,
        )
        db.add(chat)
        db.commit()
        db.refresh(chat)
    elif browser_locale and not (chat.user_context or {}).get("browser_locale"):
        ctx = dict(chat.user_context or {})
        ctx["browser_locale"] = browser_locale
        chat.user_context = ctx
        db.add(chat)
        db.commit()
        db.refresh(chat)

    if effective_user_ctx is None and chat.user_context:
        effective_user_ctx = dict(chat.user_context)

    user_context_line = _user_context_prompt_line(effective_user_ctx)

    disclosure_cfg: dict[str, Any] | None = None
    if client_row and isinstance(client_row.disclosure_config, dict):
        disclosure_cfg = client_row.disclosure_config

    msgs = build_chat_messages_for_openai(chat, redacted_question)

    # --- Chat closed ---
    if chat.ended_at is not None:
        out = complete_escalation_openai_turn(
            phase=EscalationPhase.chat_already_closed,
            chat_messages=msgs,
            fact_json={},
            latest_user_text=redacted_question,
            api_key=api_key,
        )
        _persist_turn(
            db,
            chat,
            client_id,
            question,
            out.message_to_user,
            [],
            out.tokens_used,
            optional_entity_types=optional_entity_types,
        )
        return (out.message_to_user, [], out.tokens_used, True)

    # --- Awaiting contact email ---
    if chat.escalation_awaiting_ticket_id:
        ticket = db.get(EscalationTicket, chat.escalation_awaiting_ticket_id)
        if not ticket:
            chat.escalation_awaiting_ticket_id = None
            db.add(chat)
            db.commit()
        else:
            # Parse contact email from original user text, not redacted text.
            # Redaction replaces addresses with placeholders and would break capture.
            email = parse_contact_email(question)
            if email:
                apply_collected_contact_email(ticket.id, chat.id, email, db)
                db.refresh(ticket)
                db.refresh(chat)
                db.expire(chat, ["messages"])
                msgs = build_chat_messages_for_openai(chat, redacted_question)
                out = complete_escalation_openai_turn(
                    phase=EscalationPhase.handoff_email_known,
                    chat_messages=msgs,
                    fact_json=fact_from_ticket(ticket, chat=chat),
                    latest_user_text=redacted_question,
                    api_key=api_key,
                )
                chat.escalation_followup_pending = True
                db.add(chat)
                db.commit()
                _persist_turn(
                    db,
                    chat,
                    client_id,
                    question,
                    out.message_to_user,
                    [],
                    out.tokens_used,
                    optional_entity_types=optional_entity_types,
                )
                return (out.message_to_user, [], out.tokens_used, False)
            out = complete_escalation_openai_turn(
                phase=EscalationPhase.email_parse_failed,
                chat_messages=msgs,
                fact_json=fact_from_ticket(ticket, chat=chat),
                latest_user_text=redacted_question,
                api_key=api_key,
            )
            _persist_turn(
                db,
                chat,
                client_id,
                question,
                out.message_to_user,
                [],
                out.tokens_used,
                optional_entity_types=optional_entity_types,
            )
            return (out.message_to_user, [], out.tokens_used, False)

    # --- Follow-up yes/no ---
    if chat.escalation_followup_pending:
        ticket = get_latest_escalation_ticket_for_chat(chat.id, db)
        out = complete_escalation_openai_turn(
            phase=EscalationPhase.followup_awaiting_yes_no,
            chat_messages=msgs,
            fact_json={
                **fact_from_ticket(ticket, chat=chat),
                "clarify_round": 1 if _escalation_clarify_already_asked(chat) else 0,
            },
            latest_user_text=redacted_question,
            api_key=api_key,
        )
        decision = out.followup_decision or "unclear"
        if decision == "unclear" and _escalation_clarify_already_asked(chat):
            decision = "yes"
        if decision == "yes":
            chat.escalation_followup_pending = False
            _clear_escalation_clarify_flag(chat)
            db.add(chat)
            db.commit()
            _persist_turn(
                db,
                chat,
                client_id,
                question,
                out.message_to_user,
                [],
                out.tokens_used,
                optional_entity_types=optional_entity_types,
            )
            return (out.message_to_user, [], out.tokens_used, False)
        if decision == "no":
            chat.escalation_followup_pending = False
            _clear_escalation_clarify_flag(chat)
            chat.ended_at = datetime.now(timezone.utc)
            db.add(chat)
            db.commit()
            _persist_turn(
                db,
                chat,
                client_id,
                question,
                out.message_to_user,
                [],
                out.tokens_used,
                optional_entity_types=optional_entity_types,
            )
            return (out.message_to_user, [], out.tokens_used, True)
        _set_escalation_clarify_flag(chat)
        db.add(chat)
        db.commit()
        _persist_turn(
            db,
            chat,
            client_id,
            question,
            out.message_to_user,
            [],
            out.tokens_used,
            optional_entity_types=optional_entity_types,
        )
        return (out.message_to_user, [], out.tokens_used, False)

    # --- T-3: explicit human request (before RAG) ---
    if detect_human_request(redacted_question):
        try:
            ticket = create_escalation_ticket(
                client_id,
                question,
                EscalationTrigger.user_request,
                db,
                chat_id=chat.id,
                session_id=session_id,
                user_context=effective_user_ctx,
                optional_entity_types=optional_entity_types,
            )
            phase = (
                EscalationPhase.handoff_ask_email
                if not ticket.user_email
                else EscalationPhase.handoff_email_known
            )
            out = complete_escalation_openai_turn(
                phase=phase,
                chat_messages=msgs,
                fact_json=fact_from_ticket(ticket, chat=chat),
                latest_user_text=redacted_question,
                api_key=api_key,
            )
            if not ticket.user_email:
                chat.escalation_awaiting_ticket_id = ticket.id
            else:
                chat.escalation_followup_pending = True
            db.add(chat)
            db.commit()
            _persist_turn(
                db,
                chat,
                client_id,
                question,
                out.message_to_user,
                [],
                out.tokens_used,
                optional_entity_types=optional_entity_types,
            )
            return (out.message_to_user, [], out.tokens_used, False)
        except Exception as e:  # noqa: BLE001
            logger.warning("Escalation T-3 failed, falling back to RAG: %s", e)

    # --- Normal RAG ---
    retrieval = retrieve_context(client_id, redacted_question, db, api_key, top_k=5)
    chunk_texts = retrieval.chunk_texts
    scores = retrieval.scores
    document_ids = list(dict.fromkeys(retrieval.document_ids))

    answer, tokens_used = generate_answer(
        redacted_question,
        chunk_texts,
        api_key=api_key,
        user_context_line=user_context_line,
        disclosure_config=disclosure_cfg,
    )

    validation = validate_answer(
        redacted_question, answer, chunk_texts, api_key=api_key
    )
    if (
        not validation["is_valid"]
        and validation["confidence"] < LOW_CONFIDENCE_THRESHOLD
    ):
        answer = FALLBACK_LOW_CONFIDENCE_ANSWER

    escalate, esc_trigger = should_escalate(
        retrieval.best_confidence_score,
        len(chunk_texts),
        validation=validation,
    )
    if escalate and esc_trigger is not None:
        try:
            preview = chunks_preview_from_results(document_ids, scores, chunk_texts)
            ticket = create_escalation_ticket(
                client_id,
                question,
                esc_trigger,
                db,
                chat_id=chat.id,
                session_id=session_id,
                best_similarity_score=retrieval.best_confidence_score,
                retrieved_chunks=preview,
                user_context=effective_user_ctx,
                optional_entity_types=optional_entity_types,
            )
            esc_phase = (
                EscalationPhase.handoff_ask_email
                if not ticket.user_email
                else EscalationPhase.handoff_email_known
            )
            esc = complete_escalation_openai_turn(
                phase=esc_phase,
                chat_messages=msgs,
                fact_json=fact_from_ticket(ticket, chat=chat),
                latest_user_text=redacted_question,
                api_key=api_key,
            )
            answer = answer + "\n\n" + esc.message_to_user
            tokens_used = tokens_used + esc.tokens_used
            if not ticket.user_email:
                chat.escalation_awaiting_ticket_id = ticket.id
            else:
                chat.escalation_followup_pending = True
            db.add(chat)
            db.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("Escalation T-1/T-2 failed, returning RAG answer only: %s", e)

    _persist_turn(
        db,
        chat,
        client_id,
        question,
        answer,
        document_ids,
        tokens_used,
        optional_entity_types=optional_entity_types,
    )
    return (answer, document_ids, tokens_used, bool(chat.ended_at))


def run_debug(
    client_id: uuid.UUID,
    question: str,
    db: Session,
    *,
    api_key: str,
) -> tuple[str, int, dict]:
    """
    Run RAG pipeline for debug: retrieval + answer, no DB persistence.

    Returns:
        Tuple of (answer, tokens_used, debug_dict).
        debug_dict: {"mode": str, "chunks": [{"document_id": str, "score": float, "preview": str}]}
    """
    client_row = db.query(Client).filter(Client.id == client_id).first()
    optional_entity_types = _client_optional_entity_types(client_row)
    redacted_question = redact(
        question,
        optional_entity_types=optional_entity_types,
    ).redacted_text
    retrieval = retrieve_context(client_id, redacted_question, db, api_key, top_k=5)
    chunk_texts = retrieval.chunk_texts
    document_ids = retrieval.document_ids
    scores = retrieval.scores
    mode = retrieval.mode
    disclosure_cfg: dict[str, Any] | None = None
    if client_row and isinstance(client_row.disclosure_config, dict):
        disclosure_cfg = client_row.disclosure_config
    answer, tokens_used = generate_answer(
        redacted_question,
        chunk_texts,
        api_key=api_key,
        disclosure_config=disclosure_cfg,
    )

    chunks_debug = [
        {
            "document_id": str(doc_id),
            "score": score,
            "preview": (text[:200] + "..." if len(text) > 200 else text),
        }
        for doc_id, score, text in zip(document_ids, scores, chunk_texts)
    ]

    debug = {
        "mode": mode,
        "best_rank_score": retrieval.best_rank_score,
        "best_confidence_score": retrieval.best_confidence_score,
        "confidence_source": retrieval.confidence_source,
        "chunks": chunks_debug,
        "validation": validate_answer(
            redacted_question, answer, chunk_texts, api_key=api_key
        ),
    }
    return (answer, tokens_used, debug)


def get_chat_history(
    session_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
) -> list[Message]:
    """
    Get all messages for a chat session (ownership enforced).

    Args:
        session_id: Chat session ID.
        client_id: Client ID for ownership check.
        db: Database session.

    Returns:
        List of Message objects, or empty list if not found/not owner.
    """
    chat = db.query(Chat).filter(
        Chat.session_id == session_id,
        Chat.client_id == client_id,
    ).first()
    if not chat:
        return []

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return list(messages)


@dataclass
class SessionSummary:
    """Summary of a chat session for inbox list."""

    session_id: uuid.UUID
    message_count: int
    last_question: Optional[str]
    last_answer_preview: Optional[str]
    last_activity: datetime


def list_chat_sessions(client_id: uuid.UUID, db: Session) -> list[SessionSummary]:
    """
    List all chat sessions for a client, sorted by last_activity DESC.

    Args:
        client_id: Client ID for tenant isolation.
        db: Database session.

    Returns:
        List of SessionSummary, sorted by last_activity descending.
    """
    # N+1 fix: joinedload eager-loads messages in one query instead of N queries per chat
    chats = (
        db.query(Chat)
        .filter(Chat.client_id == client_id)
        .options(joinedload(Chat.messages))
        .all()
    )
    result: list[SessionSummary] = []
    for chat in chats:
        messages = sorted(chat.messages, key=lambda m: m.created_at or datetime.min)
        msg_count = len(messages)
        last_activity = datetime.min
        last_question: str | None = None
        last_answer_preview: str | None = None

        for m in messages:
            if m.created_at and m.created_at > last_activity:
                last_activity = m.created_at
            if m.role == MessageRole.user:
                last_question = _display_message_content(m, include_original=False)
            elif m.role == MessageRole.assistant:
                preview = _display_message_content(m, include_original=False)
                if len(preview) > PREVIEW_MAX_LEN:
                    preview = preview[:PREVIEW_MAX_LEN].rstrip() + "..."
                last_answer_preview = preview

        if msg_count > 0:
            result.append(
                SessionSummary(
                    session_id=chat.session_id,
                    message_count=msg_count,
                    last_question=last_question,
                    last_answer_preview=last_answer_preview,
                    last_activity=last_activity,
                )
            )
        else:
            result.append(
                SessionSummary(
                    session_id=chat.session_id,
                    message_count=0,
                    last_question=None,
                    last_answer_preview=None,
                    last_activity=chat.created_at or datetime.min,
                )
            )

    result.sort(key=lambda s: s.last_activity, reverse=True)
    return result


def get_session_logs(
    session_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
    *,
    include_original: bool = False,
) -> Optional[list[tuple[uuid.UUID, uuid.UUID, str, str, str | None, bool, str, str | None, datetime]]]:
    """
    Get all messages for a session (ownership enforced).

    Args:
        session_id: Chat session ID.
        client_id: Client ID for ownership check.
        db: Database session.

    Returns:
        List of tuples with safe content, optional original content, availability,
        feedback, ideal_answer, created_at or None if not found.
    """
    chat = db.query(Chat).filter(
        Chat.session_id == session_id,
        Chat.client_id == client_id,
    ).first()
    if not chat:
        return None

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [
        (
            m.id,
            chat.session_id,
            m.role.value,
            _display_message_content(m, include_original=False),
            _display_message_content(m, include_original=True) if include_original else None,
            _message_original_available(m),
            (m.feedback or MessageFeedback.none).value,
            m.ideal_answer,
            m.created_at,
        )
        for m in messages
    ]


def delete_session_original_content(
    session_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
) -> tuple[Chat | None, int]:
    chat = db.query(Chat).filter(
        Chat.session_id == session_id,
        Chat.client_id == client_id,
    ).first()
    if not chat:
        return None, 0

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .all()
    )
    deleted_count = 0
    for message in messages:
        if message.content_original_encrypted is None:
            continue
        message.content_original_encrypted = None
        if message.content_redacted:
            message.content = message.content_redacted
        db.add(message)
        deleted_count += 1
    db.commit()
    return chat, deleted_count
