"""FastAPI auth endpoints."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.auth.middleware import require_verified_user
from backend.auth.schemas import (
    AuthResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    UserResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
)
from backend.auth.service import (
    authenticate_user,
    create_reset_token,
    create_token_for_user,
    register_user,
    reset_password,
)
from backend.core.config import settings
from backend.core.db import get_db
from backend.core.limiter import limiter
from chat9 import generateToken
from backend.email.service import send_email
from backend.models import User
from backend.tenants.service import ensure_tenant_for_user, get_kyc_decrypted_keys_for_validation, get_tenant_by_user

auth_router = APIRouter(tags=["auth"])


@auth_router.post("/register", response_model=RegisterResponse)
@limiter.limit("5/hour")
def register(
    request: Request,
    body: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
) -> RegisterResponse:
    """
    Register a new user.

    Returns user info only — no JWT token until email is verified.
    Sends verification email. Errors: 400 (invalid input), 409 (email exists), 500 (db error).
    """
    user = register_user(body.email, body.password, db)

    # Generate verification token and send email
    token = uuid.uuid4().hex
    user.verification_token = token
    user.verification_expires_at = datetime.now(UTC) + timedelta(days=2)
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

    return RegisterResponse(
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
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Email not verified. Please check your inbox.")
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


@auth_router.post("/verify-email", response_model=VerifyEmailResponse)
def verify_email(
    body: VerifyEmailRequest,
    db: Annotated[Session, Depends(get_db)],
) -> VerifyEmailResponse:
    """Verify user's email using a one-time token. Returns JWT on success."""
    now = datetime.now(UTC)
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
    ensure_tenant_for_user(user.id, db)
    db.commit()

    jwt_token, expires_in = create_token_for_user(user)
    return VerifyEmailResponse(
        token=jwt_token,
        expires_in=expires_in,
        user=UserResponse(
            id=user.id,
            email=user.email,
            created_at=user.created_at,
        ),
    )


@auth_router.post("/forgot-password", response_model=ForgotPasswordResponse)
@limiter.limit("3/hour")
def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ForgotPasswordResponse:
    """
    Request password reset email.

    Always returns same message (security: don't reveal if email exists).
    Rate limited: 3/hour to prevent email spam.
    """
    token = create_reset_token(body.email, db)

    if token:
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
        subject = "Reset your Chat9 password"
        body_text = (
            "Hi,\n\n"
            "You requested a password reset. Click the link below:\n\n"
            f"{reset_url}\n\n"
            "This link expires in 1 hour.\n\n"
            "If you didn't request this, you can safely ignore this email.\n"
        )
        try:
            send_email(to=body.email, subject=subject, body=body_text)
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning("Failed to send reset email: %s", e)

    return ForgotPasswordResponse(
        message="If this email is registered, you'll receive a password reset link shortly."
    )


@auth_router.post("/reset-password", response_model=ResetPasswordResponse)
@limiter.limit("5/hour")
def reset_password_endpoint(
    request: Request,
    body: ResetPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ResetPasswordResponse:
    """
    Reset password using token from email.

    Errors: 400 (invalid/expired token), 422 (password validation).
    """
    success = reset_password(body.token, body.new_password, db)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired reset token. Please request a new one.",
        )
    return ResetPasswordResponse(
        message="Password updated successfully. You can now log in."
    )


@auth_router.get("/me/widget-token")
def get_widget_token(
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Generate a short-lived widget identity token for the authenticated user.

    Returns a signed token that can be passed to the widget session init
    so the bot knows who the user is (identified mode).
    Requires the tenant to have a KYC secret configured.
    """
    tenant = get_tenant_by_user(current_user.id, db)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    keys = get_kyc_decrypted_keys_for_validation(tenant)
    if not keys:
        raise HTTPException(
            status_code=404,
            detail="No identity secret configured. Generate one in Settings → Widget.",
        )

    secret = keys[0][0]
    token = generateToken({
        "secret": secret,
        "user": {"user_id": str(current_user.id), "email": current_user.email},
        "options": {"ttl": 300},
    })
    return {"identity_token": token}


@auth_router.get("/me", response_model=UserResponse)
def get_me(
    current_user: Annotated[User, Depends(require_verified_user)],
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
