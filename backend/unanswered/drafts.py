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
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.chat.pii import redact_for_egress
from backend.core import db as core_db
from backend.core.config import settings
from backend.core.openai_client import get_async_openai_client
from backend.core.openai_json import async_chat_json
from backend.guards.injection_detector import async_detect_injection
from backend.models import (
    Message,
    Tenant,
    TenantFaq,
    TenantProfile,
    UnansweredGroup,
    UnansweredGroupStatus,
    UnansweredQuestion,
)
from backend.models.base import _utcnow
from backend.unanswered.schemas import DraftResponse, PublishResponse
from backend.unanswered.service import (
    GroupNotFoundError,
    GroupStateConflictError,
    group_query,
    group_response,
)

logger = logging.getLogger(__name__)

UNANSWERED_QUEUE_FAQ_SOURCE = "unanswered_queue"
DEFAULT_LANGUAGE = "en"
QUESTION_SAMPLE_SIZE = 5
GUARD_ATTEMPTS = 2
GENERATION_LOCK_TTL = timedelta(minutes=2)
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
        if not question or not answer:
            raise ValueError(f"{operation}: response is missing question or answer")
    except Exception as exc:
        logger.exception("%s_failed", operation)
        raise DraftGenerationError from exc
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


def _draft_response(group: UnansweredGroup) -> DraftResponse:
    return DraftResponse(
        question=group.draft_question or "",
        answer=group.draft_answer or "",
        language=group.draft_language or DEFAULT_LANGUAGE,
        version=group.draft_version,
    )


