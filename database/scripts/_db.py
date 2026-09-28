"""Shared helpers for the database scripts: env settings, URLs and connections."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

DATABASE_DIR = Path(__file__).resolve().parent.parent
POSTGRES_DIR = DATABASE_DIR / "postgres"
MIGRATIONS_DIR = POSTGRES_DIR / "migrations"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        fail(f"environment variable {name} is not set (see .env.example)")
    return value


def sync_url(url: str) -> str:
    """Turn a SQLAlchemy URL (postgresql+asyncpg://...) into a libpq URL."""
    scheme, sep, rest = url.partition("://")
    return f"{scheme.split('+', 1)[0]}{sep}{rest}"


def database_url() -> str:
    return sync_url(env("DATABASE_URL"))


def admin_url() -> str:
    return sync_url(env("POSTGRES_ADMIN_URL"))


def url_parts(url: str) -> tuple[str, str, str]:
    """Return (user, password, database) from a Postgres URL."""
    parts = urlsplit(url)
    return parts.username or "", parts.password or "", parts.path.lstrip("/")


def url_host(url: str) -> str:
    return urlsplit(url).hostname or ""


def connect(url: str, *, autocommit: bool = False, wait_seconds: int = 60) -> psycopg.Connection:
    """Connect, retrying while the server starts up."""
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return psycopg.connect(url, autocommit=autocommit, connect_timeout=5)
        except psycopg.OperationalError as exc:
            if time.monotonic() >= deadline:
                fail(f"could not connect to {url_host(url)}: {exc}")
            time.sleep(1)


def log(message: str) -> None:
    print(f"[db] {message}", flush=True)


def fail(message: str) -> None:
    print(f"[db] ERROR: {message}", file=sys.stderr, flush=True)
    sys.exit(1)
