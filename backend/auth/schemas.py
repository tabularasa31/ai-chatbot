"""Pydantic schemas for auth request/response models."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

# Password validation: min 8 chars, 1 uppercase, 1 number, 1 special char
_PASSWORD_PATTERN = re.compile(
    r"^(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]).{8,}$"
)


def _validate_password_strength(v: str) -> str:
    """Validate password: min 8 chars, 1 uppercase, 1 number, 1 special char."""
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not _PASSWORD_PATTERN.match(v):
        raise ValueError(
            "Password must include at least one uppercase letter, "
            "one number, and one special character"
        )
    return v


StrongPassword = Annotated[str, AfterValidator(_validate_password_strength)]


class RegisterRequest(BaseModel):
    """Request body for user registration."""

    email: EmailStr
    password: StrongPassword


class LoginRequest(BaseModel):
    """Request body for user login."""

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """User data in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    created_at: datetime


class AuthResponse(BaseModel):
    """Response with JWT token and user info."""

    token: str
    expires_in: int
    user: UserResponse


class RegisterResponse(BaseModel):
    """Response for registration (no token — issued only after email verification)."""

    user: UserResponse


class VerifyEmailRequest(BaseModel):
    """Request body for email verification."""

    token: str


class VerifyEmailResponse(AuthResponse):
    """Response for email verification — includes JWT token."""


class ForgotPasswordRequest(BaseModel):
    """Request body for forgot password."""

    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Response for forgot password (always generic for security)."""

    message: str


class ResetPasswordRequest(BaseModel):
    """Request body for password reset."""

    token: str
    new_password: StrongPassword = Field(..., min_length=8, max_length=128)


class ResetPasswordResponse(BaseModel):
    """Response for password reset."""

    message: str
