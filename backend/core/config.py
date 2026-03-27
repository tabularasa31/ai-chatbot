from __future__ import annotations

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Глобальные настройки приложения, загружаемые из .env."""

    database_url: str = Field(..., alias="DATABASE_URL")
    environment: str = Field("development", alias="ENVIRONMENT")
    jwt_secret: str = Field(..., alias="JWT_SECRET")
    openai_api_key: Optional[str] = Field(None, alias="OPENAI_API_KEY")
    encryption_key: Optional[str] = Field(None, alias="ENCRYPTION_KEY")
    langfuse_host: Optional[str] = Field(None, alias="LANGFUSE_HOST")
    langfuse_public_key: Optional[str] = Field(None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: Optional[str] = Field(None, alias="LANGFUSE_SECRET_KEY")

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
