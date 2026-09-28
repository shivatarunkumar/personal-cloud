"""A person's drive: folders, files, moving things around, and the trash.

The bucket mirrors the drive with readable names (see "bucket layout" below):

    {GCS_PREFIX}{username}/{folder path}/{file name}      a file
    {GCS_PREFIX}{username}/{folder path}/                 a folder (empty marker object)
    {GCS_PREFIX}_trash/{username}/{deleted at}/{path}     anything in the trash

Every change is made in the database first; `sync_bucket` then moves whatever objects no
longer sit where they should (a server-side copy, so no bytes pass through this machine).
The old objects are deleted only after the database commit, so a failure never leaves a
file without its bytes.

Names are unique per folder across files and folders together, case-insensitively, the
way Finder and OneDrive behave. A clash on move, copy or restore becomes "name (1)".
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import StorageNotConfigured, get_storage
from app.core.config import Settings
from app.models.drive import File, FileEvent, Folder
from app.models.user import User
from app.services.errors import NotFound, ServiceError

log = logging.getLogger("cloud.drive")

# most items one move, copy or delete may touch, folder contents included
MAX_ITEMS = 5000
SORTS = {"name", "modified", "size"}


# ------------------------------------------------------------------ helpers
async def run_storage(call, *args):
    """Run a blocking storage call in a thread, with errors the person can act on."""
    try:
        return await asyncio.to_thread(call, *args)
    except StorageNotConfigured as exc:
        raise ServiceError(str(exc), status_code=503) from exc
    except ServiceError:
        raise
    except Exception as exc:
        log.warning(
            "storage call %s failed: %s: %s", getattr(call, "__name__", call), type(exc).__name__, exc
        )
        raise ServiceError(
            "Google Cloud Storage refused the request. Run `make check-gcp` on the server to see why.",
            status_code=502,
        ) from exc


def clean_name(name: str) -> str:
    name = name.replace("\x00", "").strip()
    if not name or name in {".", ".."}:
        raise ServiceError("Enter a name", field="name")
    if "/" in name:
        raise ServiceError("A name can't contain /", field="name")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ServiceError("A name can't contain control characters", field="name")
    if len(name) > 255:
        raise ServiceError("Names can be up to 255 characters", field="name")
    return name


def free_name(name: str, taken: set[str], is_file: bool) -> str:
    """`name`, or "name (1)", "name (2)" … whichever isn't taken (the extension stays last)."""
    if name.lower() not in taken:
        return name
    stem, dot, ext = name.rpartition(".") if is_file and "." in name.lstrip(".") else (name, "", "")
    numbered = re.fullmatch(r"(.*) \((\d+)\)", stem)
    if numbered:
        stem = numbered.group(1)
    n = int(numbered.group(2)) + 1 if numbered else 1
    while f"{stem} ({n}){dot}{ext}".lower() in taken:
        n += 1
    return f"{stem} ({n}){dot}{ext}"


async def taken_names(session: AsyncSession, user_id: uuid.UUID, parent_id: uuid.UUID | None) -> set[str]:
    """Lowercased names of every live folder and file (uploads in progress too) in a folder."""
    folders = await session.scalars(
        select(Folder.name).where(
            Folder.user_id == user_id, in_folder(Folder.parent_id, parent_id), Folder.trashed_at.is_(None)
        )
    )
    files = await session.scalars(
        select(File.name).where(
            File.user_id == user_id, in_folder(File.folder_id, parent_id), File.trashed_at.is_(None)
        )
    )
    return {n.lower() for n in [*folders, *files]}


def in_folder(column, folder_id: uuid.UUID | None):
    """SQL condition: `column` points at this folder (None = the root)."""
    return column.is_(None) if folder_id is None else column == folder_id


def child_path(parent: Folder | None, name: str) -> str:
    return f"{parent.path}/{name}" if parent else name


# ------------------------------------------------------------------ bucket layout
# Usernames start with a letter or digit, so "_trash/" can never be someone's folder.
TRASH_DIR = "_trash"
MAX_KEY_BYTES = 1024  # GCS's limit on an object name


def user_root(settings: Settings, user: User) -> str:
    return f"{settings.gcs_prefix}{user.username}/"


def trash_root(settings: Settings, user: User) -> str:
    return f"{settings.gcs_prefix}{TRASH_DIR}/{user.username}/"


def _joined(folder_path: str, name: str) -> str:
    return f"{folder_path}/{name}" if folder_path else name


def _stamp(when: datetime) -> str:
    # microseconds keep two deletions of the same path apart
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%S-%f")


def live_file_key(settings: Settings, user: User, folder_path: str, name: str) -> str:
    return user_root(settings, user) + _joined(folder_path, name)


