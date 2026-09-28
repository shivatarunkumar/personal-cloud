"""The signed-in person's own account: profile, password, devices, and deleting it."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import get_storage
from app.api.deps import current_session_id, current_user, to_http
from app.api.v1.auth import clear_cookies
from app.core import security
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.models.user import User
from app.schemas.auth import ChangePasswordIn, DeleteAccountIn, ProfileIn, SessionOut, UserOut
from app.services import auth as auth_service
from app.services import drive
from app.services.errors import ServiceError

router = APIRouter(prefix="/account", tags=["account"])
log = logging.getLogger("cloud.account")


@router.patch("/me", response_model=UserOut)
async def update_profile(
    data: ProfileIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> User:
    user.display_name = data.display_name
    await session.commit()
    return user


@router.post("/password", response_model=UserOut)
async def change_password(
    data: ChangePasswordIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    sid: uuid.UUID | None = Depends(current_session_id),
) -> User:
    """New password. Every other device is signed out; this one stays signed in."""
    try:
        await auth_service.change_password(session, user, data.current_password, data.new_password, keep=sid)
    except ServiceError as exc:
        raise to_http(exc) from exc
    await session.commit()
    return user


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    sid: uuid.UUID | None = Depends(current_session_id),
) -> list[SessionOut]:
    return [
        SessionOut(
            id=row.id,
            user_agent=row.user_agent,
            ip=str(row.ip) if row.ip else None,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            current=row.id == sid,
        )
        for row in await auth_service.sessions(session, user)
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_device(
    session_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        await auth_service.sign_out_device(session, user, session_id)
    except ServiceError as exc:
        raise to_http(exc) from exc
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    data: DeleteAccountIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Delete the account and every file in it, from the bucket too. Cannot be undone."""
    if not security.verify_password(data.password, user.password_hash):
        raise to_http(ServiceError("That password is not right", field="password"))
    username = user.username
    prefixes = [
        drive.user_root(settings, user),
        drive.trash_root(settings, user),
        f"{settings.gcs_prefix}u/{user.id}/",  # the layout before readable paths
    ]
    try:
        count = 0
        for prefix in prefixes:
            count += await drive.run_storage(get_storage().delete_prefix, prefix)
    except ServiceError as exc:
        raise to_http(exc) from exc
    await session.delete(user)  # folders, files, sessions and history cascade
    await session.commit()
    log.warning("account %s deleted with %d object(s)", username, count)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_cookies(response)
    return response
