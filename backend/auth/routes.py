"""FastAPI auth endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.core.limiter import limiter
from backend.auth.schemas import AuthResponse, LoginRequest, RegisterRequest, UserResponse
from backend.auth.service import (
    authenticate_user,
    create_token_for_user,
    register_user,
)
from backend.auth.middleware import get_current_user
from backend.models import User

auth_router = APIRouter(tags=["auth"])


@auth_router.post("/register", response_model=AuthResponse)
@limiter.limit("5/hour")
def register(
    request: Request,
    body: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
) -> AuthResponse:
    """
    Register a new user.

    Returns JWT token and user info on success.
    Errors: 400 (invalid input), 409 (email exists), 500 (db error).
    """
    user = register_user(body.email, body.password, db)
    token, expires_in = create_token_for_user(user)
    return AuthResponse(
        token=token,
        expires_in=expires_in,
        user=UserResponse(
            id=user.id,
            email=user.email,
            created_at=user.created_at,
        ),
    )


@auth_router.post("/login", response_model=AuthResponse)
@limiter.limit("10/minute")
def login(
    request: Request,
    body: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> AuthResponse:
    """
    Login with email and password.

    Returns JWT token and user info on success.
    Errors: 401 (invalid credentials), 404 (user not found).
    """
    user = authenticate_user(body.email, body.password, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token, expires_in = create_token_for_user(user)
    return AuthResponse(
        token=token,
        expires_in=expires_in,
        user=UserResponse(
            id=user.id,
            email=user.email,
            created_at=user.created_at,
        ),
    )


@auth_router.get("/me", response_model=UserResponse)
def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserResponse:
    """
    Get current user info (protected route).

    Requires valid JWT in Authorization header.
    Errors: 401 (missing/invalid token).
    """
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        created_at=current_user.created_at,
    )
