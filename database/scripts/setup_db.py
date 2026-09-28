"""Set up the whole Personal Cloud database in one command.

Runs, in order, skipping whatever already exists:

    1. init_db.sql   role, database, privileges, extensions   (as the admin user)
    2. migrations    every pending V<NNN>__*.sql file          (as the app user)
    3. verify        report tables and migrations

Safe to run any number of times: existing objects are left alone.

    python database/scripts/setup_db.py                 # create / update everything
    python database/scripts/setup_db.py --reset --yes   # DROP the database first (APP_ENV=local)

Connection settings come from .env (DATABASE_URL, POSTGRES_ADMIN_URL).
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import MIGRATIONS_DIR, sync_url, url_host, url_parts  # noqa: E402
from migrate import MigrationError  # noqa: E402
from migrate import run as run_migrations  # noqa: E402

INIT_DB_SQL = REPO_ROOT / "database" / "postgres" / "init_db.sql"

log = logging.getLogger("setup_db")


class SetupError(Exception):
    """Something the user can fix, reported without a traceback."""


def resolve_urls() -> tuple[str, str]:
    database_url = os.environ.get("DATABASE_URL")
    admin_url = os.environ.get("POSTGRES_ADMIN_URL")
    if not database_url:
        raise SetupError("DATABASE_URL is not set: copy .env.example to .env and fill it in")
    if not admin_url:
        raise SetupError("POSTGRES_ADMIN_URL is not set: copy .env.example to .env and fill it in")
    database_url, admin_url = sync_url(database_url), sync_url(admin_url)
    user, password, name = url_parts(database_url)
    if not (user and password and name):
        raise SetupError("DATABASE_URL needs a user, a password and a database name")
    if password == "change-me":
        log.warning("DATABASE_URL still has the example password 'change-me': choose your own in .env")
    os.environ["DATABASE_URL"] = database_url  # migrate.py reads this
    return admin_url, database_url


def connect(url: str, *, autocommit: bool = False, what: str = "database") -> psycopg.Connection:
    try:
        return psycopg.connect(url, autocommit=autocommit, connect_timeout=10)
    except psycopg.OperationalError as exc:
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else exc
        hint = "is PostgreSQL running (brew services start postgresql@17), and is the host/port right?"
        if "password authentication failed" in str(exc) or "does not exist" in str(exc):
            hint = "check the user and password in DATABASE_URL / POSTGRES_ADMIN_URL"
        raise SetupError(f"cannot connect to the {what} at {url_host(url)}: {detail}\n  → {hint}") from exc


def wait_for_server(admin_url: str, seconds: int) -> None:
    deadline = time.monotonic() + seconds
    first = True
    while True:
        try:
            with connect(admin_url, autocommit=True, what="server"):
                return
        except SetupError:
            if time.monotonic() >= deadline:
                raise
            if first:
                log.info("waiting for PostgreSQL at %s …", url_host(admin_url))
                first = False
            time.sleep(2)


def step_reset(admin_url: str, db_name: str, confirmed: bool) -> None:
    if os.environ.get("APP_ENV", "local") != "local":
        raise SetupError("--reset only runs with APP_ENV=local")
    if not confirmed:
        raise SetupError(f"--reset deletes the '{db_name}' database and all its data; add --yes")
    log.warning("dropping database %s (files in the bucket are NOT touched)", db_name)
    with connect(admin_url, autocommit=True, what="server") as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name)))


def step_init_db(admin_url: str, db_user: str, db_password: str, db_name: str) -> None:
    if not shutil.which("psql"):
        raise SetupError("psql is not installed, and init_db.sql needs it.\n  → brew install postgresql@17")
    log.info("step 1/2: init_db.sql (role %s, database %s)", db_user, db_name)
    result = subprocess.run(
        [
            "psql",
            admin_url,
            "--no-psqlrc",
            "--quiet",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"db_name={db_name}",
            "-v",
            f"db_user={db_user}",
            "-v",
            f"db_password={db_password}",
            "-f",
            str(INIT_DB_SQL),
        ],
        capture_output=True,
        text=True,
    )
    for line in (result.stdout + result.stderr).splitlines():
        line = line.strip()
        if not line or line.startswith("=="):
            continue
        (log.error if result.returncode else log.debug)("  %s", line)
    if result.returncode != 0:
        raise SetupError("init_db.sql failed (see the errors above)")


def step_migrate(database_url: str) -> None:
    log.info("step 2/2: migrations from %s", MIGRATIONS_DIR.relative_to(REPO_ROOT))
    try:
        run_migrations("up", url=database_url)
    except MigrationError as exc:
        raise SetupError(str(exc)) from exc
    except psycopg.Error as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise SetupError(f"migration failed: {first}") from exc


def step_verify(database_url: str, db_name: str) -> None:
    with connect(database_url) as conn:
        tables = conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'").fetchone()[0]
        count, latest = conn.execute(
            "SELECT count(*), coalesce(max(version), 0) FROM schema_migrations"
        ).fetchone()
        users = conn.execute("SELECT count(*) FROM users").fetchone()[0]
    log.info(
        "verify: database=%s tables=%d migrations=%d (latest V%03d) accounts=%d",
        db_name,
        tables,
        count,
        latest,
        users,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reset", action="store_true", help="DROP the database first (APP_ENV=local)")
    parser.add_argument("--yes", action="store_true", help="confirm --reset")
    parser.add_argument("--wait", type=int, default=20, metavar="SECONDS", help="wait for the server")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    started = time.monotonic()
    try:
        admin_url, database_url = resolve_urls()
        db_user, db_password, db_name = url_parts(database_url)
        log.info("database setup → %s@%s/%s", db_user, url_host(database_url), db_name)
        wait_for_server(admin_url, args.wait)
        if args.reset:
            step_reset(admin_url, db_name, args.yes)
        step_init_db(admin_url, db_user, db_password, db_name)
        step_migrate(database_url)
        step_verify(database_url, db_name)
        log.info("all done in %.1fs", time.monotonic() - started)
        return 0
    except SetupError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.error("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
