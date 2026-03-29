from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Глобальные настройки приложения, загружаемые из .env."""

    database_url: str = Field(..., alias="DATABASE_URL")
    environment: str = Field("development", alias="ENVIRONMENT")
    jwt_secret: str = Field(..., alias="JWT_SECRET")
    eval_jwt_secret: str = Field(..., alias="EVAL_JWT_SECRET")
    openai_api_key: Optional[str] = Field(None, alias="OPENAI_API_KEY")
    encryption_key: Optional[str] = Field(None, alias="ENCRYPTION_KEY")
    langfuse_host: Optional[str] = Field(None, alias="LANGFUSE_HOST")
    langfuse_public_key: Optional[str] = Field(None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: Optional[str] = Field(None, alias="LANGFUSE_SECRET_KEY")
    observability_capture_full_prompts: bool = Field(
        False,
        alias="OBSERVABILITY_CAPTURE_FULL_PROMPTS",
    )
    trace_sample_rate: float = Field(1.0, alias="TRACE_SAMPLE_RATE")
    trace_high_volume_threshold: int = Field(1000, alias="TRACE_HIGH_VOLUME_THRESHOLD")
    trace_high_volume_sample_rate: float = Field(0.1, alias="TRACE_HIGH_VOLUME_SAMPLE_RATE")
    trace_new_tenant_threshold: int = Field(100, alias="TRACE_NEW_TENANT_THRESHOLD")
    trace_rate_window_seconds: int = Field(3600, alias="TRACE_RATE_WINDOW_SECONDS")
    # Full capture: record all traces (early-stage / low-traffic). Adaptive: tenant heuristics below (production scale).
    full_capture_mode: bool = Field(True, alias="FULL_CAPTURE_MODE")
    bm25_expansion_mode: Literal["asymmetric", "symmetric_variants"] = Field(
        "asymmetric",
        alias="BM25_EXPANSION_MODE",
    )
    contradiction_adjudication_enabled: bool = Field(
        False,
        alias="CONTRADICTION_ADJUDICATION_ENABLED",
    )
    contradiction_adjudication_model: str = Field(
        "gpt-4o-mini",
        alias="CONTRADICTION_ADJUDICATION_MODEL",
    )
    contradiction_adjudication_max_facts: int = Field(
        5,
        alias="CONTRADICTION_ADJUDICATION_MAX_FACTS",
    )
    contradiction_adjudication_preview_chars: int = Field(
        160,
        alias="CONTRADICTION_ADJUDICATION_PREVIEW_CHARS",
    )
    contradiction_adjudication_max_tokens: int = Field(
        500,
        alias="CONTRADICTION_ADJUDICATION_MAX_TOKENS",
    )

    # Email verification
    EMAIL_FROM: Optional[str] = Field(None, alias="EMAIL_FROM")
    SMTP_HOST: Optional[str] = Field(None, alias="SMTP_HOST")  # kept for backwards compat, not used by Brevo HTTP
    SMTP_PORT: Optional[int] = Field(None, alias="SMTP_PORT")
    SMTP_USER: Optional[str] = Field(None, alias="SMTP_USER")
    SMTP_PASSWORD: Optional[str] = Field(None, alias="SMTP_PASSWORD")
    FRONTEND_URL: str = Field("http://localhost:3000", alias="FRONTEND_URL")
    BREVO_API_KEY: Optional[str] = Field(None, alias="BREVO_API_KEY")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