def _file_key(settings: Settings, user: User, file: File, folder_path: str) -> str:
    """Where this file's object should be now."""
    if file.status != "ready":
        return file.gcs_key  # the browser is writing to it: leave it alone until complete
    if file.trashed_at is None:
        return live_file_key(settings, user, folder_path, file.name)
    if file.gcs_key.startswith(trash_root(settings, user)):
        return file.gcs_key  # already in the trash: it stays put until restored or deleted
    return f"{trash_root(settings, user)}{_stamp(file.trashed_at)}/{_joined(folder_path, file.name)}"


def _folder_key(settings: Settings, user: User, folder: Folder) -> str:
    if folder.trashed_at is None:
        return f"{user_root(settings, user)}{folder.path}/"
    if folder.gcs_key and folder.gcs_key.startswith(trash_root(settings, user)):
        return folder.gcs_key
    return f"{trash_root(settings, user)}{_stamp(folder.trashed_at)}/{folder.path}/"


def object_metadata(file: File) -> dict[str, str]:
    """The id rides along, so `make reindex` can rebuild a lost database with the same ids."""
    return {"pc-file-id": str(file.id)}


async def lock_user(session: AsyncSession, user: User) -> None:
    """One change at a time per account (until the transaction ends), so two requests
    never move the same objects at once."""
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(str(user.id)))))


async def sync_bucket(session: AsyncSession, settings: Settings, user: User) -> list[str]:
    """Move every object that isn't where the database says it should be.

    Call it after a change and before the commit. It copies objects to their new names
    (in parallel, server side), writes missing folder markers, and records the new keys.
    Returns the old keys, to delete *after* the commit. If a copy fails, the copies made so
    far are removed and nothing is recorded, so the caller's transaction rolls back cleanly.
    """
    await lock_user(session, user)
    fresh = {"populate_existing": True}
    root, trash = user_root(settings, user), trash_root(settings, user)
    starts_trash = lambda column: func.left(column, len(trash)) == trash  # noqa: E731

    # Postgres picks out only the rows that are out of place, so an account with 100,000
    # files costs one quick scan here, not 100,000 rows sent back to Python.
    live_key = func.concat(
        root, case((Folder.path.is_(None), File.name), else_=func.concat(Folder.path, "/", File.name))
    )
    rows = await session.execute(
        select(File, Folder.path)
        .outerjoin(Folder, Folder.id == File.folder_id)
        .where(
            File.user_id == user.id,
            File.status == "ready",
            or_(
                and_(File.trashed_at.is_(None), File.gcs_key != live_key),
                and_(File.trashed_at.is_not(None), ~starts_trash(File.gcs_key)),
            ),
        )
        .execution_options(**fresh)
    )
    file_moves = []
    for file, folder_path in rows.all():
        target = _file_key(settings, user, file, folder_path or "")
        if target != file.gcs_key:
            file_moves.append((file, file.gcs_key, target))

    stale_folders = await session.scalars(
        select(Folder)
        .where(
            Folder.user_id == user.id,
            or_(
                Folder.gcs_key.is_(None),
                and_(Folder.trashed_at.is_(None), Folder.gcs_key != func.concat(root, Folder.path, "/")),
                and_(Folder.trashed_at.is_not(None), ~starts_trash(Folder.gcs_key)),
            ),
        )
        .execution_options(**fresh)
    )
    markers = []
    for folder in stale_folders:
        target = _folder_key(settings, user, folder)
        if target != folder.gcs_key:
            markers.append((folder, folder.gcs_key, target))
    if not file_moves and not markers:
        return []

    for _, _, target in [*file_moves, *markers]:
        if len(target.encode()) > MAX_KEY_BYTES:
            raise ServiceError("That path is too long for cloud storage (1024 bytes). Use shorter names.")
    # never overwrite an object another file is about to be copied from
    sources = {old for _, old, _ in file_moves}
    clash = [new for _, old, new in file_moves if new in sources and new != old]
    if clash:
        log.error("bucket sync refused: %s would overwrite an object still being moved", clash[0])
        raise ServiceError(
            "Cloud storage is busy with an earlier change. Try again in a moment.", status_code=409
        )

    storage = get_storage()
    limit = asyncio.Semaphore(16)
    written: list[str] = []

    async def move_one(file: File, old: str, new: str) -> None:
        async with limit:
            await run_storage(storage.copy, old, new, object_metadata(file))
            written.append(new)

    async def mark_one(new: str) -> None:
        async with limit:
            await run_storage(storage.write_empty, new, "application/x-directory", {})
            written.append(new)

    results = await asyncio.gather(
        *[move_one(f, old, new) for f, old, new in file_moves],
        *[mark_one(new) for _, _, new in markers],
        return_exceptions=True,
    )
    failures = [r for r in results if isinstance(r, BaseException)]
    if failures:
        await drop_objects(written)
        raise failures[0]

    for file, _, new in file_moves:
        file.gcs_key = new
    for folder, _, new in markers:
        folder.gcs_key = new
    await session.flush()
    log.info("bucket: moved %d file(s), wrote %d folder marker(s)", len(file_moves), len(markers))
    return [old for _, old, _ in file_moves] + [old for _, old, _ in markers if old]


