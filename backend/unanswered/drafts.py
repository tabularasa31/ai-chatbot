"""FAQ drafts for the Knowledge "Unanswered" queue: generate, edit, refine, publish.

A draft lives on the group and carries a version; every write names the version
it was based on, so a stale tab gets a conflict instead of overwriting newer
text. Generation and refinement call the tenant's own OpenAI key and never
publish; only ``publish_draft`` writes to ``tenant_faq``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.chat.language import locale_language_of
from backend.chat.pii import redact_for_egress
from backend.core import db as core_db
from backend.core.config import settings
from backend.core.openai_client import get_async_openai_client
from backend.core.openai_json import async_chat_json
from backend.guards.injection_detector import async_detect_injection
from backend.models import (
    Document,
    Message,
    Tenant,
    TenantFaq,
    TenantProfile,
    UnansweredGroup,
    UnansweredGroupStatus,
    UnansweredQuestion,
)
from backend.models.base import _utcnow
from backend.search.embedding import async_embed_query
from backend.unanswered.schemas import DraftResponse, PublishResponse
from backend.unanswered.service import (
    GroupStateConflictError,
    get_group,
    group_response,
    is_generating,
    lock_group,
    set_status,
)

logger = logging.getLogger(__name__)

UNANSWERED_QUEUE_FAQ_SOURCE = "unanswered_queue"
DEFAULT_LANGUAGE = "en"
QUESTION_SAMPLE_SIZE = 5
EXAMPLES_SIZE = 5
GUARD_ATTEMPTS = 2
LLM_TIMEOUT_SECONDS = 30.0

_DRAFT_SYSTEM = (
    "You write FAQ articles for a product support knowledge base.\n"
    "Given example customer questions from real chats that the support bot could not "
    "answer, produce one FAQ entry that answers them.\n"
    "Rules:\n"
    "- Output strictly JSON with keys: question, answer.\n"
    "- question: a single canonical user question phrasing that covers the examples.\n"
    "- answer: the answer body as GitHub-flavored Markdown. Start with a 1-2 sentence "
    "direct answer, then add details, examples, or steps as needed. Do NOT repeat the "
    "question inside the answer.\n"
    "- Write in the requested output language.\n"
    "- Stay factual. If specific facts are not implied by the questions, write generic "
    "guidance that a support admin can fill in. Never invent product specifics."
)

_REFINE_SYSTEM = (
    "You are revising an existing FAQ article based on the admin's guidance.\n"
    "Preserve the article's intent; apply the guidance precisely.\n"
    "Rules:\n"
    "- Output strictly JSON with keys: question, answer.\n"
    "- Keep the same output language as the existing draft unless the guidance asks to switch.\n"
    "- Do not introduce facts that contradict the original or the customer questions."
)


class NoOpenAIKeyError(Exception):
    pass


class DraftGeneratingError(Exception):
    pass


class DraftGenerationError(Exception):
    pass


class DraftRejectedError(Exception):
    pass


class FaqEmbeddingError(Exception):
    pass


class NoDraftError(Exception):
    pass


class DraftVersionConflictError(Exception):
    def __init__(self, current: DraftResponse | None) -> None:
        super().__init__("draft version is stale")
        self.current = current


@dataclass(frozen=True)
class DraftContent:
    question: str
    answer: str


@dataclass(frozen=True)
class _GenerationInput:
    api_key: str
    questions: list[str]
    language: str


def _format_questions(questions: list[str]) -> str:
    return "\n".join(f"- {q}" for q in questions)


async def _chat_draft(
    operation: str, api_key: str, system: str, user_prompt: str, *, temperature: float
) -> DraftContent:
    try:
        parsed = await async_chat_json(
            operation,
            get_async_openai_client(api_key, timeout=LLM_TIMEOUT_SECONDS),
            model=settings.extraction_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
            strict=True,
        )
        question = (parsed.get("question") or "").strip()
        answer = (parsed.get("answer") or "").strip()
    except Exception as exc:
        logger.exception("%s_failed", operation)
        raise DraftGenerationError from exc
    if not question or not answer:
        logger.error("%s_missing_question_or_answer", operation)
        raise DraftGenerationError
    return DraftContent(question=question, answer=answer)


async def _generate_content(request: _GenerationInput) -> DraftContent:
    user_prompt = (
        f"Example customer questions (verbatim from chats):\n{_format_questions(request.questions)}\n\n"
        f"Output language: {request.language}\n"
        "Return JSON with question, answer."
    )
    return await _chat_draft(
        "unanswered_generate_draft", request.api_key, _DRAFT_SYSTEM, user_prompt, temperature=0.2
    )


async def _refine_content(
    request: _GenerationInput, *, current: DraftContent, instructions: str
) -> DraftContent:
    user_prompt = (
        f"Example customer questions:\n{_format_questions(request.questions)}\n\n"
        f"Current draft (output language: {request.language}):\n"
        f"Question: {current.question}\n"
        f"Answer:\n{current.answer}\n\n"
        f"Admin guidance: {instructions}\n\n"
        "Return JSON with question, answer."
    )
    return await _chat_draft(
        "unanswered_refine_draft", request.api_key, _REFINE_SYSTEM, user_prompt, temperature=0.0
    )


async def _passes_injection_guard(
    content: DraftContent, *, tenant_id: uuid.UUID, api_key: str
) -> bool:
    verdict = await async_detect_injection(
        f"{content.question}\n{content.answer}", tenant_id=str(tenant_id), api_key=api_key
    )
    return not verdict.blocked


async def _guarded(
    produce: Callable[[], Awaitable[DraftContent]], *, tenant_id: uuid.UUID, api_key: str
) -> DraftContent:
    for attempt in range(1, GUARD_ATTEMPTS + 1):
        content = await produce()
        if await _passes_injection_guard(content, tenant_id=tenant_id, api_key=api_key):
            return content
        logger.warning(
            "unanswered_draft_injection_detected tenant_id=%s attempt=%s", tenant_id, attempt
        )
    raise DraftRejectedError


async def _draft_response(db: AsyncSession, group: UnansweredGroup) -> DraftResponse:
    """A group still on its first generation has no text yet and reports empty fields."""
    return DraftResponse(
        question=group.draft_question or "",
        answer=group.draft_answer or "",
        language=group.draft_language or await _draft_language(db, group.tenant_id),
        version=group.draft_version,
        generating=is_generating(group),
        examples=await _recent_question_texts(db, group.tenant_id, group.id, EXAMPLES_SIZE),
    )


def _require_editable(group: UnansweredGroup) -> None:
    if group.status == UnansweredGroupStatus.answered:
        raise GroupStateConflictError


async def _require_version(db: AsyncSession, group: UnansweredGroup, version: int) -> None:
    if group.draft_version != version:
        raise DraftVersionConflictError(
            await _draft_response(db, group) if group.draft_answer is not None else None
        )


async def _api_key(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    tenant = await db.get(Tenant, tenant_id)
    if not tenant.openai_api_key:
        raise NoOpenAIKeyError
    return tenant.openai_api_key


async def _draft_language(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    """The language most of the tenant's documents are in, else its ``escalation_language``."""
    dominant_document_language = (
        await db.execute(
            select(Document.language)
            .where(Document.tenant_id == tenant_id, Document.language.is_not(None))
            .group_by(Document.language)
            .order_by(func.count().desc(), Document.language)
            .limit(1)
        )
    ).scalar_one_or_none()
    profile = await db.get(TenantProfile, tenant_id)
    configured = profile.escalation_language if profile else None
    for raw in (dominant_document_language, configured):
        locale = locale_language_of(raw)
        if locale is not None:
            return locale[1]
    return DEFAULT_LANGUAGE


