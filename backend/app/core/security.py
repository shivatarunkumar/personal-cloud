"""Password hashing and JWT access / refresh tokens.

- Passwords: argon2id (memory-hard, the current recommendation for password storage).
- Access token: short-lived JWT, sent as an httpOnly cookie.
- Refresh token: random opaque string; only its SHA-256 hash is stored, so a database
  leak can't be used to log in. Every use rotates it (see services/auth.py).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import Settings

log = logging.getLogger("cloud.auth")

_hasher = PasswordHasher()
ALGORITHM = "HS256"
TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when argon2's parameters changed since this hash was made."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def create_access_token(settings: Settings, user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """`sid` names the refresh-token row, i.e. this device's session."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_min),
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(settings: Settings, token: str) -> dict[str, Any] | None:
    """Return the claims, or None if the token is invalid, expired or the wrong type."""
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        log.debug("access token expired (the browser should call /auth/refresh)")
        return None
    except jwt.InvalidSignatureError:
        log.warning("access token signature does not match JWT_SECRET: was the secret changed?")
        return None
    except jwt.PyJWTError as exc:
        log.warning("access token could not be decoded: %s", exc.__class__.__name__)
        return None
    if claims.get("typ") != "access":
        log.warning("token is a %r, not an access token", claims.get("typ"))
        return None
    return claims


def hash_token(token: str) -> str:
    """What we store instead of an opaque token, so the table is useless if it leaks."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_opaque_token() -> tuple[str, str]:
    """(token to hand out, hash to store). Used for refresh tokens and reset links."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token)


def new_refresh_token() -> tuple[str, str]:
    """Return (token to send to the browser, hash to store)."""
    return new_opaque_token()


def hash_refresh_token(token: str) -> str:
    return hash_token(token)