def event(session: AsyncSession, user: User, action: str, *, file_id=None, folder_id=None, **detail) -> None:
    session.add(
        FileEvent(user_id=user.id, file_id=file_id, folder_id=folder_id, action=action, detail=detail)
    )


async def add_usage(session: AsyncSession, user_id: uuid.UUID, delta: int) -> None:
    if delta:
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(used_bytes=func.greatest(User.used_bytes + delta, 0))
        )


def check_quota(user: User, extra: int) -> None:
    if user.quota_bytes is not None and user.used_bytes + extra > user.quota_bytes:
        free = max(0, user.quota_bytes - user.used_bytes)
        raise ServiceError(
            f"Not enough space: this needs {extra:,} bytes and {free:,} are left", status_code=507
        )


# ------------------------------------------------------------------ lookups
async def get_folder(session: AsyncSession, user: User, folder_id: uuid.UUID | None) -> Folder | None:
    """A live folder the user owns; None means the root."""
    if folder_id is None:
        return None
    folder = await session.scalar(
        select(Folder).where(Folder.id == folder_id, Folder.user_id == user.id, Folder.trashed_at.is_(None))
    )
    if folder is None:
        raise NotFound("Folder")
    return folder


async def get_file(session: AsyncSession, user: User, file_id: uuid.UUID, *, trashed: bool = False) -> File:
    query = select(File).where(File.id == file_id, File.user_id == user.id, File.status == "ready")
    if not trashed:
        query = query.where(File.trashed_at.is_(None))
    file = await session.scalar(query)
    if file is None:
        raise NotFound("File")
    return file


async def folder_path_of(session: AsyncSession, folder_id: uuid.UUID | None) -> str:
    if folder_id is None:
        return ""
    return await session.scalar(select(Folder.path).where(Folder.id == folder_id)) or ""


async def breadcrumbs(session: AsyncSession, user: User, folder: Folder | None) -> list[Folder]:
    """The folder and every folder above it, root first."""
    chain: list[Folder] = []
    current = folder
    while current is not None:
        chain.append(current)
        if current.parent_id is None:
            break
        current = await session.get(Folder, current.parent_id)
    return list(reversed(chain))


def _order(sort: str, order: str, model):
    column = {"name": func.lower(model.name), "modified": model.updated_at}.get(sort)
    if sort == "size":
        column = model.size_bytes if model is File else func.lower(model.name)
    column = column if column is not None else func.lower(model.name)
    return column.desc() if order == "desc" else column.asc()


async def children(
    session: AsyncSession, user: User, folder: Folder | None, sort: str = "name", order: str = "asc"
) -> tuple[list[Folder], list[File]]:
    parent_id = folder.id if folder else None
    folders = await session.scalars(
        select(Folder)
        .where(
            Folder.user_id == user.id,
            in_folder(Folder.parent_id, parent_id),
            Folder.trashed_at.is_(None),
        )
        .order_by(_order(sort, order, Folder))
    )
    files = await session.scalars(
        select(File)
        .where(
            File.user_id == user.id,
            in_folder(File.folder_id, parent_id),
            File.trashed_at.is_(None),
            File.status == "ready",
        )
        .order_by(_order(sort, order, File))
    )
    return list(folders), list(files)


