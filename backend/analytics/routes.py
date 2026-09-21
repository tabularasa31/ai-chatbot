"""HTTP routes for the tenant analytics summary."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics.schemas import AnalyticsSummaryResponse
from backend.analytics.service import PERIOD_DAYS, compute_analytics_summary
from backend.auth.middleware import require_member
from backend.core.db import get_async_db
from backend.models import User

analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])


@analytics_router.get("/summary", response_model=AnalyticsSummaryResponse)
async def get_analytics_summary(
    current_user: Annotated[User, Depends(require_member)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
    period: Annotated[str, Query()] = "30d",
) -> AnalyticsSummaryResponse:
    if period not in PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid period '{period}'; expected one of {sorted(PERIOD_DAYS)}",
        )
    if current_user.tenant_id is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return await compute_analytics_summary(
        db, tenant_id=current_user.tenant_id, period=period
    )
