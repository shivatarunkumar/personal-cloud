"""Give an account a temporary password (there is no email, and no admin).

    make reset-password LOGIN=<email or username>

Prompts for the new password. The person is asked to choose their own at the next sign-in,
and every device they were signed in on is signed out.
"""

from __future__ import annotations

import asyncio
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import or_, select  # noqa: E402

from app.core import security  # noqa: E402
from app.core.db import get_sessionmaker  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.auth import MIN_PASSWORD  # noqa: E402
from app.services.auth import revoke_all  # noqa: E402


async def main(login: str) -> int:
    login = login.strip().lower()
    async with get_sessionmaker()() as session:
        user = await session.scalar(select(User).where(or_(User.email == login, User.username == login)))
        if user is None:
            print(f"No account with the email or username '{login}'.", file=sys.stderr)
            return 1
        print(f"Account: {user.display_name} <{user.email}> (username {user.username})")
        password = getpass.getpass("Temporary password: ")
        if len(password) < MIN_PASSWORD:
            print(f"Use at least {MIN_PASSWORD} characters.", file=sys.stderr)
            return 1
        if getpass.getpass("Again: ") != password:
            print("The two passwords don't match.", file=sys.stderr)
            return 1
        user.password_hash = security.hash_password(password)
        user.password_changed_at = datetime.now(UTC)
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        await revoke_all(session, user.id, reason="password_reset")
        await session.commit()
    print("Done. They sign in with the temporary password and then choose their own.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: make reset-password LOGIN=<email or username>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
