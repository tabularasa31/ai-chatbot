"""FastAPI auth endpoints."""

from datetime import datetime, timedelta
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.db import get_db
from backend.core.limiter import limiter
from backend.auth.schemas import AuthResponse, LoginRequest, RegisterRequest, UserResponse, VerifyEmailRequest
from backend.auth.service import (
    authenticate_user,
    create_token_for_user,
    register_user,
)
from backend.auth.middleware import get_current_user
from backend.email.service import send_email
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
    Sends verification email. Errors: 400 (invalid input), 409 (email exists), 500 (db error).
    """
    user = register_user(body.email, body.password, db)

    # Generate verification token and send email
    token = uuid.uuid4().hex
    user.verification_token = token
    user.verification_expires_at = datetime.utcnow() + timedelta(days=2)
    user.is_verified = False
    db.commit()
    db.refresh(user)

    verify_url = f"{settings.FRONTEND_URL}/verify?token={token}"
    subject = "Verify your email"
    body_text = (
        "Hi,\n\n"
        "Please verify your email by clicking the link below:\n\n"
        f"{verify_url}\n\n"
        "If you did not sign up, you can ignore this email.\n"
    )
    try:
        send_email(to=user.email, subject=subject, body=body_text)
    except Exception as e:
        # Do not block signup if email fails in dev
        import logging
        logging.getLogger(__name__).warning("Failed to send verification email: %s", e)

    jwt_token, expires_in = create_token_for_user(user)
    return AuthResponse(
        token=jwt_token,
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


@auth_router.post("/verify-email")
def verify_email(
    body: VerifyEmailRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Verify user's email using a one-time token."""
    now = datetime.utcnow()
    user = (
        db.query(User)
        .filter(
            User.verification_token == body.token,
            User.verification_expires_at >= now,
        )
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )

    user.is_verified = True
    user.verification_token = None
    user.verification_expires_at = None
    db.commit()

    return {"status": "ok"}


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
