"""Uploads and file bytes.

`/content` serves a file inline for the viewer and the video player, honouring HTTP Range:
the browser asks for the part it needs (seeking in a video jumps straight there), and only
that part is read from GCS. `/download` is the same bytes as an attachment. Nothing is
fetched until one of these is called, which only happens when someone opens a file.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import get_storage
from app.api.deps import current_user, to_http
from app.api.v1.drive import files_out
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.models.user import User
from app.schemas.drive import Conflict, ConflictCheck, FileOut, UploadOut, UploadStart
from app.services import drive, kinds, uploads
from app.services.errors import ServiceError

router = APIRouter(tags=["files"])
log = logging.getLogger("cloud.files")

# An open-ended range ("bytes=0-", what a video element sends first) is answered with at
# most this much; the browser asks for more as it plays. Seeking never downloads the gap.
MAX_OPEN_RANGE = 8 * 1024 * 1024
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


# ------------------------------------------------------------------ uploads
@router.post("/uploads", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def start_upload(
    data: UploadStart,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    origin: str | None = Header(default=None),
) -> UploadOut:
    try:
        file, url, stale = await uploads.start(
            session,
            settings,
            user,
            folder_id=data.folder_id,
            name=data.name,
            size=data.size,
            content_type=data.content_type,
            on_conflict=data.on_conflict,
            origin=origin,
        )
    except ServiceError as exc:
        raise to_http(exc) from exc
    await session.commit()
    await drive.drop_stale(session, user, stale)
    return UploadOut(
        file=FileOut.model_validate(file), upload_url=url, chunk_size=settings.upload_chunk_bytes
    )


@router.post("/uploads/conflicts", response_model=list[Conflict])
async def upload_conflicts(
    data: ConflictCheck, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    """Before an upload: which of these names already exist? Nothing is changed.

    The web app asks once for a whole batch (up to 20,000 names per call) and lets the
    person choose Replace, Skip or Keep both for each clash.
    """
    return await drive.existing_names(session, user, [(i.folder_id, i.name.strip()) for i in data.items])


@router.get("/uploads/{file_id}", response_model=UploadOut)
async def resume_upload(
    file_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UploadOut:
    """An unfinished upload's session, so a reloaded page can carry on where it stopped."""
    try:
        file = await uploads.resume_info(session, user, file_id)
    except ServiceError as exc:
        raise to_http(exc) from exc
    return UploadOut(
        file=FileOut.model_validate(file),
        upload_url=file.upload_session_url,
        chunk_size=settings.upload_chunk_bytes,
    )


@router.post("/uploads/{file_id}/complete", response_model=FileOut)
async def complete_upload(
    file_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FileOut:
    try:
        file = await uploads.complete(session, user, file_id)
        # if its folder was renamed or trashed meanwhile, the object follows now
        await drive.save(session, settings, user)
    except ServiceError as exc:
        raise to_http(exc) from exc
    return FileOut.model_validate(file)


@router.delete("/uploads/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_upload(
    file_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        key = await uploads.cancel(session, user, file_id)
    except ServiceError as exc:
        raise to_http(exc) from exc
    await session.commit()
    await drive.drop_stale(session, user, [key])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ bytes
@router.get("/files/{file_id}", response_model=FileOut)
async def file_info(
    file_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> FileOut:
    try:
        file = await drive.get_file(session, user, file_id)
    except ServiceError as exc:
        raise to_http(exc) from exc
    return (await files_out(session, [file], with_path=True))[0]


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """(start, end) inclusive, or None for the whole file. Raises on a range past the end."""
    if not header or size == 0:
        return None
    match = RANGE_RE.match(header.strip())
    if not match or (not match.group(1) and not match.group(2)):
        return None  # multiple or odd ranges: send the whole thing, which is always allowed
    first, last = match.group(1), match.group(2)
    if not first:  # "bytes=-500": the last 500 bytes
        start, end = max(0, size - int(last)), size - 1
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else min(size - 1, start + MAX_OPEN_RANGE - 1)
    if start >= size or start > end:
        raise ValueError("range not satisfiable")
    return start, end


def disposition(kind: str, name: str) -> str:
    ascii_name = name.encode("ascii", "ignore").decode().replace('"', "").replace("\\", "") or "file"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


async def _serve(
    file_id: uuid.UUID, request: Request, user: User, session: AsyncSession, *, attachment: bool
) -> Response:
    try:
        file = await drive.get_file(session, user, file_id)
    except ServiceError as exc:
        raise to_http(exc) from exc

    inline = not attachment and kinds.safe_inline(file.content_type)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": disposition("inline" if inline else "attachment", file.name),
        "X-Content-Type-Options": "nosniff",
        # private: the browser may keep it, shared caches may not
        "Cache-Control": "private, max-age=3600",
    }
    if file.kind != "pdf":  # the browser's PDF viewer doesn't run in a sandbox
        # a file opened on its own can't run script or reach anything on this origin
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; media-src 'self'; sandbox"
    try:
        byte_range = parse_range(request.headers.get("range"), file.size_bytes)
    except ValueError:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file.size_bytes}"})

    # opening or downloading is what "Recent" goes by; one write per open, not per range
    if byte_range is None or byte_range[0] == 0:
        file.last_opened_at = datetime.now(UTC)
        if attachment:
            drive.event(session, user, "download", file_id=file.id, name=file.name)
        await session.commit()

    storage = get_storage()
    if file.size_bytes == 0:
        return Response(content=b"", media_type=file.content_type, headers=headers)
    if byte_range is None:
        start, end, code = 0, file.size_bytes - 1, 200
    else:
        (start, end), code = byte_range, 206
        headers["Content-Range"] = f"bytes {start}-{end}/{file.size_bytes}"
    headers["Content-Length"] = str(end - start + 1)
    log.info(
        "%s %s bytes %d-%d of %d",
        "download" if attachment else "open",
        file.name,
        start,
        end,
        file.size_bytes,
    )
    # a sync generator: Starlette pulls it in a worker thread, so GCS reads don't block
    return StreamingResponse(
        storage.read_range(file.gcs_key, start, end),
        status_code=code,
        media_type=file.content_type,
        headers=headers,
    )


@router.get("/files/{file_id}/content")
async def content(
    file_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _serve(file_id, request, user, session, attachment=False)


@router.get("/files/{file_id}/download")
async def download(
    file_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    return await _serve(file_id, request, user, session, attachment=True)
