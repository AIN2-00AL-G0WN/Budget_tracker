"""
app/core/config.py
------------------
Application-wide settings driven by environment variables (or a .env file).

All secrets (secret_key, db credentials) MUST be provided via environment
variables.  The application refuses to start if SECRET_KEY is absent or
still set to the well-known placeholder value (Fix #2).
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_name: str = "Budget Allocator & Tracker"
    app_version: str = "0.1.0"
    debug: bool = False

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    # Provide the full DSN **or** individual components; the validator
    # below builds the URL automatically from components if DSN is absent.
    database_url: str = Field(
        default="",
        description="Full asyncpg DSN: postgresql+asyncpg://user:pw@host:5432/dbname",
    )
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "budget_tracker"
    db_user: str = "postgres"
    db_password: str = "postgres"
    db_echo: bool = False          # Set True locally to see SQL queries

    @model_validator(mode="after")
    def _build_database_url(self) -> "Settings":
        if not self.database_url:
            self.database_url = (
                f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        return self

    # ------------------------------------------------------------------
    # Security / JWT
    # ------------------------------------------------------------------
    # REQUIRED — no default.  Generate with: openssl rand -hex 32
    # The app will refuse to start if this is missing or still set to the
    # placeholder value.
    secret_key: str = Field(
        ...,
        min_length=32,
        description="JWT signing secret — generate with: openssl rand -hex 32",
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    setup_token_expire_hours: int = 72    # One-time provisioning link TTL

    @field_validator("secret_key")
    @classmethod
    def _reject_placeholder_key(cls, v: str) -> str:
        """Prevent accidental production deployment with the well-known placeholder."""
        if v.startswith("CHANGE_ME"):
            raise ValueError(
                "SECRET_KEY is still set to the placeholder value. "
                "Generate a real key with: openssl rand -hex 32"
            )
        return v

    # ------------------------------------------------------------------
    # TOTP / MFA
    # ------------------------------------------------------------------
    totp_issuer: str = "BudgetAllocator"  # Displayed in Authenticator apps

    # ------------------------------------------------------------------
    # Initial Admin Seeding
    # ------------------------------------------------------------------
    admin_username: str = "tejasbhat2001@gmail.com"
    admin_password: str = "Rdl@12345"


# Singleton — import this in other modules
settings = Settings()
