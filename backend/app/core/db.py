import logging
import time
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings

log = logging.getLogger("cloud.sql")

# How much of a statement to show: enough to recognise it, not enough to fill the screen.
SQL_PREVIEW = 160


def _trace_queries(engine: AsyncEngine) -> None:
    """At DEBUG, log every statement with how long it took.

    This is what turns "the request took 793ms" into "it ran four queries and one of them
    took 780ms". SQL_ECHO=true is still there for the full, untruncated firehose.
    """

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ARG001
        conn.info["query_started"] = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ARG001
        started = conn.info.pop("query_started", None)
        if started is None:
            return
        elapsed = (time.perf_counter() - started) * 1000
        flat = " ".join(statement.split())
        if len(flat) > SQL_PREVIEW:
            flat = f"{flat[:SQL_PREVIEW]}…"
        # slow queries are worth seeing even when nobody asked for a trace
        level = logging.INFO if elapsed >= 500 else logging.DEBUG
        log.log(level, "  sql %.1fms  %s", elapsed, flat)


def should_trace_sql(settings: Settings) -> bool:
    """Queries are traced when the app log is at DEBUG, or on demand with SQL_ECHO."""
    return settings.log_level == "DEBUG" or settings.sql_echo


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_size=settings.db_pool_size, pool_pre_ping=True)
    if should_trace_sql(settings):
        _trace_queries(engine)
    return engine


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
