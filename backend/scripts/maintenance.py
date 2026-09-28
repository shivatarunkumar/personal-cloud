"""Housekeeping, run by cloud.sh (every 10 minutes under cron) or by hand:

    make maintenance

1. Items in the trash longer than TRASH_RETENTION_DAYS are deleted for good.
2. Uploads that were started but never finished, and whose session has expired, are
   forgotten (and any partial object removed).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.db import get_engine, get_sessionmaker  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import drive, uploads  # noqa: E402

log = logging.getLogger("cloud.maintenance")


async def main() -> int:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S"
    )
    purged_files = 0
    async with get_sessionmaker()() as session:
        expired = await drive.expired_trash(session, settings.trash_retention_days)
        for user_id, (file_ids, folder_ids) in expired.items():
            user = await session.get(User, user_id)
            if user is None:
                continue
            outcome, keys = await drive.delete_forever(session, user, file_ids, folder_ids)
            await session.commit()
            await drive.drop_objects(keys)
            purged_files += outcome.files
            log.info(
                "trash: deleted %d file(s) of %s older than %d days",
                outcome.files,
                user.username,
                settings.trash_retention_days,
            )

        keys = await uploads.stale(session)
        await session.commit()
        await drive.drop_objects(keys)
    await get_engine().dispose()
    log.info(
        "maintenance done: %d file(s) purged from trash, %d stale upload(s) removed", purged_files, len(keys)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
