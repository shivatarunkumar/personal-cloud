"""One log line per request, and a request id that ties everything else to it.

Written as raw ASGI rather than BaseHTTPMiddleware on purpose: BaseHTTPMiddleware runs the
endpoint in its own task, so a contextvar the auth dependency sets (which user this is)
is invisible by the time the middleware logs. Plain ASGI shares the context, so the
request line can name the person who made it.
"""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import Timer, new_request_id, request_id_var, user_var

log = logging.getLogger("cloud.request")

# Health polling would otherwise be most of the log; its failures are logged elsewhere.
QUIET_PATHS = {"/api/v1/health", "/healthz"}


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.decode(): value.decode() for key, value in scope.get("headers", [])}
        # honour an id from upstream (a proxy, another service) so one trace spans both
        request_id = headers.get("x-request-id") or new_request_id()
        request_id_var.set(request_id)
        user_var.set("")
        timer = Timer()

        method = scope.get("method", "?")
        path = scope.get("path", "")
        raw_query = scope.get("query_string", b"").decode()
        status = 500
        response_bytes = 0
        content_type = ""
        debug = log.isEnabledFor(logging.DEBUG)

        if debug:
            # entry: everything known before the handler runs, so a trace has a start
            log.debug(
                "→ %s %s%s from %s%s%s%s",
                method,
                path,
                f"?{raw_query}" if raw_query else "",
                scope["client"][0] if scope.get("client") else "unknown",
                f" as={headers['content-type']}" if "content-type" in headers else "",
                f" bytes={headers['content-length']}" if "content-length" in headers else "",
                " cookies=yes" if headers.get("cookie") else " cookies=none",
            )

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_bytes, content_type
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
                if debug:
                    out = {k.decode(): v.decode() for k, v in message.get("headers", [])}
                    content_type = out.get("content-type", "")
                    # the handler is done here: everything after this is transfer
                    log.debug("  responding %d after %dms (%s)", status, timer.ms, content_type or "no type")
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b"") or b"")
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            # the handler blew up: log it here, with the request id, before it becomes a 500
            log.exception("%s %s failed after %dms", method, path, timer.ms)
            raise

        if path in QUIET_PATHS and status < 400:
            level = logging.DEBUG
        elif status >= 500:
            level = logging.WARNING
        else:
            level = logging.INFO

        query = f"?{raw_query}" if raw_query and debug else ""
        log.log(
            level,
            "← %s %s%s -> %d in %dms%s",
            method,
            path,
            query,
            status,
            timer.ms,
            f" ({response_bytes} bytes)" if debug and response_bytes else "",
            extra={
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": timer.ms,
                "response_bytes": response_bytes or None,
                "client": scope["client"][0] if scope.get("client") else None,
            },
        )
