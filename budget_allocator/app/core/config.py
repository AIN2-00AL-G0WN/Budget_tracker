"""
app/core/config.py
------------------
Application-wide settings driven by environment variables (or a .env file).

All secrets (secret_key, db credentials) should be provided via environment
variables in production — never hard-coded.
"""

from __future__ import annotations

from pydantic import Field, model_validator
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
    secret_key: str = Field(
        default="CHANGE_ME_IN_PRODUCTION_use_openssl_rand_hex_32",
        description="Secret key for signing JWTs — MUST be changed in production.",
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    setup_token_expire_hours: int = 72    # One-time provisioning link TTL

    # ------------------------------------------------------------------
    # TOTP / MFA
    # ------------------------------------------------------------------
    totp_issuer: str = "BudgetAllocator"  # Displayed in Authenticator apps


# Singleton — import this in other modules
settings = Settings()
