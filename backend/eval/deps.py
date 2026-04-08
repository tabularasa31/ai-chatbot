from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.eval.tokens import EvalJwtSecretMissingError, decode_eval_access_token
from backend.models import Tester

eval_security = HTTPBearer(auto_error=False)


async def get_current_tester(
    credentials: HTTPAuthorizationCredentials = Depends(eval_security),
    db: Session = Depends(get_db),
) -> Tester:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    try:
        tester_id: uuid.UUID | None = decode_eval_access_token(credentials.credentials)
    except EvalJwtSecretMissingError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Eval authentication is not configured (set EVAL_JWT_SECRET)",
        ) from None
    if tester_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    tester = db.query(Tester).filter(Tester.id == tester_id).first()
    if not tester:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    if not tester.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return tester
