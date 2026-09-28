"""Logging setup: one line per request, and enough context to debug from the log alone.

Uvicorn's access log says a request returned 401 but never why, which is useless when the
question is "why can't I upload?". So every request gets an id, every log line inside that
request carries the id and the signed-in user, and the places that can quietly refuse a
request say what they decided and on what grounds.

    LOG_LEVEL=INFO   one line per request, plus decisions worth knowing about
    LOG_LEVEL=DEBUG  adds query strings, every SQL statement and every storage call
    LOG_FORMAT=json  structured output
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from app.core.config import Settings

# Set per request by the middleware, read by the filter below, so no function has to
# thread a request id through its arguments to get it into its log lines.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
user_var: ContextVar[str] = ContextVar("user", default="")

# Libraries that are chatty at DEBUG and rarely tell us anything we asked for.
NOISY = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "urllib3": logging.WARNING,
    "google": logging.WARNING,
    "google.auth": logging.WARNING,
    "google.cloud": logging.WARNING,
    "asyncio": logging.WARNING,
    "multipart": logging.INFO,
}


class ContextFilter(logging.Filter):
    """Attach the current request id and user to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        record.user = user_var.get() or "anonymous"
        return True


class TextFormatter(logging.Formatter):
    """18:05:41.123 INFO  cloud.auth    [a1b2c3d4 alex] message"""

    default_time_format = "%H:%M:%S"
    default_msec_format = "%s.%03d"

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record)} {record.levelname:<5} "
            f"{record.name:<22} [{record.request_id} {record.user}] {record.getMessage()}"
        )
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": record.request_id,
            "user": record.user,
        }
        # anything passed as extra={...}, so structured fields survive into the log
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "request_id",
    "user",
    "message",
    "asctime",
    "taskName",
}


def configure_logging(settings: Settings) -> None:
    """Install our handler on the root logger and quiet the duplicates."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn's own access line duplicates ours, with less in it
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    for name, noisy_level in NOISY.items():
        logging.getLogger(name).setLevel(max(noisy_level, level))

    # SQL is only ever wanted deliberately: it is enormous
    logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO if settings.sql_echo else logging.WARNING)

    logging.getLogger("cloud").setLevel(level)
    logging.getLogger("cloud.startup").info(
        "logging ready: level=%s format=%s env=%s",
        settings.log_level.upper(),
        settings.log_format,
        settings.app_env,
    )


def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


class Timer:
    """Milliseconds elapsed, for the duration in a log line."""

    def __init__(self) -> None:
        self.started = time.perf_counter()

    @property
    def ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)
