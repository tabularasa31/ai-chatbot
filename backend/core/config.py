from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = Field(..., alias="DATABASE_URL")
    environment: str = Field("development", alias="ENVIRONMENT")
    jwt_secret: str = Field(..., alias="JWT_SECRET")
    eval_jwt_secret: str = Field(..., alias="EVAL_JWT_SECRET")
    openai_api_key: str | None = Field(None, alias="OPENAI_API_KEY")
    encryption_key: str | None = Field(None, alias="ENCRYPTION_KEY")
    langfuse_host: str | None = Field(None, alias="LANGFUSE_HOST")
    langfuse_public_key: str | None = Field(None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = Field(None, alias="LANGFUSE_SECRET_KEY")
    posthog_api_key: str | None = Field(None, alias="POSTHOG_API_KEY")
    posthog_host: str = Field("https://eu.i.posthog.com", alias="POSTHOG_HOST")
    sentry_dsn: str | None = Field(None, alias="SENTRY_DSN")
    git_sha: str | None = Field(None, alias="GIT_SHA")
    pipeline_release: str | None = Field(None, alias="PIPELINE_RELEASE")
    observability_capture_full_prompts: bool = Field(
        False,
        alias="OBSERVABILITY_CAPTURE_FULL_PROMPTS",
    )
    trace_sample_rate: float = Field(1.0, alias="TRACE_SAMPLE_RATE")
    trace_high_volume_threshold: int = Field(1000, alias="TRACE_HIGH_VOLUME_THRESHOLD")
    trace_high_volume_sample_rate: float = Field(0.1, alias="TRACE_HIGH_VOLUME_SAMPLE_RATE")
    trace_new_tenant_threshold: int = Field(100, alias="TRACE_NEW_TENANT_THRESHOLD")
    trace_rate_window_seconds: int = Field(3600, alias="TRACE_RATE_WINDOW_SECONDS")
    # Full capture: record all traces (early-stage / low-traffic). Adaptive: client heuristics below (production scale).
    full_capture_mode: bool = Field(True, alias="FULL_CAPTURE_MODE")
    bm25_expansion_mode: Literal["asymmetric", "symmetric_variants"] = Field(
        "asymmetric",
        alias="BM25_EXPANSION_MODE",
    )
    embedding_model: str = Field(
        "text-embedding-3-small",
        alias="EMBEDDING_MODEL",
        description="OpenAI embedding model used across all retrieval and similarity paths.",
    )
    chat_model: str = Field(
        "gpt-5-mini",
        alias="CHAT_MODEL",
        description="OpenAI model for main chat response generation.",
    )
    guards_model: str = Field(
        "gpt-4.1-mini",
        alias="GUARDS_MODEL",
        description="OpenAI model for relevance and capability guard checks.",
    )
    extraction_model: str = Field(
        "gpt-4.1-mini",
        alias="EXTRACTION_MODEL",
        description="OpenAI model for knowledge extraction, gap analysis, and alias extraction.",
    )
    escalation_model: str = Field(
        "gpt-4.1-mini",
        alias="ESCALATION_MODEL",
        description="OpenAI model for escalation turn completions.",
    )
    escalation_max_completion_tokens: int = Field(
        600,
        alias="ESCALATION_MAX_COMPLETION_TOKENS",
        ge=1,
    )
    answer_validation_model: str = Field(
        "gpt-4.1-mini",
        alias="ANSWER_VALIDATION_MODEL",
        description="OpenAI model for answer grounding validation.",
    )
    answer_validation_max_completion_tokens: int = Field(
        150,
        alias="ANSWER_VALIDATION_MAX_COMPLETION_TOKENS",
        ge=1,
    )
    query_rewrite_model: str = Field(
        "gpt-4.1-mini",
        alias="QUERY_REWRITE_MODEL",
    )
    contradiction_adjudication_enabled: bool = Field(
        False,
        alias="CONTRADICTION_ADJUDICATION_ENABLED",
    )
    contradiction_adjudication_model: str = Field(
        "gpt-4.1-mini",
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
    clarification_turn_limit: int = Field(1, alias="CLARIFICATION_TURN_LIMIT", ge=1)
    language_detection_reliability_threshold: float = Field(
        0.7,
        alias="LANGUAGE_DETECTION_RELIABILITY_THRESHOLD",
    )
    localization_model: str = Field(
        "gpt-4.1-mini",
        alias="LOCALIZATION_MODEL",
        description="OpenAI chat model used for localize/translate/render paths.",
    )
    allowed_hosts_raw: str = Field("*", alias="ALLOWED_HOSTS")
    cors_allowed_origins_raw: str = Field(
        "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003,https://getchat9.live",
        alias="CORS_ALLOWED_ORIGINS",
    )
    widget_message_max_chars: int = Field(1000, alias="WIDGET_MESSAGE_MAX_CHARS", ge=1)
    chat_response_max_tokens: int = Field(800, alias="CHAT_RESPONSE_MAX_TOKENS", ge=1)
    chat_response_max_tokens_reasoning: int = Field(
        4096,
        alias="CHAT_RESPONSE_MAX_TOKENS_REASONING",
        ge=1,
        description="max_completion_tokens for reasoning models (o1/o3/gpt-5 family) that consume tokens for internal chain-of-thought.",
    )
    widget_chat_per_client_rate: str | None = Field(
        None,
        alias="WIDGET_CHAT_PER_CLIENT_RATE",
    )

    # Email verification
    EMAIL_FROM: str | None = Field(None, alias="EMAIL_FROM")
    SMTP_HOST: str | None = Field(None, alias="SMTP_HOST")  # kept for backwards compat, not used by Brevo HTTP
    SMTP_PORT: int | None = Field(None, alias="SMTP_PORT")
    SMTP_USER: str | None = Field(None, alias="SMTP_USER")
    SMTP_PASSWORD: str | None = Field(None, alias="SMTP_PASSWORD")
    FRONTEND_URL: str = Field("http://localhost:3000", alias="FRONTEND_URL")
    auth_cookie_domain: str | None = Field(
        None,
        alias="AUTH_COOKIE_DOMAIN",
        description="Optional parent domain for dashboard auth cookies, e.g. .getchat9.live.",
    )
    auth_cookie_samesite: Literal["lax", "strict", "none"] = Field(
        "lax",
        alias="AUTH_COOKIE_SAMESITE",
    )
    auth_cookie_secure: bool | None = Field(
        None,
        alias="AUTH_COOKIE_SECURE",
        description="Override auth cookie Secure flag. Defaults to true for HTTPS frontend URLs.",
    )
    BREVO_API_KEY: str | None = Field(None, alias="BREVO_API_KEY")

    # Read timeout for OpenAI HTTP calls (waiting for response headers / first streaming chunk).
    # Connect/write/pool timeouts are fixed at 10 s in openai_client.py.
    openai_request_timeout_seconds: float = Field(
        60.0,
        alias="OPENAI_REQUEST_TIMEOUT_SECONDS",
    )
    openai_user_retry_max_attempts: int = Field(
        3,
        alias="OPENAI_USER_RETRY_MAX_ATTEMPTS",
        ge=1,
        le=5,
    )
    openai_user_retry_budget_seconds: float = Field(
        1.5,
        alias="OPENAI_USER_RETRY_BUDGET_SECONDS",
        gt=0,
    )
    gap_shutdown_timeout_seconds: float = Field(
        25.0,
        alias="GAP_SHUTDOWN_TIMEOUT_SECONDS",
        gt=0,
    )
    gap_transient_max_attempts: int = Field(
        5,
        alias="GAP_TRANSIENT_MAX_ATTEMPTS",
        ge=1,
    )
    gap_base_delay_seconds: float = Field(
        30.0,
        alias="GAP_BASE_DELAY_SECONDS",
        gt=0,
    )
    gap_max_delay_seconds: float = Field(
        1800.0,
        alias="GAP_MAX_DELAY_SECONDS",
        gt=0,
    )

    # ── OpenAI token pricing ─────────────────────────────────────────────
    # Fallback rates used when a model is not in the per-model map below.
    openai_default_cost_per_1m_input_tokens: float = Field(
        0.30,
        alias="OPENAI_DEFAULT_COST_PER_1M_INPUT_TOKENS",
    )
    openai_default_cost_per_1m_output_tokens: float = Field(
        0.30,
        alias="OPENAI_DEFAULT_COST_PER_1M_OUTPUT_TOKENS",
    )

    @property
    def openai_model_costs(self) -> dict[str, dict[str, float]]:
        """Per-model token pricing (USD per 1M tokens) with input/output split."""
        return {
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-4.1": {"input": 2.00, "output": 8.00},
            "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
            "gpt-5-mini": {"input": 1.10, "output": 4.40},
            "o1": {"input": 15.00, "output": 60.00},
            "o1-mini": {"input": 3.00, "output": 12.00},
            "o3": {"input": 10.00, "output": 40.00},
            "o3-mini": {"input": 1.10, "output": 4.40},
        }

    def compute_cost_usd(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Estimate USD cost for one LLM call using per-model input/output rates."""
        rates = self.openai_model_costs.get(model)
        if rates:
            input_rate = rates["input"]
            output_rate = rates["output"]
        else:
            input_rate = self.openai_default_cost_per_1m_input_tokens
            output_rate = self.openai_default_cost_per_1m_output_tokens
        return round(
            (prompt_tokens / 1_000_000) * input_rate
            + (completion_tokens / 1_000_000) * output_rate,
            6,
        )

    # ── Guard thread pool ────────────────────────────────────────────────
    guard_pool_workers: int = Field(
        8,
        alias="GUARD_POOL_WORKERS",
        ge=1,
        description="Thread pool size for concurrent guard checks (injection + relevance + semantic rewrite).",
    )

    # ── Injection detector v2 ────────────────────────────────────────────
    injection_semantic_threshold: float = Field(
        0.82,
        alias="INJECTION_SEMANTIC_THRESHOLD",
    )
    injection_semantic_timeout_sec: float = Field(
        0.5,
        alias="INJECTION_SEMANTIC_TIMEOUT_SEC",
    )
    injection_semantic_enabled: bool = Field(
        True,
        alias="INJECTION_SEMANTIC_ENABLED",
    )

    # ── Retrieval guards ─────────────────────────────────────────────────────
    relevance_retrieval_threshold: float = Field(
        0.22,
        alias="RELEVANCE_RETRIEVAL_THRESHOLD",
    )
    reranker_bypass_threshold: float = Field(
        0.5,
        alias="RERANKER_BYPASS_THRESHOLD",
    )

    # ── Phase 4: Chat-log analysis ─────────────────────────────────────────
    # Messages fetched per analysis job run
    log_analysis_batch_size: int = Field(1000, alias="LOG_ANALYSIS_BATCH_SIZE")
    # Cosine similarity threshold for message clustering
    log_cluster_similarity_threshold: float = Field(
        0.82, alias="LOG_CLUSTER_SIMILARITY_THRESHOLD"
    )
    # Minimum messages in a cluster to generate a FAQ candidate
    log_cluster_min_size: int = Field(3, alias="LOG_CLUSTER_MIN_SIZE")
    # Maximum FAQ candidates created per job run
    max_faq_per_run: int = Field(20, alias="MAX_FAQ_PER_RUN")
    # Minimum confidence to auto-approve a FAQ (skip human review)
    faq_confidence_auto_accept: float = Field(0.85, alias="FAQ_CONFIDENCE_AUTO_ACCEPT")
    faq_direct_threshold: float = Field(0.92, alias="FAQ_DIRECT_THRESHOLD")
    faq_context_threshold: float = Field(0.70, alias="FAQ_CONTEXT_THRESHOLD")
    faq_context_max_items: int = Field(2, alias="FAQ_CONTEXT_MAX_ITEMS", ge=1)
    faq_approved_promotion_delta: float = Field(0.02, alias="FAQ_APPROVED_PROMOTION_DELTA")
    # Hours between cron-triggered analysis runs
    log_analysis_cron_hours: int = Field(24, alias="LOG_ANALYSIS_CRON_HOURS")
    # Threshold: number of new messages that triggers an analysis job
    log_analysis_threshold_messages: int = Field(
        100, alias="LOG_ANALYSIS_THRESHOLD_MESSAGES"
    )
    # Minimum cluster size for alias extraction
    alias_min_cluster_size: int = Field(5, alias="ALIAS_MIN_CLUSTER_SIZE")
    # Minimum lexical diversity for alias extraction (0-1)
    alias_min_diversity: float = Field(0.6, alias="ALIAS_MIN_DIVERSITY")
    # Days after which unused message embeddings are deleted
    log_embeddings_retention_days: int = Field(
        90, alias="LOG_EMBEDDINGS_RETENTION_DAYS"
    )
    # Messages per embedding API call
    embedding_batch_size: int = Field(100, alias="EMBEDDING_BATCH_SIZE")
    # Seconds to sleep between embedding batches (rate limiting)
    embedding_batch_delay_sec: float = Field(0.5, alias="EMBEDDING_BATCH_DELAY_SEC")
    # Maximum job duration before timeout (seconds)
    max_job_duration_sec: int = Field(300, alias="MAX_JOB_DURATION_SEC")

    # ── Semantic query rewrite ─────────────────────────────────────────────
    # Timeout for the concurrent LLM rewrite call (runs alongside guard checks).
    semantic_query_rewrite_timeout_sec: float = Field(
        2.0,
        alias="SEMANTIC_QUERY_REWRITE_TIMEOUT_SEC",
    )

    url_knowledge_extract_when_unchanged: bool = Field(
        False,
        alias="URL_KNOWLEDGE_EXTRACT_WHEN_UNCHANGED",
    )

    # ── Agent instructions ─────────────────────────────────────────────────
    enable_agent_instructions: bool = Field(True, alias="ENABLE_AGENT_INSTRUCTIONS")
    enable_cot_reasoning: bool = Field(True, alias="ENABLE_COT_REASONING")

    @field_validator("posthog_host", mode="before")
    @classmethod
    def _strip_posthog_host(cls, v: str) -> str:
        return v.strip().strip("'\"")

    @field_validator("auth_cookie_domain", mode="before")
    @classmethod
    def _normalize_auth_cookie_domain(cls, v: str | None) -> str | None:
        if v is None:
            return None
        domain = v.strip().strip("'\"")
        return domain or None

    @field_validator("auth_cookie_samesite", mode="before")
    @classmethod
    def _normalize_auth_cookie_samesite(cls, v: str) -> str:
        return v.strip().strip("'\"").lower()

    @field_validator("auth_cookie_secure", mode="before")
    @classmethod
    def _normalize_auth_cookie_secure(cls, v: bool | str | None) -> bool | str | None:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def effective_widget_chat_per_client_rate(self) -> str:
        if self.widget_chat_per_client_rate and self.widget_chat_per_client_rate.strip():
            return self.widget_chat_per_client_rate.strip()
        if self.environment == "development":
            return "1000/minute"
        return "120/minute"

    @property
    def allowed_hosts(self) -> list[str]:
        items = [
            item.strip()
            for item in self.allowed_hosts_raw.split(",")
            if item.strip()
        ]
        return items or ["*"]

    @property
    def cors_allowed_origins(self) -> list[str]:
        return [x.strip() for x in self.cors_allowed_origins_raw.split(",") if x.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        enable_decoding=False,
    )


settings = Settings()
