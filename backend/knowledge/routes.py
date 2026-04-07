from __future__ import annotations

import logging
import uuid
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth.middleware import require_verified_user
from backend.clients.service import get_client_by_user
from backend.core import db as core_db
from backend.core.db import get_db
from backend.core.openai_client import get_openai_client
from backend.knowledge.schemas import (
    KnowledgeFaqApproveAllResponse,
    KnowledgeFaqApproveResponse,
    KnowledgeFaqItemResponse,
    KnowledgeFaqListResponse,
    KnowledgeFaqRejectResponse,
    KnowledgeFaqUpdateRequest,
    KnowledgeProfilePatchRequest,
    KnowledgeProfileResponse,
)
from backend.models import Client, TenantFaq, TenantProfile, User

knowledge_router = APIRouter(tags=["knowledge"])

EMBEDDING_MODEL = "text-embedding-3-small"
logger = logging.getLogger(__name__)


def _get_or_create_profile(db: Session, client_id: uuid.UUID) -> TenantProfile:
    profile = db.get(TenantProfile, client_id)
    if profile is None:
        profile = TenantProfile(tenant_id=client_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _profile_or_404(db: Session, client_id: uuid.UUID) -> TenantProfile:
    profile = db.get(TenantProfile, client_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Knowledge profile not found")
    return profile


def _faq_or_404(db: Session, *, client_id: uuid.UUID, faq_id: uuid.UUID) -> TenantFaq:
    faq = (
        db.query(TenantFaq)
        .filter(TenantFaq.id == faq_id, TenantFaq.tenant_id == client_id)
        .first()
    )
    if faq is None:
        raise HTTPException(status_code=404, detail="FAQ entry not found")
    return faq


def _resolve_client_for_knowledge(
    *,
    db: Session,
    current_user: User,
    bot_id: Optional[str],
) -> Client:
    if bot_id is None:
        client = get_client_by_user(current_user.id, db)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")
        return client

    client = (
        db.query(Client)
        .filter(Client.public_id == bot_id, Client.user_id == current_user.id)
        .first()
    )
    if not client:
        raise HTTPException(status_code=404, detail="Bot not found")
    return client


def _generate_faq_embedding_background(
    *,
    faq_id: uuid.UUID,
    question: str,
    encrypted_api_key: str,
) -> None:
    db = core_db.SessionLocal()
    try:
        faq = db.get(TenantFaq, faq_id)
        if faq is None:
            return
        openai_client = get_openai_client(encrypted_api_key)
        response = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=question,
        )
        faq.question_embedding = response.data[0].embedding
        db.add(faq)
        db.commit()
    except Exception:
        logger.exception(
            "Failed to generate FAQ embedding in background (faq_id=%s)",
            faq_id,
        )
        db.rollback()
    finally:
        db.close()


@knowledge_router.get("/knowledge/profile", response_model=KnowledgeProfileResponse)
@knowledge_router.get(
    "/api/v1/bots/{bot_id}/knowledge/profile", response_model=KnowledgeProfileResponse
)
def get_knowledge_profile(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    bot_id: Optional[str] = None,
) -> KnowledgeProfileResponse:
    client = _resolve_client_for_knowledge(db=db, current_user=current_user, bot_id=bot_id)
    profile = _get_or_create_profile(db, client.id)
    return KnowledgeProfileResponse(
        product_name=profile.product_name,
        modules=list(profile.modules or []),
        glossary=list(profile.glossary or []),
        support_email=profile.support_email,
        support_urls=list(profile.support_urls or []),
        aliases=list(profile.aliases or []),
        updated_at=profile.updated_at,
        extraction_status=profile.extraction_status,  # type: ignore[arg-type]
    )


@knowledge_router.patch("/knowledge/profile", response_model=KnowledgeProfileResponse)
@knowledge_router.patch(
    "/api/v1/bots/{bot_id}/knowledge/profile", response_model=KnowledgeProfileResponse
)
def patch_knowledge_profile(
    payload: KnowledgeProfilePatchRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    bot_id: Optional[str] = None,
) -> KnowledgeProfileResponse:
    client = _resolve_client_for_knowledge(db=db, current_user=current_user, bot_id=bot_id)
    profile = _profile_or_404(db, client.id)

    if "product_name" in payload.model_fields_set:
        profile.product_name = payload.product_name
    if "modules" in payload.model_fields_set and payload.modules is not None:
        profile.modules = payload.modules
    if "glossary" in payload.model_fields_set and payload.glossary is not None:
        profile.glossary = payload.glossary
    if "support_email" in payload.model_fields_set:
        profile.support_email = payload.support_email
    if "support_urls" in payload.model_fields_set and payload.support_urls is not None:
        profile.support_urls = payload.support_urls

    db.add(profile)
    db.commit()
    db.refresh(profile)
    return KnowledgeProfileResponse(
        product_name=profile.product_name,
        modules=list(profile.modules or []),
        glossary=list(profile.glossary or []),
        support_email=profile.support_email,
        support_urls=list(profile.support_urls or []),
        aliases=list(profile.aliases or []),
        updated_at=profile.updated_at,
        extraction_status=profile.extraction_status,  # type: ignore[arg-type]
    )


@knowledge_router.get("/knowledge/faq", response_model=KnowledgeFaqListResponse)
@knowledge_router.get("/api/v1/bots/{bot_id}/knowledge/faq", response_model=KnowledgeFaqListResponse)
def list_knowledge_faq(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    approved: Literal["true", "false", "all"] = Query("all"),
    source: Literal["docs", "logs", "swagger", "all"] = Query("all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    bot_id: Optional[str] = None,
) -> KnowledgeFaqListResponse:
    client = _resolve_client_for_knowledge(db=db, current_user=current_user, bot_id=bot_id)

    query = db.query(TenantFaq).filter(TenantFaq.tenant_id == client.id)
    if approved == "true":
        query = query.filter(TenantFaq.approved.is_(True))
    elif approved == "false":
        query = query.filter(TenantFaq.approved.is_(False))
    if source != "all":
        query = query.filter(TenantFaq.source == source)

    total = query.count()
    items = (
        query.order_by(TenantFaq.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    pending_count = (
        db.query(func.count(TenantFaq.id))
        .filter(TenantFaq.tenant_id == client.id, TenantFaq.approved.is_(False))
        .scalar()
        or 0
    )

    return KnowledgeFaqListResponse(
        items=[
            KnowledgeFaqItemResponse(
                id=item.id,
                question=item.question,
                answer=item.answer,
                confidence=item.confidence,
                source=item.source,
                approved=bool(item.approved),
                created_at=item.created_at,
            )
            for item in items
        ],
        total=int(total),
        pending_count=int(pending_count),
    )


@knowledge_router.post("/knowledge/faq/{faq_id}/approve", response_model=KnowledgeFaqApproveResponse)
@knowledge_router.post(
    "/api/v1/bots/{bot_id}/knowledge/faq/{faq_id}/approve",
    response_model=KnowledgeFaqApproveResponse,
)
def approve_faq(
    faq_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    bot_id: Optional[str] = None,
) -> KnowledgeFaqApproveResponse:
    client = _resolve_client_for_knowledge(db=db, current_user=current_user, bot_id=bot_id)
    faq = _faq_or_404(db, client_id=client.id, faq_id=faq_id)
    faq.approved = True
    db.add(faq)
    db.commit()

    if faq.question_embedding is None and client.openai_api_key:
        background_tasks.add_task(
            _generate_faq_embedding_background,
            faq_id=faq.id,
            question=faq.question,
            encrypted_api_key=client.openai_api_key,
        )

    return KnowledgeFaqApproveResponse(id=faq.id, approved=True)


@knowledge_router.post("/knowledge/faq/{faq_id}/reject", response_model=KnowledgeFaqRejectResponse)
@knowledge_router.post(
    "/api/v1/bots/{bot_id}/knowledge/faq/{faq_id}/reject",
    response_model=KnowledgeFaqRejectResponse,
)
def reject_faq(
    faq_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    bot_id: Optional[str] = None,
) -> KnowledgeFaqRejectResponse:
    client = _resolve_client_for_knowledge(db=db, current_user=current_user, bot_id=bot_id)
    faq = _faq_or_404(db, client_id=client.id, faq_id=faq_id)
    db.delete(faq)
    db.commit()
    return KnowledgeFaqRejectResponse(id=faq_id, deleted=True)


@knowledge_router.post("/knowledge/faq/approve-all", response_model=KnowledgeFaqApproveAllResponse)
@knowledge_router.post(
    "/api/v1/bots/{bot_id}/knowledge/faq/approve-all",
    response_model=KnowledgeFaqApproveAllResponse,
)
def approve_all_faq(
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    bot_id: Optional[str] = None,
) -> KnowledgeFaqApproveAllResponse:
    client = _resolve_client_for_knowledge(db=db, current_user=current_user, bot_id=bot_id)
    missing_embedding = (
        db.query(TenantFaq.id, TenantFaq.question)
        .filter(
            TenantFaq.tenant_id == client.id,
            TenantFaq.approved.is_(False),
            TenantFaq.question_embedding.is_(None),
        )
        .all()
    )
    updated = (
        db.query(TenantFaq)
        .filter(TenantFaq.tenant_id == client.id, TenantFaq.approved.is_(False))
        .update({TenantFaq.approved: True}, synchronize_session=False)
    )
    db.commit()
    if client.openai_api_key:
        for faq_id, question in missing_embedding:
            background_tasks.add_task(
                _generate_faq_embedding_background,
                faq_id=faq_id,
                question=question,
                encrypted_api_key=client.openai_api_key,
            )
    return KnowledgeFaqApproveAllResponse(approved_count=int(updated))


@knowledge_router.put("/knowledge/faq/{faq_id}", response_model=KnowledgeFaqItemResponse)
@knowledge_router.put(
    "/api/v1/bots/{bot_id}/knowledge/faq/{faq_id}",
    response_model=KnowledgeFaqItemResponse,
)
def update_faq(
    faq_id: uuid.UUID,
    payload: KnowledgeFaqUpdateRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    bot_id: Optional[str] = None,
) -> KnowledgeFaqItemResponse:
    client = _resolve_client_for_knowledge(db=db, current_user=current_user, bot_id=bot_id)
    faq = _faq_or_404(db, client_id=client.id, faq_id=faq_id)

    question_changed = payload.question.strip() != faq.question.strip()
    faq.question = payload.question.strip()
    faq.answer = payload.answer.strip()
    if question_changed:
        faq.approved = False
    if question_changed:
        faq.question_embedding = None
    db.add(faq)
    db.commit()
    db.refresh(faq)

    if question_changed and client.openai_api_key:
        background_tasks.add_task(
            _generate_faq_embedding_background,
            faq_id=faq.id,
            question=faq.question,
            encrypted_api_key=client.openai_api_key,
        )

    return KnowledgeFaqItemResponse(
        id=faq.id,
        question=faq.question,
        answer=faq.answer,
        confidence=faq.confidence,
        source=faq.source,
        approved=bool(faq.approved),
        created_at=faq.created_at,
    )


@knowledge_router.delete("/knowledge/faq/{faq_id}", response_model=KnowledgeFaqRejectResponse)
@knowledge_router.delete(
    "/api/v1/bots/{bot_id}/knowledge/faq/{faq_id}",
    response_model=KnowledgeFaqRejectResponse,
)
def delete_faq(
    faq_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    bot_id: Optional[str] = None,
) -> KnowledgeFaqRejectResponse:
    client = _resolve_client_for_knowledge(db=db, current_user=current_user, bot_id=bot_id)
    faq = _faq_or_404(db, client_id=client.id, faq_id=faq_id)
    db.delete(faq)
    db.commit()
    return KnowledgeFaqRejectResponse(id=faq_id, deleted=True)
