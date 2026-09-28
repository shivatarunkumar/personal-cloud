"""Bring the bucket in line with the database: every object at its readable path.

    make relayout                           # move objects, write folder markers
    make relayout ARGS=--prune-missing      # also forget files whose object is gone

The app does this itself after every change; run it once after upgrading from the old
id-based layout (u/{user id}/f/{file id}), or to check the bucket. Files whose object can't
be found are listed; --prune-missing removes them from the drive (their bytes are already
gone, e.g. deleted in the Cloud console).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.adapters.storage import get_storage  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import get_engine, get_sessionmaker  # noqa: E402
from app.models.drive import File  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import drive  # noqa: E402
from app.services.errors import ServiceError  # noqa: E402

log = logging.getLogger("cloud.relayout")


async def main(prune: bool) -> int:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S"
    )
    storage = get_storage()
    problems = 0
    async with get_sessionmaker()() as session:
        for user in list(await session.scalars(select(User).order_by(User.username))):
            # which objects exist: listings only, no bytes
            existing: set[str] = set()
            for prefix in (
                drive.user_root(settings, user),
                drive.trash_root(settings, user),
                f"{settings.gcs_prefix}u/{user.id}/",
            ):
                existing |= {
                    b.name for b in await asyncio.to_thread(lambda p=prefix: list(storage.list_objects(p)))
                }
            files = list(
                await session.scalars(select(File).where(File.user_id == user.id, File.status == "ready"))
            )
            missing = [f for f in files if f.gcs_key not in existing]
            log.info(
                "%s: %d file(s), %d object(s) found, %d missing",
                user.username,
                len(files),
                len(existing),
                len(missing),
            )
            for f in missing[:20]:
                log.warning("  missing: %s (%s)", f.name, f.gcs_key)
            if len(missing) > 20:
                log.warning("  … and %d more", len(missing) - 20)

            if missing and prune:
                await session.execute(delete(File).where(File.id.in_([f.id for f in missing])))
                await drive.add_usage(session, user.id, -sum(f.size_bytes for f in missing))
                log.info("%s: forgot %d file(s) whose object is gone", user.username, len(missing))
            elif missing:
                problems += len(missing)
                log.warning(
                    "%s: skipped (missing objects can't be moved). Re-run with ARGS=--prune-missing",
                    user.username,
                )
                await session.rollback()
                continue

            try:
                stale = await drive.sync_bucket(session, settings, user)
            except ServiceError as exc:
                log.error("%s: %s", user.username, exc.message)
                await session.rollback()
                problems += 1
                continue
            await session.commit()
            await drive.drop_stale(session, user, stale)
            log.info("%s: bucket up to date (%d old object(s) removed)", user.username, len(stale))
    await get_engine().dispose()
    return 1 if problems else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--prune-missing", action="store_true", help="forget files whose object is gone")
    raise SystemExit(asyncio.run(main(parser.parse_args().prune_missing)))