async def _lock_group(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID
) -> UnansweredGroup:
    """Row-locks the group (Postgres) and reloads it, so version checks see committed state."""
    group = (
        await db.execute(
            group_query(tenant_id, group_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if group is None:
        raise GroupNotFoundError
    return group


def _require_editable(group: UnansweredGroup) -> None:
    if group.status == UnansweredGroupStatus.answered:
        raise GroupStateConflictError


def _require_version(group: UnansweredGroup, version: int) -> None:
    if group.draft_version != version:
        raise DraftVersionConflictError(
            _draft_response(group) if group.draft_answer is not None else None
        )


async def _api_key(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or not tenant.openai_api_key:
        raise NoOpenAIKeyError
    return tenant.openai_api_key


async def _draft_language(db: AsyncSession, tenant_id: uuid.UUID) -> str:
    """The tenant's configured output language (``escalation_language``) names the draft language."""
    profile = await db.get(TenantProfile, tenant_id)
    language = ((profile.escalation_language if profile else None) or "").strip().lower()
    return language or DEFAULT_LANGUAGE


async def _sample_questions(db: AsyncSession, group_id: uuid.UUID) -> list[str]:
    """Most recent captured questions of the group, redacted for the model."""
    texts = (
        await db.execute(
            select(Message.content)
            .join(UnansweredQuestion, UnansweredQuestion.user_message_id == Message.id)
            .where(UnansweredQuestion.group_id == group_id)
            .order_by(UnansweredQuestion.created_at.desc(), UnansweredQuestion.id)
            .limit(QUESTION_SAMPLE_SIZE)
        )
    ).scalars()
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
    group = (await db.execute(group_query(tenant_id, group_id))).scalar_one_or_none()
    if group is None:
        raise GroupNotFoundError
    if group.draft_answer is None:
        raise NoDraftError
    return _draft_response(group)


async def save_draft(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    content: DraftContent,
    version: int,
) -> DraftResponse:
    """Stores the member's own text; the first save of a group without a draft is version 0."""
    group = await _lock_group(db, tenant_id=tenant_id, group_id=group_id)
    _require_editable(group)
    _require_version(group, version)
    _apply_draft(
        group,
        content,
        language=group.draft_language or await _draft_language(db, tenant_id),
    )
    await db.commit()
    return _draft_response(group)


async def _claim_generation(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID
) -> _GenerationInput:
    group = await _lock_group(db, tenant_id=tenant_id, group_id=group_id)
    _require_editable(group)
    now = _utcnow()
    since = group.draft_generating_since
    if since is not None and now - since < GENERATION_LOCK_TTL:
        raise DraftGeneratingError
    request = _GenerationInput(
        api_key=await _api_key(db, tenant_id),
        questions=await _sample_questions(db, group.id),
        language=await _draft_language(db, tenant_id),
    )
    group.draft_generating_since = now
    await db.commit()
    return request


async def _release_generation(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID
) -> None:
    await db.rollback()
    group = await _lock_group(db, tenant_id=tenant_id, group_id=group_id)
    group.draft_generating_since = None
    await db.commit()


async def generate_draft(*, tenant_id: uuid.UUID, group_id: uuid.UUID) -> DraftResponse:
    """Replaces the group's draft with a freshly generated one.

    Owns its session so that it can run to completion after the client is gone.
    Raises ``DraftGeneratingError`` while another generation is in flight,
    ``NoOpenAIKeyError`` without a tenant key, ``DraftGenerationError`` when the
    model fails and ``DraftRejectedError`` when the injection guard blocks both
    attempts; the stored draft is untouched in the failure cases.
    """
    async with core_db.AsyncSessionLocal() as db:
        request = await _claim_generation(db, tenant_id=tenant_id, group_id=group_id)
        try:
            content = await _guarded(
                lambda: _generate_content(request), tenant_id=tenant_id, api_key=request.api_key
            )
        except Exception:
            await _release_generation(db, tenant_id=tenant_id, group_id=group_id)
            raise
        group = await _lock_group(db, tenant_id=tenant_id, group_id=group_id)
        _apply_draft(group, content, language=request.language)
        await db.commit()
        return _draft_response(group)


async def refine_draft(
    *, tenant_id: uuid.UUID, group_id: uuid.UUID, instructions: str, version: int
) -> DraftResponse:
    """Revises the current draft per ``instructions``; owns its session like ``generate_draft``.

    Raises ``DraftVersionConflictError`` if the draft changed while the model
    was working, so a concurrent edit is never overwritten.
    """
    async with core_db.AsyncSessionLocal() as db:
        group = await _lock_group(db, tenant_id=tenant_id, group_id=group_id)
        _require_editable(group)
        _require_version(group, version)
        if group.draft_answer is None:
            raise NoDraftError
        current = DraftContent(question=group.draft_question or "", answer=group.draft_answer)
        language = group.draft_language or DEFAULT_LANGUAGE
        request = _GenerationInput(
            api_key=await _api_key(db, tenant_id),
            questions=await _sample_questions(db, group.id),
            language=language,
        )
        await db.rollback()

        content = await _guarded(
            lambda: _refine_content(request, current=current, instructions=instructions),
            tenant_id=tenant_id,
            api_key=request.api_key,
        )

        group = await _lock_group(db, tenant_id=tenant_id, group_id=group_id)
        _require_version(group, version)
        _apply_draft(group, content, language=language)
        await db.commit()
        return _draft_response(group)


async def publish_draft(
    db: AsyncSession, *, tenant_id: uuid.UUID, group_id: uuid.UUID, version: int
) -> PublishResponse:
    """Turns the draft into an approved FAQ entry and marks the group answered.

    Idempotent: a group that already has its FAQ returns it again, whatever
    ``version`` the retry carries.
    """
    group = await _lock_group(db, tenant_id=tenant_id, group_id=group_id)
    if group.faq_id is None:
        _require_version(group, version)
        if group.draft_answer is None:
            raise NoDraftError
        faq = TenantFaq(
            tenant_id=tenant_id,
            question=group.draft_question,
            answer=group.draft_answer,
            source=UNANSWERED_QUEUE_FAQ_SOURCE,
            approved=True,
        )
        db.add(faq)
        await db.flush()
        group.faq_id = faq.id
        group.status = UnansweredGroupStatus.answered
        group.dismiss_reason = None
        group.status_changed_at = _utcnow()
        await db.commit()
    return PublishResponse(group=await group_response(db, group), faq_id=group.faq_id)
