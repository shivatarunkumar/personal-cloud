"""Sign-up, sign-in, refresh, sign-out.

Tokens travel as httpOnly cookies, so JavaScript can't read them and the browser sends
them automatically. The web app and the API share an origin (Next.js proxies /api/*), and
cookies are SameSite=Lax, which blocks cross-site posts.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ACCESS_COOKIE, REFRESH_COOKIE, current_user, to_http
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.auth import LoginIn, RegisterIn, UserOut
from app.services import auth as auth_service
from app.services.errors import ServiceError

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("cloud.auth")

REFRESH_PATH = "/api/v1/auth"


def client(request: Request) -> tuple[str | None, str | None]:
    return request.headers.get("user-agent"), (request.client.host if request.client else None)


def set_cookies(
    response: Response, settings: Settings, user: User, refresh_token: str, session_id: uuid.UUID
) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        create_access_token(settings, user.id, session_id),
        max_age=settings.access_token_ttl_min * 60,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path=REFRESH_PATH,
    )


def clear_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)


@router.get("/config")
async def auth_config(settings: Settings = Depends(get_settings)) -> dict:
    """What the sign-in page needs to know before anyone is signed in."""
    return {"app_name": settings.app_name, "allow_signup": settings.allow_signup}


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    """Create an account and sign in straight away."""
    if not settings.allow_signup:
        raise HTTPException(
            status_code=403, detail={"message": "New accounts are switched off on this cloud"}
        )
    try:
        user = await auth_service.register(session, settings, data)
        user_agent, ip = client(request)
        token, sid = await auth_service.issue_refresh_token(
            session, settings, user, user_agent=user_agent, ip=ip
        )
    except ServiceError as exc:
        raise to_http(exc) from exc
    await session.commit()
    set_cookies(response, settings, user, token, sid)
    return user


@router.post("/login", response_model=UserOut)
async def login(
    data: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    try:
        user = await auth_service.authenticate(session, data.login, data.password)
        user_agent, ip = client(request)
        token, sid = await auth_service.issue_refresh_token(
            session, settings, user, user_agent=user_agent, ip=ip
        )
    except ServiceError as exc:
        await session.commit()  # keep the failed-attempt counter
        raise to_http(exc) from exc
    await session.commit()
    set_cookies(response, settings, user, token, sid)
    return user


@router.post("/refresh", response_model=UserOut, responses={401: {}})
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    pc_refresh: str | None = Cookie(default=None),
) -> User | JSONResponse:
    """Swap the refresh cookie for a new pair. The web app calls this on a 401."""
    if not pc_refresh:
        raise HTTPException(status_code=401, detail={"message": "Please sign in"})
    try:
        user_agent, ip = client(request)
        user, token, sid = await auth_service.rotate_refresh_token(
            session, settings, pc_refresh, user_agent=user_agent, ip=ip
        )
    except ServiceError as exc:
        await session.commit()  # a detected reuse must stay revoked
        # a dead refresh cookie is removed, so the browser stops offering it
        failed = JSONResponse(status_code=exc.status_code, content={"detail": {"message": exc.message}})
        clear_cookies(failed)
        return failed
    await session.commit()
    set_cookies(response, settings, user, token, sid)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: AsyncSession = Depends(get_session),
    pc_refresh: str | None = Cookie(default=None),
) -> Response:
    if pc_refresh:
        await auth_service.revoke_refresh_token(session, pc_refresh)
        await session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    clear_cookies(response)
    return response


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> User:
    return user