async def _recent_question_texts(
    db: AsyncSession, tenant_id: uuid.UUID, group_id: uuid.UUID, limit: int
) -> list[str]:
    """Distinct texts of the group's ``limit`` most recent captured questions, newest first."""
    texts = (
        await db.execute(
            select(Message.content)
            .join(UnansweredQuestion, UnansweredQuestion.user_message_id == Message.id)
            .where(
                UnansweredQuestion.group_id == group_id, UnansweredQuestion.tenant_id == tenant_id
            )
            .order_by(UnansweredQuestion.created_at.desc(), UnansweredQuestion.id)
            .limit(limit)
        )
    ).scalars()
    return list(dict.fromkeys(texts))


async def _sample_questions(
    db: AsyncSession, tenant_id: uuid.UUID, group_id: uuid.UUID
) -> list[str]:
    """Most recent captured questions of the group, redacted for the model."""
    texts = await _recent_question_texts(db, tenant_id, group_id, QUESTION_SAMPLE_SIZE)
    return list(dict.fromkeys(redact_for_egress(text) for text in texts))


def _apply_draft(group: UnansweredGroup, content: DraftContent, *, language: str) -> None:
    group.draft_question = content.question
    group.draft_answer = content.answer
    group.draft_language = language
    group.draft_version += 1
    group.draft_generating_since = None


