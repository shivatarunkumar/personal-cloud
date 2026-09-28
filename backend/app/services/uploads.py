"""Uploads: the browser sends the bytes straight to GCS; the API only books and checks.

    1. start     a `files` row (status uploading) + a GCS resumable session for its object
    2. browser   PUTs chunks to the session URL (GCS answers 308 with what it has so far)
    3. complete  the API asks GCS for the object's size, and the file becomes ready

A reloaded page can pick an unfinished upload up again: the session URL is kept on the row
until the upload completes, and GCS keeps a session for about a week.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import get_storage
from app.core.config import Settings
from app.models.drive import File
from app.models.user import User
from app.services import drive, kinds
from app.services.errors import NotFound, ServiceError

log = logging.getLogger("cloud.uploads")

# GCS keeps a resumable session for a week; give up a little before it does
SESSION_LIFETIME = timedelta(days=6)


def session_origin(settings: Settings, origin: str | None) -> str:
    """The page that will send the chunks; GCS answers CORS for exactly this origin."""
    allowed = settings.allowed_origins
    if origin and origin.rstrip("/").lower() in allowed:
        return origin.rstrip("/").lower()
    return allowed[0]


async def start(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    folder_id: uuid.UUID | None,
    name: str,
    size: int,
    content_type: str | None,
    on_conflict: str,
    origin: str | None,
) -> tuple[File, str | None, list[str]]:
    """Book the upload. Returns the file, the session URL (None for an empty file, which is
    stored straight away: there is nothing to send) and object keys to drop after commit."""
    folder = await drive.get_folder(session, user, folder_id)
    name = drive.clean_name(name)
    if size < 0:
        raise ServiceError("Invalid size")
    if size > settings.max_upload_bytes:
        raise ServiceError(
            f"“{name}” is larger than the {settings.max_upload_bytes / 1024**3:.0f} GB limit", status_code=413
        )
    drive.check_quota(user, size)

    taken = await drive.taken_names(session, user.id, folder_id)
    replaced = False
    if name.lower() in taken:
        if on_conflict == "error":
            raise ServiceError(
                f"There's already something called “{name}” here", status_code=409, field="name"
            )
        if on_conflict == "replace":
            existing = await session.scalar(
                select(File).where(
                    File.user_id == user.id,
                    drive.in_folder(File.folder_id, folder_id),
                    File.trashed_at.is_(None),
                    File.status == "ready",
                    func.lower(File.name) == name.lower(),
                )
            )
            if existing is None:  # a folder or an unfinished upload holds the name: keep both
                name = drive.free_name(name, taken, is_file=True)
            else:
                # the old one goes to the trash, so "replace" can still be undone
                await drive.trash(session, user, [existing.id], [])
                replaced = True
        else:
            name = drive.free_name(name, taken, is_file=True)

    content_type = kinds.content_type_for(name, content_type)
    file = File(
        id=uuid.uuid4(),
        user_id=user.id,
        folder_id=folder_id,
        name=name,
        content_type=content_type,
        kind=kinds.kind_for(name, content_type),
        size_bytes=size,
        status="uploading",
    )
    stale: list[str] = []
    if replaced:
        # the old file's object is copied to the trash area before the new upload can
        # overwrite its name; the caller deletes leftovers after the commit
        stale = await drive.sync_bucket(session, settings, user)
    file.gcs_key = drive.live_file_key(settings, user, folder.path if folder else "", name)
    if len(file.gcs_key.encode()) > drive.MAX_KEY_BYTES:
        raise ServiceError("That path is too long for cloud storage (1024 bytes). Use shorter names.")
    metadata = drive.object_metadata(file)
    storage = get_storage()

    if size == 0:
        await drive.run_storage(storage.write_empty, file.gcs_key, content_type, metadata)
        file.status = "ready"
        session.add(file)
        drive.event(session, user, "upload", file_id=file.id, name=name, bytes=0)
        await session.flush()
        return file, None, stale

    url = await drive.run_storage(
        storage.resumable_session,
        file.gcs_key,
        content_type,
        size,
        session_origin(settings, origin),
        metadata,
    )
    file.upload_session_url = url
    file.upload_expires_at = datetime.now(UTC) + SESSION_LIFETIME
    session.add(file)
    await session.flush()
    log.info("upload started: %s (%s, %d bytes) → %s", name, content_type, size, file.gcs_key)
    return file, url, stale


async def _uploading(session: AsyncSession, user: User, file_id: uuid.UUID) -> File:
    file = await session.scalar(
        select(File).where(File.id == file_id, File.user_id == user.id, File.status == "uploading")
    )
    if file is None:
        raise NotFound("Upload")
    return file


async def resume_info(session: AsyncSession, user: User, file_id: uuid.UUID) -> File:
    file = await _uploading(session, user, file_id)
    if file.upload_expires_at and file.upload_expires_at < datetime.now(UTC):
        raise ServiceError("This upload is too old to resume. Upload the file again.", status_code=410)
    return file


async def complete(session: AsyncSession, user: User, file_id: uuid.UUID) -> File:
    """GCS has the whole object: check its size, and the file appears in the folder."""
    file = await _uploading(session, user, file_id)
    stored = await drive.run_storage(get_storage().stat, file.gcs_key)
    if stored is None:
        raise ServiceError("The upload hasn't reached storage yet. Try again in a moment.", status_code=409)
    if stored != file.size_bytes:
        raise ServiceError(
            f"Storage has {stored:,} of {file.size_bytes:,} bytes: the upload is incomplete", status_code=409
        )
    file.status = "ready"
    file.upload_session_url = None
    file.upload_expires_at = None
    await drive.add_usage(session, user.id, file.size_bytes)
    drive.event(session, user, "upload", file_id=file.id, name=file.name, bytes=file.size_bytes)
    await session.flush()
    log.info("upload complete: %s (%d bytes)", file.name, file.size_bytes)
    return file


async def cancel(session: AsyncSession, user: User, file_id: uuid.UUID) -> str:
    """Forget an unfinished upload. Returns the object key, deleted after the commit."""
    file = await _uploading(session, user, file_id)
    await session.delete(file)
    await session.flush()
    return file.gcs_key


async def stale(session: AsyncSession) -> list[str]:
    """Remove uploads whose session has expired; returns their object keys."""
    rows = list(
        await session.scalars(
            select(File).where(File.status == "uploading", File.upload_expires_at < datetime.now(UTC))
        )
    )
    if rows:
        await session.execute(delete(File).where(File.id.in_([r.id for r in rows])))
    return [r.gcs_key for r in rows]
