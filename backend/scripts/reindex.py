"""Rebuild the drive from what's in the bucket: a new machine, a lost database, or files
added straight to the bucket in the Cloud console.

    make reindex                     # add every object the database doesn't know about
    make reindex ARGS=--dry-run      # only say what would be added
    make reindex ARGS=--usage-only   # just recompute each account's used space

The bucket is laid out as {GCS_PREFIX}{username}/{folder path}/{file name}, so each
object's path says whose it is and where it goes. The account must exist with that
username (sign up first). Only object listings are read, never file contents.
Anything under _trash/ is left alone.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, update  # noqa: E402

from app.adapters.storage import get_storage  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import get_engine, get_sessionmaker  # noqa: E402
from app.models.drive import File, Folder  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import drive, kinds  # noqa: E402
from app.services.errors import ServiceError  # noqa: E402

log = logging.getLogger("cloud.reindex")


async def recompute_usage(session) -> None:
    totals = dict(
        (
            await session.execute(
                select(File.user_id, func.coalesce(func.sum(File.size_bytes), 0))
                .where(File.status == "ready")
                .group_by(File.user_id)
            )
        ).all()
    )
    for user in await session.scalars(select(User)):
        used = int(totals.get(user.id, 0))
        await session.execute(update(User).where(User.id == user.id).values(used_bytes=used))
        log.info("usage: %s %.2f GiB", user.username, used / 1024**3)
    await session.commit()


async def main(dry_run: bool, usage_only: bool) -> int:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S"
    )
    storage = get_storage()
    added = skipped = 0
    async with get_sessionmaker()() as session:
        if usage_only:
            await recompute_usage(session)
            return 0
        users = {u.username: u for u in await session.scalars(select(User))}
        known = set(await session.scalars(select(File.gcs_key)))
        known |= {k for k in await session.scalars(select(Folder.gcs_key)) if k}
        log.info("listing gs://%s/%s …", storage.bucket_name, settings.gcs_prefix)
        blobs = await asyncio.to_thread(lambda: list(storage.list_objects(settings.gcs_prefix)))
        for blob in sorted(blobs, key=lambda b: b.name):
            rel = blob.name[len(settings.gcs_prefix) :]
            owner, _, path = rel.partition("/")
            if blob.name in known or owner in {drive.TRASH_DIR, "_probe"} or not path:
                skipped += 1
                continue
            user = users.get(owner)
            if user is None:
                log.warning("skipped %s: no account with the username %r", blob.name, owner)
                skipped += 1
                continue
            is_folder = path.endswith("/")
            parts = path.strip("/").split("/")
            folder_parts = parts if is_folder else parts[:-1]
            try:
                for part in parts:
                    drive.clean_name(part)
            except ServiceError as exc:
                log.warning("skipped %s: %s", blob.name, exc.message)
                skipped += 1
                continue
            log.info("%s %s", "would add" if dry_run else "add", blob.name)
            added += 1
            if dry_run:
                continue
            folder_id = None
            if folder_parts:
                wanted = ["/".join(folder_parts[: i + 1]) for i in range(len(folder_parts))]
                folder_id = (await drive.ensure_folders(session, user, None, wanted))["/".join(folder_parts)]
            if not is_folder:
                name = parts[-1]
                content_type = kinds.content_type_for(name, blob.content_type)
                try:
                    file_id = uuid.UUID((blob.metadata or {}).get("pc-file-id", ""))
                except ValueError:
                    file_id = uuid.uuid4()
                if await session.get(File, file_id):
                    file_id = uuid.uuid4()
                taken = await drive.taken_names(session, user.id, folder_id)
                session.add(
                    File(
                        id=file_id,
                        user_id=user.id,
                        folder_id=folder_id,
                        name=drive.free_name(name, taken, is_file=True),
                        gcs_key=blob.name,
                        content_type=content_type,
                        kind=kinds.kind_for(name, content_type),
                        size_bytes=blob.size or 0,
                        status="ready",
                    )
                )
            await session.commit()
        if not dry_run:
            await recompute_usage(session)
            # new folders get their markers; a renamed duplicate moves to its new name
            for user in users.values():
                stale = await drive.sync_bucket(session, settings, user)
                await session.commit()
                await drive.drop_stale(session, user, stale)
    await get_engine().dispose()
    log.info("reindex done: %d added, %d skipped", added, skipped)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--usage-only", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.dry_run, args.usage_only)))