async def get_draft(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID
) -> DraftResponse:
    group = await get_group(db, tenant_id=tenant_id, group_id=group_id)
    if group.draft_answer is None and not is_generating(group):
        raise NoDraftError
    return await _draft_response(db, group)


async def save_draft(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    content: DraftContent,
    version: int,
) -> DraftResponse:
    """Stores the member's own text; the first save of a group without a draft is version 0."""
    group = await lock_group(db, tenant_id=tenant_id, group_id=group_id)
    _require_editable(group)
    if is_generating(group):
        raise DraftGeneratingError
    await _require_version(db, group, version)
    _apply_draft(
        group,
        content,
        language=group.draft_language or await _draft_language(db, tenant_id),
    )
    await db.commit()
    return await _draft_response(db, group)


async def _begin_generation(
    db: AsyncSession, group: UnansweredGroup, *, language: str
) -> _GenerationInput:
    """Takes the group's generation marker and commits it; the group must be locked."""
    request = _GenerationInput(
        api_key=await _api_key(db, group.tenant_id),
        questions=await _sample_questions(db, group.tenant_id, group.id),
        language=language,
    )
    group.draft_generating_since = _utcnow()
    await db.commit()
    return request


async def _release_generation(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID
) -> None:
    await db.rollback()
    group = await lock_group(db, tenant_id=tenant_id, group_id=group_id)
    group.draft_generating_since = None
    await db.commit()


async def _finish_generation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    claimed_version: int,
    request: _GenerationInput,
    produce: Callable[[], Awaitable[DraftContent]],
) -> DraftResponse:
    """Stores the produced content unless the group moved on while the model worked.

    The marker is released on every other outcome, cancellation included.
    """
    try:
        content = await _guarded(produce, tenant_id=tenant_id, api_key=request.api_key)
        group = await lock_group(db, tenant_id=tenant_id, group_id=group_id)
        _require_editable(group)
        await _require_version(db, group, claimed_version)
    except BaseException:
        await _release_generation(db, tenant_id=tenant_id, group_id=group_id)
        raise
    _apply_draft(group, content, language=request.language)
    await db.commit()
    return await _draft_response(db, group)


