"""Shared FastAPI dependencies: the signed-in user, from the access-token cookie.

A 401 is the most common "why doesn't this work?", so each way it can happen is logged
with its reason: no cookie, or an expired or tampered token.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.logging import user_var
from app.core.security import decode_access_token
from app.models.user import User
from app.services.errors import ServiceError

ACCESS_COOKIE = "pc_access"
REFRESH_COOKIE = "pc_refresh"

log = logging.getLogger("cloud.auth")


async def current_user_optional(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    pc_access: str | None = Cookie(default=None),
) -> User | None:
    if not pc_access:
        log.debug("no %s cookie: anonymous", ACCESS_COOKIE)
        return None
    claims = decode_access_token(settings, pc_access)
    if not claims:
        return None
    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError):
        log.warning("access token has no usable subject claim")
        return None
    user = await session.get(User, user_id)
    if user is None:
        log.info("access token names user %s, who no longer exists", user_id)
        return None
    user_var.set(user.username)  # every later log line in this request names them
    return user


async def current_user(user: User | None = Depends(current_user_optional)) -> User:
    if user is None:
        log.info(
            "401: no valid session (missing or expired %s cookie). The browser refreshes at "
            "POST /api/v1/auth/refresh, or signs in again",
            ACCESS_COOKIE,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Sign in to continue"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def current_session_id(
    settings: Settings = Depends(get_settings), pc_access: str | None = Cookie(default=None)
) -> uuid.UUID | None:
    """Which device this is: the refresh-token row named in the access token."""
    claims = decode_access_token(settings, pc_access) if pc_access else None
    try:
        return uuid.UUID(claims["sid"]) if claims else None
    except (KeyError, ValueError):
        return None


def to_http(error: ServiceError) -> HTTPException:
    detail: dict = {"message": error.message}
    if error.field:
        detail["field"] = error.field
    return HTTPException(status_code=error.status_code, detail=detail)
