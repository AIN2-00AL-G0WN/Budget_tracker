"""
app/core/security.py
--------------------
Central security primitives:
  * Argon2id password hashing via passlib
  * JWT access / refresh / setup-link token generation & verification
  * TOTP helpers via pyotp (Google / Microsoft Authenticator compatible)

Nothing in this module touches the database — it is pure cryptographic logic.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import pyotp
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# ---------------------------------------------------------------------------
# Password hashing  (Argon2id)
# ---------------------------------------------------------------------------

pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    # Argon2id tuning — sensible defaults; adjust for your hardware
    argon2__memory_cost=65536,   # 64 MB
    argon2__time_cost=3,
    argon2__parallelism=4,
)


def hash_password(plain: str) -> str:
    """Return the Argon2id hash of *plain*."""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

TokenKind = Literal["access", "refresh", "setup", "temp_mfa", "password_reset"]


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def create_token(
    *,
    user_id: uuid.UUID,
    kind: TokenKind,
    token_version: int,
    username: str | None = None,
    is_admin: bool = False,
) -> str:
    """
    Mint a signed JWT.

    Payload claims
    --------------
    sub      : str(user_id)
    kind     : "access" | "refresh" | "setup"
    ver      : token_version  — bump this to invalidate all tokens for a user
    iat      : issued-at
    exp      : expiry
    username : only in access tokens (convenience for the frontend)
    is_admin : only in access tokens
    jti      : unique random ID (for future denylist support)
    """
    now = _utc_now()

    if kind == "access":
        delta = timedelta(minutes=settings.access_token_expire_minutes)
    elif kind == "refresh":
        delta = timedelta(days=settings.refresh_token_expire_days)
    elif kind == "temp_mfa":
        delta = timedelta(minutes=5)
    elif kind == "password_reset":
        delta = timedelta(minutes=10)
    else:  # setup
        delta = timedelta(hours=settings.setup_token_expire_hours)

    payload: dict = {
        "sub": str(user_id),
        "kind": kind,
        "ver": token_version,
        "iat": now,
        "exp": now + delta,
        "jti": secrets.token_hex(16),
    }

    if kind == "access":
        payload["username"] = username
        payload["is_admin"] = is_admin

    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT.

    Raises
    ------
    jose.JWTError  — on any validation failure (expired, bad sig, etc.)
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


def verify_token_kind(payload: dict, expected_kind: TokenKind) -> None:
    """Raise JWTError if the token is not of the expected kind."""
    if payload.get("kind") != expected_kind:
        raise JWTError(f"Expected token kind '{expected_kind}', got '{payload.get('kind')}'")


# ---------------------------------------------------------------------------
# TOTP / MFA helpers  (pyotp — RFC 6238)
# ---------------------------------------------------------------------------

def generate_totp_secret() -> str:
    """
    Generate a fresh base32 TOTP secret for a new user.
    Store this (encrypted at rest ideally) in `users.totp_secret`.
    """
    return pyotp.random_base32()


def get_totp_uri(secret: str, username: str) -> str:
    """
    Return the `otpauth://` URI that should be encoded as a QR code so the
    user can scan it with Google/Microsoft Authenticator.
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=settings.totp_issuer)


def verify_totp_code(secret: str, code: str) -> bool:
    """
    Return True if *code* is a valid TOTP code for *secret*.

    `valid_window=1` allows ±30 second clock drift between server and device.
    """
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)
