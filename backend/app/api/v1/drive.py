"""Browsing and organising: folders, listings, move/copy, trash.

Nothing here reads a file's bytes: every listing comes from the database, so opening the
app costs no GCS traffic at all.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, to_http
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.models.drive import File, Folder
from app.models.user import User
from app.schemas.drive import (
    Crumb,
    EnsureFolders,
    EnsureFoldersOut,
    FileOut,
    FolderCreate,
    FolderOut,
    ListingOut,
    Order,
    Outcome,
    Rename,
    Selection,
    Sort,
    Transfer,
    UsageOut,
)
from app.services import drive
from app.services.errors import ServiceError

router = APIRouter(prefix="/drive", tags=["drive"])

ROOT = "root"


def parse_folder_id(value: str) -> uuid.UUID | None:
    if value == ROOT:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise to_http(ServiceError("Folder not found", status_code=404)) from exc


async def files_out(session: AsyncSession, files: list[File], with_path: bool = False) -> list[FileOut]:
    paths = await drive.folder_paths(session, {f.folder_id for f in files}) if with_path else {}
    out = []
    for file in files:
        item = FileOut.model_validate(file)
        if with_path:
            item.folder_path = paths.get(file.folder_id, "") if file.folder_id else ""
        out.append(item)
    return out


async def folders_out(session: AsyncSession, folders: list[Folder]) -> list[FolderOut]:
    counts = await drive.folder_counts(session, [f.id for f in folders])
    return [
        FolderOut.model_validate(f).model_copy(update={"item_count": counts.get(f.id, 0)}) for f in folders
    ]


def outcome(result: drive.Outcome) -> Outcome:
    return Outcome(files=result.files, folders=result.folders, renamed=result.renamed)


# ------------------------------------------------------------------ browse
@router.get("/usage", response_model=UsageOut)
async def usage(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    return await drive.usage(session, user)


@router.get("/folders/{folder_id}", response_model=ListingOut)
async def list_folder(
    folder_id: str,
    sort: Sort = "name",
    order: Order = "asc",
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ListingOut:
    """A folder's contents ("root" = My files), with breadcrumbs."""
    try:
        folder = await drive.get_folder(session, user, parse_folder_id(folder_id))
        folders, files = await drive.children(session, user, folder, sort, order)
        chain = await drive.breadcrumbs(session, user, folder)
    except ServiceError as exc:
        raise to_http(exc) from exc
    return ListingOut(
        folder=FolderOut.model_validate(folder) if folder else None,
        breadcrumbs=[Crumb(id=None, name="My files"), *[Crumb(id=f.id, name=f.name) for f in chain]],
        folders=await folders_out(session, folders),
        files=await files_out(session, files),
    )


