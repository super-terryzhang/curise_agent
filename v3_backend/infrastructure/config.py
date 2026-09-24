"""Application settings.

Loaded from environment variables (plus `.env` file for local dev).
Single source of truth for configuration — never read `os.getenv` elsewhere.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    # ─── Database ──────────────────────────────────────────────
    DATABASE_URL: str = Field(
        default="sqlite:///./dev.db",
        description="SQLAlchemy connection URL. Shared with v2 in production.",
    )

    # ─── Auth / JWT ────────────────────────────────────────────
    SECRET_KEY: str = Field(
        default="dev-secret-change-in-production",
        description="Environment-specific signing key; legacy tokens are not accepted.",
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=1, le=60)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1, le=30)
    MAX_FAILED_LOGIN_ATTEMPTS: int = Field(default=5, ge=3, le=20)
    ACCOUNT_LOCK_MINUTES: int = Field(default=15, ge=1, le=60)
    JWT_ISSUER: str = "cruise-development"
    JWT_AUDIENCE: str = "cruise-api"
    SESSION_MAX_DAYS: int = Field(default=7, ge=1, le=30)
    TEMPORARY_PASSWORD_HOURS: int = Field(default=24, ge=1, le=72)
    RESTRICTED_SESSION_MINUTES: int = Field(default=15, ge=1, le=30)
    REFRESH_RETRY_SECONDS: int = Field(default=5, ge=0, le=10)
    AUTH_SOURCE_PER_MINUTE: int = Field(default=120, ge=10)
    AUTH_ACCOUNT_PER_15_MINUTES: int = Field(default=20, ge=5)
    SECURITY_EVENT_RETENTION_DAYS: int = Field(default=180, ge=30)

    # ─── CORS ──────────────────────────────────────────────────
    ALLOWED_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://localhost:3002",  # v3-frontend
        ]
    )
    CORS_PREVIEW_ORIGIN_REGEX: str | None = None

    # ─── Environment ───────────────────────────────────────────
    ENV: Literal["development", "test", "staging", "production"] = "development"
    K_SERVICE: str = ""

    # Public origin of THIS backend, including scheme + host (no trailing
    # slash). Used to prefix backend-proxied storage URLs (GCS
    # `/uploads/{key}`) so the frontend can render <img src=...> directly
    # without hardcoding the API base. Empty string falls back to a
    # relative path (works for local Next.js rewrites; breaks for Vercel
    # cross-origin). Set in prod via env_vars.yaml.
    PUBLIC_BACKEND_URL: str = ""

    # ─── External services (optional — filled in later phases) ─
    GOOGLE_API_KEY: str = ""
    MOONSHOT_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    # Serper (google.serper.dev) — used by general_agent's web_search tool.
    # Without it, web_search auto-filters out of the LLM's tool list
    # (general_agent checks .available() via requires_env). Free tier
    # is 2500 searches/month.
    SERPER_API_KEY: str = ""
    AGENT_PO_EXTRACT_MODEL: str = "gemini-3.5-flash"
    # Chat agent (general-agent runtime via Gemini OpenAI-compat endpoint).
    # Override at runtime if a different Gemini model is preferred.
    AGENT_CHAT_MODEL: str = "gemini-3.5-flash"
    DOCUMENT_AI_PROJECT_ID: str = ""
    DOCUMENT_AI_PROCESSOR_ID: str = ""
    DOCUMENT_AI_LOCATION: str = "us"
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    STORAGE_BUCKET: str = "v3-files"
    # Explicit storage backend: "gcs" | "supabase" | "local" | "" (auto).
    # In prod (GCP) set to "gcs"; legacy Supabase keeps working if set to "supabase".
    STORAGE_BACKEND: str = ""
    LINE_CHANNEL_SECRET: str = ""
    LINE_CHANNEL_ACCESS_TOKEN: str = ""
    # Stable identifier for the LINE channel — purely an internal key in
    # `v3_line_users.line_channel_id`. Defaults to "default" so single-channel
    # deployments don't have to set it; multi-channel future deployments
    # override per channel (e.g. "prod", "test") so the same LINE userId in
    # two channels doesn't collide on the unique constraint.
    LINE_CHANNEL_ID: str = "default"
    # Base URL of the v3 frontend — used to build the bind link we send via
    # the bot. The bind page itself is at `${LINE_BIND_BASE_URL}/line/bind?token=...`.
    LINE_BIND_BASE_URL: str = "http://localhost:3002"
    # How long a bind URL stays valid before the user must re-request one.
    LINE_BIND_TOKEN_TTL_MINUTES: int = 30
    # Hard ceiling we self-enforce: agent runs that don't finish within
    # this many seconds get aborted and the user gets a "go to web" reply.
    # Must stay under LINE's 30-second reply-token window so we always reply
    # within the free tier.
    LINE_AGENT_TIMEOUT_SECONDS: int = 25
    # Skip webhook signature verification — DEV ONLY. NEVER set this true
    # in production; LINE's signature is the only thing keeping random
    # internet POSTs from impersonating the platform.
    LINE_DISABLE_SIGNATURE_VERIFICATION: bool = False

    # ─── Upload limits ─────────────────────────────────────────
    MAX_UPLOAD_SIZE: int = 30 * 1024 * 1024  # 30 MB

    # ─── Feature flags ─────────────────────────────────────────
    # When true (default), POST /documents/{id}/create-order returns as
    # soon as the Order row exists (status="matching") and the Gemini
    # matching pipeline runs in the background via AsyncioRunner. The
    # frontend then polls /orders/{id} until status flips to "ready" /
    # "error". Set to false to fall back to the legacy synchronous
    # behavior — useful if a deploy regresses the polling UI.
    ASYNC_CREATE_ORDER: bool = True

    # Same idea for the "manually set this document's type to
    # purchase_order" PATCH path — the Gemini enrichment that fires on
    # type change used to block the request for 8-30s, causing user-
    # visible timeouts (2026-06-22). When true (default), PATCH returns
    # as soon as the doc_type column is saved; enrichment runs in the
    # background. Set false to fall back to the synchronous path.
    ASYNC_DOC_TYPE_ENRICH: bool = True

    # Apply verified masterdata unit-conversion rules during order matching.
    # Expand-first rollout keeps this disabled until migration and shadow audit
    # are complete; false preserves the existing one-row manual workflow.
    UNIT_CONVERSION_RULES_ENABLED: bool = False

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.K_SERVICE and self.ENV not in ("production", "staging"):
            raise ValueError("Cloud Run requires an explicit production or staging ENV")
        if self.ENV in ("production", "staging"):
            invalid = []
            if len(self.SECRET_KEY.encode()) < 32 or self.SECRET_KEY.startswith(("dev-secret", "change-me", "replace-me", "your-secret")):
                invalid.append("SECRET_KEY")
            if not self.DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
                invalid.append("DATABASE_URL")
            if self.JWT_ISSUER == "cruise-development" or not self.JWT_ISSUER:
                invalid.append("JWT_ISSUER")
            if not self.JWT_AUDIENCE:
                invalid.append("JWT_AUDIENCE")
            if not self.PUBLIC_BACKEND_URL.startswith("https://"):
                invalid.append("PUBLIC_BACKEND_URL")
            if self.STORAGE_BACKEND not in ("gcs", "supabase"):
                invalid.append("STORAGE_BACKEND")
            if any(not origin.startswith("https://") for origin in self.ALLOWED_ORIGINS) or not self.ALLOWED_ORIGINS:
                invalid.append("ALLOWED_ORIGINS")
            if self.LINE_DISABLE_SIGNATURE_VERIFICATION:
                invalid.append("LINE_DISABLE_SIGNATURE_VERIFICATION")
            if invalid:
                raise ValueError("Invalid production configuration fields: " + ", ".join(invalid))
        return self

    @property
    def DEBUG(self) -> bool:
        return self.ENV == "development"

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
