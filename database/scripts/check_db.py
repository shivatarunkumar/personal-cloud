"""Check the database without starting the API: one line per check, and the fix.

    python database/scripts/check_db.py

Exits non-zero when anything fails, so it can be chained in scripts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import MIGRATIONS_DIR, sync_url, url_host, url_parts  # noqa: E402

REQUIRED_TABLES = ("users", "refresh_tokens", "folders", "files", "file_events")

failures = 0


def report(ok: bool, name: str, detail: str, fix: str = "") -> None:
    global failures
    print(f"  {'ok  ' if ok else 'FAIL'}  {name:<12} {detail}")
    if not ok:
        failures += 1
        if fix:
            print(f"  {'':<18}→ {fix}")


def main() -> int:
    print("[check-db] checking PostgreSQL\n")
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        report(False, "config", "DATABASE_URL is not set", "cp .env.example .env, then fill it in")
        return 1
    url = sync_url(raw)
    user, _, name = url_parts(url)
    report(True, "config", f"{user}@{url_host(url)}/{name}")

    try:
        conn = psycopg.connect(url, connect_timeout=5)
    except psycopg.OperationalError as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        fix = "make db-init"
        if "Connection refused" in first or "No such file" in first:
            fix = "start PostgreSQL: brew services start postgresql@17"
        report(False, "connect", first, fix)
        return 1

    with conn:
        version = conn.execute("SHOW server_version").fetchone()[0]
        report(True, "connect", f"PostgreSQL {version}")

        has_trgm = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'").fetchone()
        report(bool(has_trgm), "extensions", "pg_trgm" if has_trgm else "pg_trgm missing", "make db-init")

        if not conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0]:
            report(False, "migrations", "never run", "make db-init")
            return 1
        applied = {v for (v,) in conn.execute("SELECT version FROM schema_migrations")}
        files = sorted(MIGRATIONS_DIR.glob("V*.sql"))
        pending = [f.name for f in files if int(f.name[1:4]) not in applied]
        report(
            not pending,
            "migrations",
            f"{len(applied)} applied" + (f", pending: {', '.join(pending)}" if pending else ""),
            "make db-init",
        )

        missing = [
            t for t in REQUIRED_TABLES if not conn.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0]
        ]
        report(not missing, "tables", "all present" if not missing else f"missing {missing}", "make db-init")
        if not missing:
            users, files_n, size = conn.execute(
                "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM files WHERE status = 'ready'),"
                " (SELECT coalesce(sum(size_bytes), 0) FROM files WHERE status = 'ready')"
            ).fetchone()
            report(True, "contents", f"{users} account(s), {files_n} file(s), {size / 1024**3:.2f} GiB")

    print()
    print("[check-db] all good" if not failures else f"[check-db] {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