@router.get("/tree", response_model=list[FolderOut])
async def tree(
    parent: str = ROOT, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[FolderOut]:
    """One level of folders, for the move/copy picker (it loads deeper levels on demand)."""
    return await folders_out(session, await drive.subfolders(session, user, parse_folder_id(parent)))


@router.get("/recent", response_model=list[FileOut])
async def recent(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await files_out(session, await drive.recent(session, user), with_path=True)


@router.get("/kind/{kind}", response_model=list[FileOut])
async def by_kind(
    kind: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Every photo, every video … newest first. Names and sizes only, no thumbnails."""
    if kind not in {"image", "video", "audio", "pdf", "text", "archive", "document", "other"}:
        raise to_http(ServiceError("Unknown kind", status_code=404))
    return await files_out(session, await drive.by_kind(session, user, kind, limit, offset), with_path=True)


@router.get("/search", response_model=ListingOut)
async def search(
    q: str = Query(min_length=1, max_length=200),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ListingOut:
    folders, files = await drive.search(session, user, q)
    return ListingOut(
        folder=None,
        breadcrumbs=[Crumb(id=None, name="My files")],
        folders=await folders_out(session, folders),
        files=await files_out(session, files, with_path=True),
    )


# ------------------------------------------------------------------ changes
# Every change goes through `drive.save`: the database is updated, the bucket's objects are
# moved to match (readable paths), then the commit, then leftovers are deleted.


async def apply(session: AsyncSession, settings: Settings, user: User, change, extra_stale=()):
    """Run a change and save it; a ServiceError anywhere becomes the HTTP answer."""
    try:
        result = await change()
        await drive.save(session, settings, user, list(extra_stale))
    except ServiceError as exc:
        await session.rollback()
        raise to_http(exc) from exc
    return result


@router.post("/folders", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(
    data: FolderCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FolderOut:
    folder = await apply(
        session, settings, user, lambda: drive.create_folder(session, user, data.parent_id, data.name)
    )
    return FolderOut.model_validate(folder).model_copy(update={"item_count": 0})


@router.post("/folders/ensure", response_model=EnsureFoldersOut)
async def ensure_folders(
    data: EnsureFolders,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> EnsureFoldersOut:
    """Create a dropped folder tree in one go (existing folders are reused)."""
    mapping = await apply(
        session, settings, user, lambda: drive.ensure_folders(session, user, data.parent_id, data.paths)
    )
    return EnsureFoldersOut(folders=mapping)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
async def rename_folder(
    folder_id: uuid.UUID,
    data: Rename,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FolderOut:
    """Renaming a folder moves every object under it to the new path in the bucket."""
    folder = await apply(
        session, settings, user, lambda: drive.rename_folder(session, user, folder_id, data.name)
    )
    return (await folders_out(session, [folder]))[0]


@router.patch("/files/{file_id}", response_model=FileOut)
async def rename_file(
    file_id: uuid.UUID,
    data: Rename,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FileOut:
    file = await apply(session, settings, user, lambda: drive.rename_file(session, user, file_id, data.name))
    return FileOut.model_validate(file)


@router.post("/move", response_model=Outcome)
async def move(
    data: Transfer,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Outcome:
    result = await apply(
        session,
        settings,
        user,
        lambda: drive.move(session, user, data.file_ids, data.folder_ids, data.target_folder_id),
    )
    return outcome(result)


@router.post("/copy", response_model=Outcome)
async def copy(
    data: Transfer,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Outcome:
    # a failed copy deletes the objects it already wrote (inside drive.copy)
    result = await apply(
        session,
        settings,
        user,
        lambda: drive.copy(session, settings, user, data.file_ids, data.folder_ids, data.target_folder_id),
    )
    return outcome(result)


# ------------------------------------------------------------------ trash
@router.post("/trash", response_model=Outcome)
async def trash(
    data: Selection,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Outcome:
    """Trashed items move to _trash/{username}/… in the bucket, so their names are free again."""
    result = await apply(
        session, settings, user, lambda: drive.trash(session, user, data.file_ids, data.folder_ids)
    )
    return outcome(result)


@router.get("/trash", response_model=ListingOut)
async def list_trash(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    folders, files = await drive.list_trash(session, user)
    return ListingOut(
        folder=None,
        breadcrumbs=[],
        folders=[FolderOut.model_validate(f) for f in folders],
        files=await files_out(session, files, with_path=True),
    )


@router.post("/restore", response_model=Outcome)
async def restore(
    data: Selection,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Outcome:
    result = await apply(
        session, settings, user, lambda: drive.restore(session, user, data.file_ids, data.folder_ids)
    )
    return outcome(result)


@router.post("/delete", response_model=Outcome)
async def delete_forever(
    data: Selection,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Outcome:
    """Delete trashed items for good, bytes included. Cannot be undone."""
    try:
        result, keys = await drive.delete_forever(session, user, data.file_ids, data.folder_ids)
        await drive.save(session, settings, user, keys)  # objects go after the commit
    except ServiceError as exc:
        raise to_http(exc) from exc
    return outcome(result)


@router.post("/trash/empty", response_model=Outcome)
async def empty_trash(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    try:
        result, keys = await drive.empty_trash(session, user)
        await drive.save(session, settings, user, keys)
    except ServiceError as exc:
        raise to_http(exc) from exc
    return outcome(result)