async def generate_draft(*, tenant_id: uuid.UUID, group_id: uuid.UUID) -> DraftResponse:
    """Replaces the group's draft with a freshly generated one.

    Owns its session so that it can run to completion after the client is gone.
    Raises ``DraftGeneratingError`` while another generation or refinement is in
    flight, ``NoOpenAIKeyError`` without a tenant key, ``DraftGenerationError`` when
    the model fails and ``DraftRejectedError`` when the injection guard blocks both
    attempts. If the draft changed or the group was answered meanwhile the result
    is discarded with ``DraftVersionConflictError`` / ``GroupStateConflictError``;
    the stored draft is untouched in all failure cases.
    """
    async with core_db.AsyncSessionLocal() as db:
        group = await lock_group(db, tenant_id=tenant_id, group_id=group_id)
        _require_editable(group)
        if is_generating(group):
            raise DraftGeneratingError
        claimed_version = group.draft_version
        request = await _begin_generation(
            db, group, language=await _draft_language(db, tenant_id)
        )
        return await _finish_generation(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            claimed_version=claimed_version,
            request=request,
            produce=lambda: _generate_content(request),
        )


async def refine_draft(
    *, tenant_id: uuid.UUID, group_id: uuid.UUID, instructions: str, version: int
) -> DraftResponse:
    """Revises the current draft per ``instructions``; owns its session like ``generate_draft``.

    Takes the same marker as generation, so refinements and generations of one group
    never overlap. Raises ``DraftVersionConflictError`` if the draft changed before or
    while the model was working, so a concurrent edit is never overwritten.
    """
    async with core_db.AsyncSessionLocal() as db:
        group = await lock_group(db, tenant_id=tenant_id, group_id=group_id)
        _require_editable(group)
        await _require_version(db, group, version)
        if group.draft_answer is None:
            raise NoDraftError
        if is_generating(group):
            raise DraftGeneratingError
        current = DraftContent(question=group.draft_question, answer=group.draft_answer)
        request = await _begin_generation(db, group, language=group.draft_language)
        return await _finish_generation(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            claimed_version=version,
            request=request,
            produce=lambda: _refine_content(request, current=current, instructions=instructions),
        )


async def _embed_faq_question(question: str, api_key: str) -> list[float]:
    try:
        return await async_embed_query(
            question, api_key=api_key, timeout=LLM_TIMEOUT_SECONDS, max_attempts=1
        )
    except Exception as exc:
        logger.exception("unanswered_publish_embedding_failed")
        raise FaqEmbeddingError from exc


async def publish_draft(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID, version: int
) -> PublishResponse:
    """Turns the draft into an approved FAQ entry and marks the group answered.

    Idempotent: a group that already has its FAQ returns it again, whatever
    ``version`` the retry carries. The question is embedded first, under the group
    lock, because the bot only matches FAQ entries that have an embedding.

    Raises ``NoOpenAIKeyError`` without a tenant key, ``DraftRejectedError`` when the
    injection guard blocks the final text (it may have been edited by hand) and
    ``FaqEmbeddingError`` when embedding fails; in every case nothing is published.
    """
    group = await lock_group(db, tenant_id=tenant_id, group_id=group_id)
    if group.faq_id is None:
        await _require_version(db, group, version)
        if group.draft_answer is None:
            raise NoDraftError
        api_key = await _api_key(db, tenant_id)
        final = DraftContent(question=group.draft_question, answer=group.draft_answer)
        if not await _passes_injection_guard(final, tenant_id=tenant_id, api_key=api_key):
            logger.warning("unanswered_publish_injection_detected tenant_id=%s", tenant_id)
            raise DraftRejectedError
        question_embedding = await _embed_faq_question(final.question, api_key)
        faq = TenantFaq(
            tenant_id=tenant_id,
            question=final.question,
            question_embedding=question_embedding,
            answer=final.answer,
            source=UNANSWERED_QUEUE_FAQ_SOURCE,
            approved=True,
        )
        db.add(faq)
        await db.flush()
        group.faq_id = faq.id
        set_status(group, status=UnansweredGroupStatus.answered, dismiss_reason=None)
        await db.commit()
    return PublishResponse(group=await group_response(db, group), faq_id=group.faq_id)
