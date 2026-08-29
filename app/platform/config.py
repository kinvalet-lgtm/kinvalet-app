"""Application configuration via pydantic-settings (twelve-factor)."""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Application ───────────────────────────────────────────────────────────
    environment: Literal["development", "staging", "production"] = "development"
    secret_key: str = Field(default="dev-secret-key-change-in-production")
    debug: bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    # Supabase transaction-mode pooler (port 6543) — no prepared statements
    database_url: str = Field(
        default="postgresql+asyncpg://sandwich:sandwich_dev_password@localhost:5432/sandwich_copilot"
    )
    database_sync_url: str = Field(
        default="postgresql://sandwich:sandwich_dev_password@localhost:5432/sandwich_copilot"
    )

    # ── Supabase Auth ─────────────────────────────────────────────────────────
    supabase_url: str = Field(default="")
    supabase_anon_key: str = Field(default="")
    supabase_jwt_secret: str = Field(default="dev-jwt-secret")

    # ── Twilio / WhatsApp ─────────────────────────────────────────────────────
    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    twilio_whatsapp_number: str = Field(default="whatsapp:+14155238886")
    twilio_webhook_base_url: str = Field(default="http://localhost:8000")

    # ── LLM ───────────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="")
    openai_api_key: str = Field(default="")
    confidence_threshold: float = Field(default=0.90, ge=0.0, le=1.0)

    # ── Google ────────────────────────────────────────────────────────────────
    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")
    google_maps_api_key: str = Field(default="")

    # ── Microsoft ─────────────────────────────────────────────────────────────
    microsoft_client_id: str = Field(default="")
    microsoft_client_secret: str = Field(default="")

    # ── Meta WhatsApp Cloud API (replaces Twilio) ─────────────────────────────
    meta_phone_number_id: str = Field(default="")   # From Meta Business Suite
    meta_access_token: str = Field(default="")       # Permanent or system user token
    meta_app_secret: str = Field(default="")          # For webhook signature verification
    meta_verify_token: str = Field(default="")        # For webhook URL verification

    # ── Plaid ─────────────────────────────────────────────────────────────────
    plaid_client_id: str = Field(default="")
    plaid_secret: str = Field(default="")
    plaid_env: Literal["sandbox", "development", "production"] = "sandbox"

    # ── Notification controls ─────────────────────────────────────────────────
    notification_daily_ceiling: int = Field(default=12, ge=1)

    # ── Secrets backend ───────────────────────────────────────────────────────
    secrets_backend: Literal["env", "aws_secrets_manager"] = "env"
    aws_region: str = Field(default="us-east-1")

    @field_validator("database_url")
    @classmethod
    def validate_db_url(cls, v: str) -> str:
        if not v:
            raise ValueError("DATABASE_URL is required")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
