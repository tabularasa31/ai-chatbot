import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.core.limiter import limiter
from backend.eval.deps import get_current_tester
from backend.eval.schemas import (
    EvalLoginRequest,
    EvalResultCreateRequest,
    EvalResultCreateResponse,
    EvalResultItemResponse,
    EvalResultListResponse,
    EvalSessionCreateRequest,
    EvalSessionResponse,
    EvalTokenResponse,
)
from backend.eval.service import (
    authenticate_tester,
    create_eval_result,
    create_eval_session,
    get_session_for_tester,
    list_session_results,
)
from backend.eval.tokens import EvalJwtSecretMissingError, create_eval_access_token
from backend.models import Tester

eval_router = APIRouter(prefix="/eval", tags=["eval"])


@eval_router.post("/login", response_model=EvalTokenResponse)
@limiter.limit("10/minute")
def eval_login(
    request: Request,
    body: EvalLoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> EvalTokenResponse:
    tester = authenticate_tester(body.username, body.password, db)
    if not tester:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    try:
        token = create_eval_access_token(tester.id)
    except EvalJwtSecretMissingError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Eval authentication is not configured (set EVAL_JWT_SECRET)",
        ) from None
    return EvalTokenResponse(access_token=token)


@eval_router.post(
    "/sessions",
    response_model=EvalSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def eval_create_session(
    body: EvalSessionCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    tester: Annotated[Tester, Depends(get_current_tester)],
) -> EvalSessionResponse:
    try:
        session = create_eval_session(tester.id, body.tenant_id, db)
    except ValueError as e:
        code = str(e)
        if code == "bot_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bot not found",
            ) from e
        if code == "bot_inactive":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant is not active",
            ) from e
        if code == "bot_openai_not_configured":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OpenAI API key not configured. Add your key in dashboard settings.",
            ) from e
        raise
    return EvalSessionResponse(
        id=session.id,
        tester_id=session.tester_id,
        tenant_id=session.tenant_id,
        started_at=session.started_at,
    )


@eval_router.post(
    "/sessions/{session_id}/results",
    response_model=EvalResultCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def eval_create_result(
    session_id: uuid.UUID,
    body: EvalResultCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    tester: Annotated[Tester, Depends(get_current_tester)],
) -> EvalResultCreateResponse:
    session = get_session_for_tester(session_id, tester.id, db)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    result = create_eval_result(session, body, db)
    return EvalResultCreateResponse(
        id=result.id,
        session_id=result.session_id,
        created_at=result.created_at,
    )


@eval_router.get("/sessions/{session_id}/results", response_model=EvalResultListResponse)
def eval_list_results(
    session_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    tester: Annotated[Tester, Depends(get_current_tester)],
) -> EvalResultListResponse:
    session = get_session_for_tester(session_id, tester.id, db)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    rows = list_session_results(session_id, db)
    return EvalResultListResponse(
        items=[
            EvalResultItemResponse(
                id=r.id,
                session_id=r.session_id,
                question=r.question,
                bot_answer=r.bot_answer,
                verdict=r.verdict,
                error_category=r.error_category,
                comment=r.comment,
                created_at=r.created_at,
            )
            for r in rows
        ],
    )
