"""HTTP routes for Gap Analyzer Phase 5 surfaces."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.auth.middleware import require_verified_user
from backend.core.db import get_db
from backend.gap_analyzer.enums import GapRunMode, GapSource
from backend.gap_analyzer.jobs import start_gap_analyzer_job_runner
from backend.gap_analyzer.orchestrator import (
    DraftGenerationNotAvailableError,
    DraftInjectionGuardError,
    DraftVersionConflictError,
    GapAnalyzerOrchestrator,
    GapResourceNotFoundError,
)
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.gap_analyzer.schemas import (
    DiscardDraftResponse,
    DraftPayload,
    GapActionResponse,
    GapAnalyzerResponse,
    GapDismissRequest,
    GapDraftResponse,
    GapSummaryOnlyResponse,
    ModeASort,
    ModeAStatusFilter,
    ModeBSort,
    ModeBStatusFilter,
    PublishResult,
    RecalculateCommandResult,
    RefineDraftRequest,
    UpdateDraftRequest,
)
from backend.knowledge.routes import _generate_faq_embedding_background
from backend.models import Tenant, User
from backend.tenants.service import get_tenant_by_user

gap_analyzer_router = APIRouter(tags=["gap-analyzer"])


def _resolve_gap_analyzer_orchestrator(*, db: Session) -> GapAnalyzerOrchestrator:
    return GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db))


def _resolve_gap_analyzer_repository(*, db: Session) -> SqlAlchemyGapAnalyzerRepository:
    return SqlAlchemyGapAnalyzerRepository(db)


def _resolve_client_id(*, db: Session, current_user: User) -> uuid.UUID:
    tenant = get_tenant_by_user(current_user.id, db)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant.id


def _resolve_tenant(*, db: Session, current_user: User) -> Tenant:
    tenant = get_tenant_by_user(current_user.id, db)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@gap_analyzer_router.get("", response_model=GapAnalyzerResponse)
def get_gap_analyzer(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    mode_a_status: ModeAStatusFilter = Query("active"),
    mode_b_status: ModeBStatusFilter = Query("active"),
    mode_a_sort: ModeASort = Query("coverage_asc"),
    mode_b_sort: ModeBSort = Query("signal_desc"),
) -> GapAnalyzerResponse:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    return orchestrator.list_gaps(
        tenant_id=tenant_id,
        mode_a_status=mode_a_status,
        mode_b_status=mode_b_status,
        mode_a_sort=mode_a_sort,
        mode_b_sort=mode_b_sort,
    )


@gap_analyzer_router.get("/summary", response_model=GapSummaryOnlyResponse)
def get_gap_analyzer_summary(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GapSummaryOnlyResponse:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    repository = _resolve_gap_analyzer_repository(db=db)
    return GapSummaryOnlyResponse(summary=repository.get_gap_summary(tenant_id=tenant_id))


@gap_analyzer_router.post("/recalculate", response_model=RecalculateCommandResult, status_code=202)
async def recalculate_gap_analyzer(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
    mode: GapRunMode = Query(...),
) -> RecalculateCommandResult:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    response = await orchestrator.request_recalculation(tenant_id=tenant_id, mode=mode)
    db.commit()
    start_gap_analyzer_job_runner()
    return response


@gap_analyzer_router.post("/{source}/{gap_id}/dismiss", response_model=GapActionResponse)
def dismiss_gap(
    source: GapSource,
    gap_id: uuid.UUID,
    payload: GapDismissRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GapActionResponse:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    try:
        response = orchestrator.dismiss_gap(
            tenant_id=tenant_id,
            source=source,
            gap_id=gap_id,
            dismissed_by=current_user.id,
            reason=payload.reason,
        )
    except GapResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    db.commit()
    return response


@gap_analyzer_router.post("/{source}/{gap_id}/reactivate", response_model=GapActionResponse)
def reactivate_gap(
    source: GapSource,
    gap_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GapActionResponse:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    try:
        response = orchestrator.reactivate_gap(
            tenant_id=tenant_id,
            source=source,
            gap_id=gap_id,
        )
    except GapResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    db.commit()
    return response


@gap_analyzer_router.post("/mode_a/{gap_id}/draft", response_model=GapDraftResponse)
def draft_mode_a_gap(
    gap_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GapDraftResponse:
    """Template-driven transient draft for Mode A docs-side gaps.

    Mode B uses the LLM workflow under ``/gap-analyzer/mode_b/{gap_id}/*``.
    """
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    try:
        return orchestrator.build_draft(tenant_id=tenant_id, source=GapSource.mode_a, gap_id=gap_id)
    except GapResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


def _handle_draft_errors(call):
    try:
        return call()
    except GapResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except DraftGenerationNotAvailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except DraftVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except DraftInjectionGuardError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@gap_analyzer_router.post("/mode_b/{gap_id}/draft", response_model=DraftPayload)
def generate_mode_b_draft(
    gap_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DraftPayload:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    payload = _handle_draft_errors(
        lambda: orchestrator.start_draft_generation(tenant_id=tenant_id, gap_id=gap_id)
    )
    db.commit()
    return payload


@gap_analyzer_router.post("/mode_b/{gap_id}/draft/refine", response_model=DraftPayload)
def refine_mode_b_draft(
    gap_id: uuid.UUID,
    payload: RefineDraftRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DraftPayload:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    result = _handle_draft_errors(
        lambda: orchestrator.refine_draft(
            tenant_id=tenant_id, gap_id=gap_id, guidance=payload.guidance
        )
    )
    db.commit()
    return result


@gap_analyzer_router.get("/mode_b/{gap_id}/draft", response_model=DraftPayload)
def get_mode_b_draft(
    gap_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DraftPayload:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    return _handle_draft_errors(
        lambda: orchestrator.get_mode_b_draft(tenant_id=tenant_id, gap_id=gap_id)
    )


@gap_analyzer_router.patch("/mode_b/{gap_id}/draft", response_model=DraftPayload)
def update_mode_b_draft(
    gap_id: uuid.UUID,
    payload: UpdateDraftRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DraftPayload:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    result = _handle_draft_errors(
        lambda: orchestrator.update_draft(
            tenant_id=tenant_id,
            gap_id=gap_id,
            title=payload.title,
            question=payload.question,
            markdown=payload.markdown,
            if_match=payload.if_match,
        )
    )
    db.commit()
    return result


@gap_analyzer_router.delete("/mode_b/{gap_id}/draft", response_model=DiscardDraftResponse)
def discard_mode_b_draft(
    gap_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DiscardDraftResponse:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    result = _handle_draft_errors(
        lambda: orchestrator.discard_draft(tenant_id=tenant_id, gap_id=gap_id)
    )
    db.commit()
    return result


@gap_analyzer_router.post("/mode_b/{gap_id}/publish", response_model=PublishResult)
def publish_mode_b_draft(
    gap_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PublishResult:
    """Promote the persisted draft into ``tenant_faq``. Requires explicit admin click.

    This is the ONLY endpoint that writes to the knowledge base — generate /
    refine / save endpoints never touch ``tenant_faq``.
    """
    tenant = _resolve_tenant(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    result = _handle_draft_errors(
        lambda: orchestrator.publish_draft(tenant_id=tenant.id, gap_id=gap_id)
    )
    db.commit()

    if tenant.openai_api_key:
        from backend.models import TenantFaq

        faq = db.get(TenantFaq, result.faq_id)
        if faq is not None and faq.question_embedding is None:
            background_tasks.add_task(
                _generate_faq_embedding_background,
                faq_id=faq.id,
                question=faq.question,
                encrypted_api_key=tenant.openai_api_key,
            )
    return result


@gap_analyzer_router.post("/mode_b/{gap_id}/resolve", response_model=GapActionResponse)
def resolve_mode_b_gap(
    gap_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GapActionResponse:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    result = _handle_draft_errors(
        lambda: orchestrator.mark_resolved(tenant_id=tenant_id, gap_id=gap_id)
    )
    db.commit()
    return result