async def folder_counts(session: AsyncSession, folder_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many live items sit directly in each folder (shown as "12 items")."""
    if not folder_ids:
        return {}
    sub_folders = await session.execute(
        select(Folder.parent_id, func.count())
        .where(Folder.parent_id.in_(folder_ids), Folder.trashed_at.is_(None))
        .group_by(Folder.parent_id)
    )
    sub_files = await session.execute(
        select(File.folder_id, func.count())
        .where(File.folder_id.in_(folder_ids), File.trashed_at.is_(None), File.status == "ready")
        .group_by(File.folder_id)
    )
    counts: dict[uuid.UUID, int] = dict.fromkeys(folder_ids, 0)
    for folder_id, n in [*sub_folders, *sub_files]:
        counts[folder_id] += n
    return counts


async def subfolders(session: AsyncSession, user: User, parent_id: uuid.UUID | None) -> list[Folder]:
    """Live folders in a folder, for the move/copy picker."""
    rows = await session.scalars(
        select(Folder)
        .where(
            Folder.user_id == user.id,
            in_folder(Folder.parent_id, parent_id),
            Folder.trashed_at.is_(None),
        )
        .order_by(func.lower(Folder.name))
    )
    return list(rows)


# ------------------------------------------------------------------ views
def _ready(user: User):
    return and_(File.user_id == user.id, File.status == "ready", File.trashed_at.is_(None))


async def recent(session: AsyncSession, user: User, limit: int = 100) -> list[File]:
    rows = await session.scalars(
        select(File)
        .where(_ready(user))
        .order_by(func.greatest(File.updated_at, func.coalesce(File.last_opened_at, File.updated_at)).desc())
        .limit(limit)
    )
    return list(rows)


async def by_kind(
    session: AsyncSession, user: User, kind: str, limit: int = 500, offset: int = 0
) -> list[File]:
    rows = await session.scalars(
        select(File)
        .where(_ready(user), File.kind == kind)
        .order_by(File.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(rows)


async def search(
    session: AsyncSession, user: User, q: str, limit: int = 200
) -> tuple[list[Folder], list[File]]:
    q = q.strip()
    if not q:
        return [], []
    pattern = f"%{q.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_')}%"
    folders = await session.scalars(
        select(Folder)
        .where(Folder.user_id == user.id, Folder.trashed_at.is_(None), Folder.name.ilike(pattern))
        .order_by(func.lower(Folder.name))
        .limit(limit)
    )
    files = await session.scalars(
        select(File)
        .where(_ready(user), File.name.ilike(pattern))
        .order_by(File.updated_at.desc())
        .limit(limit)
    )
    return list(folders), list(files)


async def folder_paths(session: AsyncSession, folder_ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    ids = [i for i in folder_ids if i]
    if not ids:
        return {}
    rows = await session.execute(select(Folder.id, Folder.path).where(Folder.id.in_(ids)))
    return dict(rows.all())


async def usage(session: AsyncSession, user: User) -> dict:
    rows = await session.execute(
        select(File.kind, func.count(), func.coalesce(func.sum(File.size_bytes), 0))
        .where(_ready(user))
        .group_by(File.kind)
    )
    trash = await session.scalar(
        select(func.coalesce(func.sum(File.size_bytes), 0)).where(
            File.user_id == user.id, File.status == "ready", File.trashed_at.is_not(None)
        )
    )
    return {
        "used_bytes": user.used_bytes,
        "quota_bytes": user.quota_bytes,
        "trash_bytes": int(trash or 0),
        "by_kind": {kind: {"count": n, "bytes": int(size)} for kind, n, size in rows},
    }


# ------------------------------------------------------------------ folders
async def create_folder(session: AsyncSession, user: User, parent_id: uuid.UUID | None, name: str) -> Folder:
    parent = await get_folder(session, user, parent_id)
    name = clean_name(name)
    if name.lower() in await taken_names(session, user.id, parent_id):
        raise ServiceError(f"There's already something called “{name}” here", status_code=409, field="name")
    folder = Folder(user_id=user.id, parent_id=parent_id, name=name, path=child_path(parent, name))
    session.add(folder)
    await session.flush()
    event(session, user, "create_folder", folder_id=folder.id, path=folder.path)
    return folder


async def ensure_folders(
    session: AsyncSession, user: User, parent_id: uuid.UUID | None, paths: list[str]
) -> dict[str, uuid.UUID]:
    """Create the folders of a dropped folder tree, reusing any that already exist.

    `paths` are relative ("Trip", "Trip/Day 1"). Returns path → folder id for each one, so
    the browser can upload every file straight into its folder. An existing *file* with a
    folder's name is a clash, and the folder becomes "name (1)".
    """
    parent = await get_folder(session, user, parent_id)
    wanted = sorted({p.strip("/") for p in paths if p.strip("/")}, key=lambda p: p.count("/"))
    if len(wanted) > MAX_ITEMS:
        raise ServiceError(f"That's more than {MAX_ITEMS} folders in one go")
    result: dict[str, uuid.UUID] = {}
    by_path: dict[str, Folder | None] = {"": parent}
    for rel in wanted:
        head, _, leaf = rel.rpartition("/")
        leaf = clean_name(leaf)
        above = by_path.get(head)
        if head and above is None:
            continue  # its parent was invalid; skipped with it
        above_id = above.id if above else None
        existing = await session.scalar(
            select(Folder).where(
                Folder.user_id == user.id,
                in_folder(Folder.parent_id, above_id),
                func.lower(Folder.name) == leaf.lower(),
                Folder.trashed_at.is_(None),
            )
        )
        if existing is None:
            name = free_name(leaf, await taken_names(session, user.id, above_id), is_file=False)
            existing = Folder(user_id=user.id, parent_id=above_id, name=name, path=child_path(above, name))
            session.add(existing)
            await session.flush()
            event(session, user, "create_folder", folder_id=existing.id, path=existing.path)
        by_path[rel] = existing
        result[rel] = existing.id
    return result


async def _rewrite_paths(session: AsyncSession, ids: list[uuid.UUID], old: str, new: str) -> None:
    """A folder moved or was renamed: it and every folder beneath it (`ids`) get the new prefix."""
    await session.execute(
        update(Folder)
        .where(Folder.id.in_(ids))
        .values(path=func.concat(new, func.substr(Folder.path, len(old) + 1)))
        .execution_options(synchronize_session=False)
    )


async def subtree_ids(session: AsyncSession, user: User, folder: Folder) -> list[uuid.UUID]:
    """The folder and every folder beneath it, by following parent links.

    Not by path: a folder in the trash and a live one can have the same path ("A" deleted,
    then a new "A" made), and each must only ever touch its own contents.
    """
    tree = select(Folder.id).where(Folder.id == folder.id, Folder.user_id == user.id).cte(recursive=True)
    tree = tree.union_all(select(Folder.id).where(Folder.parent_id == tree.c.id))
    return list(await session.scalars(select(tree.c.id)))


async def rename_folder(session: AsyncSession, user: User, folder_id: uuid.UUID, name: str) -> Folder:
    folder = await get_folder(session, user, folder_id)
    assert folder is not None
    name = clean_name(name)
    if name == folder.name:
        return folder
    taken = await taken_names(session, user.id, folder.parent_id)
    if name.lower() != folder.name.lower() and name.lower() in taken:
        raise ServiceError(f"There's already something called “{name}” here", status_code=409, field="name")
    old_path = folder.path
    parent = await session.get(Folder, folder.parent_id) if folder.parent_id else None
    new_path = child_path(parent, name)
    ids = await subtree_ids(session, user, folder)
    folder.name = name
    await session.flush()
    await _rewrite_paths(session, ids, old_path, new_path)
    await session.refresh(folder)
    event(session, user, "rename", folder_id=folder.id, before=old_path, after=new_path)
    return folder


async def rename_file(session: AsyncSession, user: User, file_id: uuid.UUID, name: str) -> File:
    file = await get_file(session, user, file_id)
    name = clean_name(name)
    if name == file.name:
        return file
    taken = await taken_names(session, user.id, file.folder_id)
    if name.lower() != file.name.lower() and name.lower() in taken:
        raise ServiceError(f"There's already something called “{name}” here", status_code=409, field="name")
    before = file.name
    file.name = name
    event(session, user, "rename", file_id=file.id, before=before, after=name)
    return file


# ------------------------------------------------------------------ move and copy
@dataclass
class Outcome:
    files: int = 0
    folders: int = 0
    renamed: list[str] = field(default_factory=list)
    # objects written by a copy: deleted again if the copy fails
    new_objects: list[str] = field(default_factory=list)


async def _selection(
    session: AsyncSession, user: User, file_ids: list[uuid.UUID], folder_ids: list[uuid.UUID]
) -> tuple[list[File], list[Folder]]:
    if len(file_ids) + len(folder_ids) > MAX_ITEMS:
        raise ServiceError(f"Select at most {MAX_ITEMS} items")
    files = [await get_file(session, user, i) for i in dict.fromkeys(file_ids)]
    folders = [f for f in [await get_folder(session, user, i) for i in dict.fromkeys(folder_ids)] if f]
    return files, folders


async def move(
    session: AsyncSession,
    user: User,
    file_ids: list[uuid.UUID],
    folder_ids: list[uuid.UUID],
    target_id: uuid.UUID | None,
) -> Outcome:
    target = await get_folder(session, user, target_id)
    files, folders = await _selection(session, user, file_ids, folder_ids)
    out = Outcome()
    taken = await taken_names(session, user.id, target_id)

    for folder in folders:
        if folder.parent_id == target_id:
            continue
        if target and (target.id == folder.id or target.path.startswith(f"{folder.path}/")):
            raise ServiceError(f"“{folder.name}” can't go inside itself")
        name = free_name(folder.name, taken, is_file=False)
        if name != folder.name:
            out.renamed.append(f"{folder.name} → {name}")
        taken.add(name.lower())
        old_path, new_path = folder.path, child_path(target, name)
        ids = await subtree_ids(session, user, folder)
        folder.parent_id, folder.name = target_id, name
        await session.flush()
        await _rewrite_paths(session, ids, old_path, new_path)
        event(session, user, "move", folder_id=folder.id, before=old_path, after=new_path)
        out.folders += 1

    for file in files:
        if file.folder_id == target_id:
            continue
        name = free_name(file.name, taken, is_file=True)
        if name != file.name:
            out.renamed.append(f"{file.name} → {name}")
        taken.add(name.lower())
        event(session, user, "move", file_id=file.id, name=name, to=target.path if target else "")
        file.folder_id, file.name = target_id, name
        out.files += 1
    await session.flush()
    return out


async def _copy_file(
    session: AsyncSession,
    settings: Settings,
    user: User,
    source: File,
    target: Folder | None,
    name: str,
    out: Outcome,
) -> None:
    new = File(
        id=uuid.uuid4(),
        user_id=user.id,
        folder_id=target.id if target else None,
        name=name,
        content_type=source.content_type,
        kind=source.kind,
        size_bytes=source.size_bytes,
        status="ready",
    )
    new.gcs_key = live_file_key(settings, user, target.path if target else "", name)
    await run_storage(get_storage().copy, source.gcs_key, new.gcs_key, object_metadata(new))
    out.new_objects.append(new.gcs_key)
    session.add(new)
    event(session, user, "copy", file_id=new.id, source=str(source.id))
    out.files += 1


async def copy(
    session: AsyncSession,
    settings: Settings,
    user: User,
    file_ids: list[uuid.UUID],
    folder_ids: list[uuid.UUID],
    target_id: uuid.UUID | None,
) -> Outcome:
    """Copies are real, independent files (a server-side copy in the bucket)."""
    target = await get_folder(session, user, target_id)
    files, folders = await _selection(session, user, file_ids, folder_ids)
    out = Outcome()

    # everything that will be copied, for the size limits and the quota
    plan: list[tuple[Folder, list[Folder], list[File]]] = []
    total_files, total_bytes = len(files), sum(f.size_bytes for f in files)
    for folder in folders:
        if target and (target.id == folder.id or target.path.startswith(f"{folder.path}/")):
            raise ServiceError(f"“{folder.name}” can't be copied inside itself")
        tree = list(
            await session.scalars(
                select(Folder)
                .where(Folder.id.in_(await subtree_ids(session, user, folder)), Folder.trashed_at.is_(None))
                .order_by(Folder.path)
            )
        )
        inner = list(
            await session.scalars(
                select(File).where(
                    File.folder_id.in_([f.id for f in tree]),
                    File.trashed_at.is_(None),
                    File.status == "ready",
                )
            )
        )
        plan.append((folder, tree, inner))
        total_files += len(inner)
        total_bytes += sum(f.size_bytes for f in inner)
    if total_files > MAX_ITEMS:
        raise ServiceError(f"That's {total_files} files; copy at most {MAX_ITEMS} at a time")
    check_quota(user, total_bytes)

    taken = await taken_names(session, user.id, target_id)
    try:
        for source in files:
            name = free_name(source.name, taken, is_file=True)
            taken.add(name.lower())
            await _copy_file(session, settings, user, source, target, name, out)

        for folder, tree, inner in plan:
            root_name = free_name(folder.name, taken, is_file=False)
            taken.add(root_name.lower())
            mapping: dict[uuid.UUID, Folder] = {}
            for original in tree:  # ordered by path: parents come first
                if original.id == folder.id:
                    parent, name = target, root_name
                else:
                    parent, name = mapping[original.parent_id], original.name
                clone = Folder(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    parent_id=parent.id if parent else None,
                    name=name,
                    path=child_path(parent, name),
                )
                session.add(clone)
                mapping[original.id] = clone
                out.folders += 1
            await session.flush()
            for source in inner:
                await _copy_file(session, settings, user, source, mapping[source.folder_id], source.name, out)
        await add_usage(session, user.id, total_bytes)
        await session.flush()
    except Exception:
        await drop_objects(out.new_objects)
        raise
    return out


async def drop_objects(keys: list[str]) -> None:
    """Best effort: the database is already right, a leftover object only costs storage."""
    storage = get_storage()
    for key in keys:
        try:
            await asyncio.to_thread(storage.delete, key)
        except Exception as exc:
            log.warning("could not delete object %s: %s", key, exc)


# ------------------------------------------------------------------ trash
async def trash(
    session: AsyncSession, user: User, file_ids: list[uuid.UUID], folder_ids: list[uuid.UUID]
) -> Outcome:
    files, folders = await _selection(session, user, file_ids, folder_ids)
    now = datetime.now(UTC)
    out = Outcome()
    for folder in folders:
        tree = await subtree_ids(session, user, folder)
        # everything beneath shares the timestamp, so a restore brings exactly it back
        await session.execute(
            update(Folder).where(Folder.id.in_(tree), Folder.trashed_at.is_(None)).values(trashed_at=now)
        )
        await session.execute(
            update(File).where(File.folder_id.in_(tree), File.trashed_at.is_(None)).values(trashed_at=now)
        )
        await session.execute(update(Folder).where(Folder.id == folder.id).values(trash_root=True))
        event(session, user, "trash", folder_id=folder.id, path=folder.path)
        out.folders += 1
    for file in files:
        if file.trashed_at is not None:  # it was inside a folder trashed just above
            continue
        file.trashed_at, file.trash_root = now, True
        event(session, user, "trash", file_id=file.id, name=file.name)
        out.files += 1
    await session.flush()
    return out


async def list_trash(session: AsyncSession, user: User) -> tuple[list[Folder], list[File]]:
    folders = await session.scalars(
        select(Folder)
        .where(Folder.user_id == user.id, Folder.trash_root, Folder.trashed_at.is_not(None))
        .order_by(Folder.trashed_at.desc())
    )
    files = await session.scalars(
        select(File)
        .where(File.user_id == user.id, File.trash_root, File.trashed_at.is_not(None), File.status == "ready")
        .order_by(File.trashed_at.desc())
    )
    return list(folders), list(files)


async def _trashed_roots(
    session: AsyncSession, user: User, file_ids: list[uuid.UUID], folder_ids: list[uuid.UUID]
) -> tuple[list[File], list[Folder]]:
    files = (
        list(
            await session.scalars(
                select(File).where(File.id.in_(file_ids), File.user_id == user.id, File.trash_root)
            )
        )
        if file_ids
        else []
    )
    folders = (
        list(
            await session.scalars(
                select(Folder).where(Folder.id.in_(folder_ids), Folder.user_id == user.id, Folder.trash_root)
            )
        )
        if folder_ids
        else []
    )
    if len(files) != len(set(file_ids)) or len(folders) != len(set(folder_ids)):
        raise NotFound("Item in the trash")
    return files, folders


async def restore(
    session: AsyncSession, user: User, file_ids: list[uuid.UUID], folder_ids: list[uuid.UUID]
) -> Outcome:
    """Back where it was; to My files if that folder is gone. A taken name gets a number."""
    files, folders = await _trashed_roots(session, user, file_ids, folder_ids)
    out = Outcome()

    async def live_parent(parent_id: uuid.UUID | None) -> Folder | None:
        if parent_id is None:
            return None
        return await session.scalar(select(Folder).where(Folder.id == parent_id, Folder.trashed_at.is_(None)))

    for folder in folders:
        stamp = folder.trashed_at
        parent = await live_parent(folder.parent_id)
        name = free_name(
            folder.name, await taken_names(session, user.id, parent.id if parent else None), False
        )
        if name != folder.name:
            out.renamed.append(f"{folder.name} → {name}")
        old_path, new_path = folder.path, child_path(parent, name)
        tree = await subtree_ids(session, user, folder)
        # the folder itself first, under its free name: it must never be live with a taken one
        await session.execute(
            update(Folder)
            .where(Folder.id == folder.id)
            .values(trashed_at=None, trash_root=False, parent_id=parent.id if parent else None, name=name)
        )
        await session.execute(
            update(Folder)
            .where(Folder.id.in_(tree), Folder.id != folder.id, Folder.trashed_at == stamp)
            .values(trashed_at=None, trash_root=False)
        )
        await session.execute(
            update(File)
            .where(File.folder_id.in_(tree), File.trashed_at == stamp)
            .values(trashed_at=None, trash_root=False)
        )
        if new_path != old_path:
            await _rewrite_paths(session, tree, old_path, new_path)
        await session.refresh(folder)
        event(session, user, "restore", folder_id=folder.id, path=new_path)
        out.folders += 1

    for file in files:
        parent = await live_parent(file.folder_id)
        name = free_name(file.name, await taken_names(session, user.id, parent.id if parent else None), True)
        if name != file.name:
            out.renamed.append(f"{file.name} → {name}")
        file.folder_id, file.name = (parent.id if parent else None), name
        file.trashed_at, file.trash_root = None, False
        event(session, user, "restore", file_id=file.id, name=name)
        out.files += 1
    await session.flush()
    return out


async def delete_forever(
    session: AsyncSession, user: User, file_ids: list[uuid.UUID], folder_ids: list[uuid.UUID]
) -> tuple[Outcome, list[str]]:
    """Remove trashed items for good. Returns the object keys to delete once committed."""
    files, folders = await _trashed_roots(session, user, file_ids, folder_ids)
    out = Outcome()
    by_id: dict[uuid.UUID, File] = {f.id: f for f in files}
    markers: list[str] = []
    for folder in folders:
        tree = await subtree_ids(session, user, folder)
        for file in await session.scalars(select(File).where(File.folder_id.in_(tree))):
            by_id[file.id] = file
        markers.extend(
            k for k in await session.scalars(select(Folder.gcs_key).where(Folder.id.in_(tree))) if k
        )
        out.folders += 1
    doomed = list(by_id.values())
    freed = sum(f.size_bytes for f in doomed if f.status == "ready")
    keys = [f.gcs_key for f in doomed] + markers
    for file in doomed:
        event(session, user, "delete", file_id=file.id, name=file.name, bytes=file.size_bytes)
    if doomed:
        await session.execute(delete(File).where(File.id.in_([f.id for f in doomed])))
    if folders:
        # child folders and their files go with them (ON DELETE CASCADE)
        await session.execute(delete(Folder).where(Folder.id.in_([f.id for f in folders])))
    await add_usage(session, user.id, -freed)
    out.files = len(doomed)
    await session.flush()
    return out, keys


async def empty_trash(session: AsyncSession, user: User) -> tuple[Outcome, list[str]]:
    folders, files = await list_trash(session, user)
    return await delete_forever(session, user, [f.id for f in files], [f.id for f in folders])


async def expired_trash(session: AsyncSession, retention_days: int) -> dict[uuid.UUID, tuple[list, list]]:
    """Per user, the trash roots older than the retention period."""
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    found: dict[uuid.UUID, tuple[list, list]] = {}
    for user_id, file_id in await session.execute(
        select(File.user_id, File.id).where(File.trash_root, File.trashed_at < cutoff)
    ):
        found.setdefault(user_id, ([], []))[0].append(file_id)
    for user_id, folder_id in await session.execute(
        select(Folder.user_id, Folder.id).where(Folder.trash_root, Folder.trashed_at < cutoff)
    ):
        found.setdefault(user_id, ([], []))[1].append(folder_id)
    return found


async def drop_stale(session: AsyncSession, user: User, keys: list[str]) -> None:
    """Delete objects left behind by a committed change.

    Under the account's lock, and only those no file or folder points at any more: a quick
    second change (rename a → b → a) may already have put something back at an old name.
    """
    if not keys:
        return
    await lock_user(session, user)
    used = set(await session.scalars(select(File.gcs_key).where(File.gcs_key.in_(keys))))
    used |= set(await session.scalars(select(Folder.gcs_key).where(Folder.gcs_key.in_(keys))))
    await drop_objects([k for k in dict.fromkeys(keys) if k not in used])
    await session.commit()  # releases the lock


async def save(session: AsyncSession, settings: Settings, user: User, extra_stale: list[str] = ()) -> None:
    """Commit a change: move objects to match it, commit, then delete what's left over."""
    stale = await sync_bucket(session, settings, user)
    await session.commit()
    await drop_stale(session, user, [*stale, *extra_stale])


async def existing_names(
    session: AsyncSession, user: User, items: list[tuple[uuid.UUID | None, str]]
) -> list[dict]:
    """Which of these (folder, name) pairs already hold a live file or folder.

    Used before an upload starts, so the person can choose Replace, Skip or Keep both.
    One query per kind, whatever the number of items.
    """
    if not items:
        return []
    wanted = {(folder_id, name.lower()) for folder_id, name in items}
    folder_ids = {f for f, _ in wanted if f is not None}
    names = {n for _, n in wanted}

    def place(column):
        return or_(column.is_(None), column.in_(folder_ids)) if folder_ids else column.is_(None)

    found: list[dict] = []
    for file in await session.scalars(
        select(File).where(
            File.user_id == user.id,
            File.trashed_at.is_(None),
            File.status == "ready",
            place(File.folder_id),
            func.lower(File.name).in_(names),
        )
    ):
        if (file.folder_id, file.name.lower()) in wanted:
            found.append(
                {
                    "folder_id": file.folder_id,
                    "name": file.name,
                    "type": "file",
                    "file_id": file.id,
                    "size_bytes": file.size_bytes,
                    "updated_at": file.updated_at,
                }
            )
    for folder in await session.scalars(
        select(Folder).where(
            Folder.user_id == user.id,
            Folder.trashed_at.is_(None),
            place(Folder.parent_id),
            func.lower(Folder.name).in_(names),
        )
    ):
        if (folder.parent_id, folder.name.lower()) in wanted:
            found.append(
                {
                    "folder_id": folder.parent_id,
                    "name": folder.name,
                    "type": "folder",
                    "file_id": None,
                    "size_bytes": None,
                    "updated_at": folder.updated_at,
                }
            )
    return found
