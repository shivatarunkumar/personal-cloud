"""Apply versioned SQL migrations from database/postgres/migrations.

Files are named V<NNN>__<name>.sql and applied in version order, each in its own
transaction (unless the file starts with `-- migrate:no-transaction`). Applied
versions are recorded in schema_migrations with a SHA-256 checksum; editing an
applied file makes every later run fail, so schema changes are always a new file.

    python database/scripts/migrate.py up       # apply pending migrations
    python database/scripts/migrate.py status   # list applied / pending
"""

from __future__ import annotations

import argparse
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

from _db import MIGRATIONS_DIR, connect, database_url, fail, log

FILENAME_RE = re.compile(r"^V(\d{3,})__([a-z0-9_]+)\.sql$")
NO_TRANSACTION_MARKER = "-- migrate:no-transaction"
LOCK_ID = 5_106_224_871  # arbitrary; serializes concurrent migration runs

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version       integer PRIMARY KEY,
    name          text NOT NULL,
    checksum      text NOT NULL,
    applied_at    timestamptz NOT NULL DEFAULT now(),
    execution_ms  integer NOT NULL
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str

    @property
    def label(self) -> str:
        return self.path.name

    @property
    def no_transaction(self) -> bool:
        return self.sql.lstrip().startswith(NO_TRANSACTION_MARKER)


class MigrationError(Exception):
    pass


def load_migrations(directory: Path) -> list[Migration]:
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")
    migrations: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = FILENAME_RE.match(path.name)
        if not match:
            raise MigrationError(f"bad migration filename {path.name!r}; expected V<NNN>__<name>.sql")
        version, name = int(match.group(1)), match.group(2)
        if version in migrations:
            raise MigrationError(
                f"duplicate migration version {version}: {migrations[version].label} and {path.name}"
            )
        body = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
        migrations[version] = Migration(version, name, path, body, checksum)
    return [migrations[v] for v in sorted(migrations)]


def plan(migrations: list[Migration], applied: dict[int, tuple[str, str]]) -> list[Migration]:
    """Validate applied history against files and return the pending migrations."""
    by_version = {m.version: m for m in migrations}
    problems = []
    for version, (name, checksum) in sorted(applied.items()):
        migration = by_version.get(version)
        if migration is None:
            problems.append(f"V{version:03d}__{name}.sql was applied but the file is missing")
        elif migration.checksum != checksum:
            problems.append(
                f"{migration.label} was edited after it was applied (checksum mismatch); "
                "add a new migration instead"
            )
    if problems:
        raise MigrationError("\n  ".join(["migration history does not match files:", *problems]))

    pending = [m for m in migrations if m.version not in applied]
    latest_applied = max(applied, default=0)
    out_of_order = [m.label for m in pending if m.version < latest_applied]
    if out_of_order:
        raise MigrationError(
            f"pending migrations are older than the latest applied (V{latest_applied:03d}): "
            + ", ".join(out_of_order)
        )
    return pending


def fetch_applied(conn) -> dict[int, tuple[str, str]]:
    rows = conn.execute("SELECT version, name, checksum FROM schema_migrations").fetchall()
    return {version: (name, checksum) for version, name, checksum in rows}


def apply(conn, migration: Migration) -> None:
    started = time.monotonic()
    no_transaction = migration.no_transaction
    if no_transaction:
        conn.autocommit = True
        conn.execute(migration.sql)
        conn.autocommit = False
    record = "INSERT INTO schema_migrations (version, name, checksum, execution_ms) VALUES (%s, %s, %s, %s)"
    with conn.transaction():
        if not no_transaction:
            conn.execute(migration.sql)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        conn.execute(record, (migration.version, migration.name, migration.checksum, elapsed_ms))
    log(f"applied {migration.label} ({elapsed_ms} ms)")


def run(command: str, directory: Path = MIGRATIONS_DIR, url: str | None = None) -> None:
    migrations = load_migrations(directory)
    with connect(url or database_url()) as conn:
        conn.autocommit = True
        conn.execute(CREATE_TABLE_SQL)
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        conn.autocommit = False
        try:
            applied = fetch_applied(conn)
            conn.commit()
            pending = plan(migrations, applied)
            if command == "status":
                for m in migrations:
                    state = "applied" if m.version in applied else "PENDING"
                    log(f"{state}  {m.label}")
                log(f"{len(applied)} applied, {len(pending)} pending")
                return
            if not pending:
                log(f"up to date ({len(applied)} migrations applied)")
                return
            for migration in pending:
                apply(conn, migration)
            log(f"done: {len(pending)} applied, {len(applied) + len(pending)} total")
        finally:
            conn.rollback()
            conn.autocommit = True
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["up", "status"], nargs="?", default="up")
    parser.add_argument("--dir", type=Path, default=MIGRATIONS_DIR, help="migrations directory")
    args = parser.parse_args()
    try:
        run(args.command, args.dir)
    except MigrationError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
