"""HTTP routes for Gap Analyzer Phase 5 surfaces."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.auth.middleware import require_verified_user
from backend.clients.service import get_client_by_user
from backend.core.db import get_db
from backend.gap_analyzer.enums import GapRunMode, GapSource
from backend.gap_analyzer.jobs import start_gap_analyzer_job_runner
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator, GapResourceNotFoundError
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.gap_analyzer.schemas import (
    GapActionResponse,
    GapAnalyzerResponse,
    GapDismissRequest,
    GapDraftResponse,
    GapSummaryOnlyResponse,
    ModeASort,
    ModeAStatusFilter,
    ModeBSort,
    ModeBStatusFilter,
    RecalculateCommandResult,
)
from backend.models import User

gap_analyzer_router = APIRouter(tags=["gap-analyzer"])


def _resolve_gap_analyzer_orchestrator(*, db: Session) -> GapAnalyzerOrchestrator:
    return GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db))


def _resolve_gap_analyzer_repository(*, db: Session) -> SqlAlchemyGapAnalyzerRepository:
    return SqlAlchemyGapAnalyzerRepository(db)


def _resolve_client_id(*, db: Session, current_user: User) -> uuid.UUID:
    client = get_client_by_user(current_user.id, db)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return client.id


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


@gap_analyzer_router.post("/{source}/{gap_id}/draft", response_model=GapDraftResponse)
def draft_gap(
    source: GapSource,
    gap_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> GapDraftResponse:
    tenant_id = _resolve_client_id(db=db, current_user=current_user)
    orchestrator = _resolve_gap_analyzer_orchestrator(db=db)
    try:
        return orchestrator.build_draft(tenant_id=tenant_id, source=source, gap_id=gap_id)
    except GapResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
