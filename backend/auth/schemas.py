"""Pydantic schemas for auth request/response models."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# Password validation: min 8 chars, 1 uppercase, 1 number, 1 special char
_PASSWORD_PATTERN = re.compile(
    r"^(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]).{8,}$"
)


class RegisterRequest(BaseModel):
    """Request body for user registration."""

    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Validate password: min 8 chars, 1 uppercase, 1 number, 1 special char."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not _PASSWORD_PATTERN.match(v):
            raise ValueError(
                "Password must include at least one uppercase letter, "
                "one number, and one special character"
            )
        return v


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


class VerifyEmailResponse(BaseModel):
    """Response for email verification — includes JWT token."""

    token: str
    expires_in: int
    user: UserResponse


class ForgotPasswordRequest(BaseModel):
    """Request body for forgot password."""

    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Response for forgot password (always generic for security)."""

    message: str


class ResetPasswordRequest(BaseModel):
    """Request body for password reset."""

    token: str
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Validate password: min 8 chars, 1 uppercase, 1 number, 1 special char."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not _PASSWORD_PATTERN.match(v):
            raise ValueError(
                "Password must include at least one uppercase letter, "
                "one number, and one special character"
            )
        return v


class ResetPasswordResponse(BaseModel):
    """Response for password reset."""

    message: str


class ErrorResponse(BaseModel):
    """Error response model."""

    detail: str
    status_code: int
