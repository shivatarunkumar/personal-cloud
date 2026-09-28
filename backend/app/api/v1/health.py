"""Is everything the app depends on reachable? 200 when yes, 503 naming what isn't."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import get_storage
from app.core.db import get_session

router = APIRouter(tags=["health"])
log = logging.getLogger("cloud.health")

TIMEOUT = 5.0


async def _timed(name: str, check) -> dict:
    started = time.perf_counter()
    try:
        detail = await asyncio.wait_for(check(), TIMEOUT)
        ok = True
    except TimeoutError:
        ok, detail = False, f"no answer within {TIMEOUT:.0f}s"
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
    ms = int((time.perf_counter() - started) * 1000)
    log.debug("  check %-8s %s %dms", name, "ok  " if ok else "FAIL", ms)
    return {"name": name, "ok": ok, "detail": detail, "ms": ms}


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    async def database() -> str:
        version = await session.scalar(text("SELECT max(version) FROM schema_migrations"))
        return f"schema V{version:03d}"

    async def storage() -> str:
        store = get_storage()
        if not await asyncio.to_thread(store.bucket_exists):
            raise RuntimeError(f"bucket {store.bucket_name} not found")
        return f"bucket {store.bucket_name}"

    checks = [await _timed("database", database), await _timed("storage", storage)]
    ok = all(c["ok"] for c in checks)
    return JSONResponse(
        status_code=200 if ok else 503, content={"status": "ok" if ok else "down", "checks": checks}
    )
