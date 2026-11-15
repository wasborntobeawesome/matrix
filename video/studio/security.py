"""Request guards for a local web app (pure ASGI middleware, so it also covers uploads and StaticFiles).

1. Host check (every request): the Host header must be a loopback name (127.0.0.1, localhost, ::1) or one listed
   in STUDIO_ALLOWED_HOSTS. Blocks DNS-rebinding pages, which would otherwise be "same origin" with the studio.
2. Same-origin check (POST/PUT/PATCH/DELETE): a request that carries an Origin header must come from this
   server's own origin; "Origin: null" (sandboxed frames, e.g. a template running in the preview) and
   Sec-Fetch-Site: cross-site are refused too. Requests without Origin (curl, scripts, the inbox) are local tools
   and are allowed — the same rule as Ovoz's `same_origin_only`.
3. Body size limits per route (also for chunked uploads without Content-Length): 413 before the body is
   parsed or spooled to disk.
4. Response headers: nosniff, frame-ancestors / X-Frame-Options (no clickjacking), Referrer-Policy; template
   files get `Content-Security-Policy: sandbox` (an SVG with a script opened directly cannot run on the studio
   origin); the renderer, fonts and template files get `Access-Control-Allow-Origin: *` so the sandboxed live
   preview (an opaque origin) can load template.js (an ES module) and fonts.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
EXTRA_HOSTS = {h.strip().lower() for h in os.environ.get("STUDIO_ALLOWED_HOSTS", "").split(",") if h.strip()}
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

MB = 1024 * 1024
# (method, path regex, limit in bytes); first match wins
BODY_LIMITS = [
    ("POST", re.compile(r"^/api/projects/?$"), 1536 * MB),                 # brief + screenshots + recordings
    ("POST", re.compile(r"^/api/projects/[^/]+/assets$"), 1536 * MB),
    ("POST", re.compile(r"^/api/library/music$"), 64 * MB),
    ("POST", re.compile(r"^/api/library/portraits$"), 32 * MB),
    ("POST", re.compile(r"^/api/templates$"), 52 * MB),
    ("POST", re.compile(r"^/api/comfy/workflows(/detect)?$"), 16 * MB),
]
DEFAULT_LIMIT = 2 * MB                                                     # every other JSON body (briefs, settings)

CORS_PREFIXES = ("/renderer/", "/templates/", "/shared/fonts/")


def _host_ok(host: str) -> bool:
    name = (urlsplit(f"//{host}").hostname or "").lower()
    return name in LOCAL_HOSTS or name in EXTRA_HOSTS or host.lower() in EXTRA_HOSTS


def origin_ok(headers: dict, method: str) -> str | None:
    """None when the request may proceed, else the reason (403)."""
    host = headers.get("host", "")
    if not _host_ok(host):
        return f"host '{host}' is not allowed (open the studio at http://127.0.0.1:<port>/)"
    if method not in UNSAFE:
        return None
    origin = headers.get("origin")
    if origin is not None:
        if origin == "null" or urlsplit(origin).netloc.lower() != host.lower():
            return "cross-origin request refused: changes can only be made from the studio's own page"
    if headers.get("sec-fetch-site") == "cross-site":
        return "cross-site request refused: changes can only be made from the studio's own page"
    return None


def body_limit(method: str, path: str) -> int:
    for m, rx, n in BODY_LIMITS:
        if m == method and rx.match(path):
            return n
    return DEFAULT_LIMIT


class _TooLarge(Exception):
    pass


async def _send_json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            (b"x-content-type-options", b"nosniff")]})
    await send({"type": "http.response.body", "body": body})


class Guard:
    """ASGI middleware: host / origin checks, body limits, security headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        method, path = scope["method"], scope.get("path", "")
        why = origin_ok(headers, method)
        if why:
            return await _send_json(send, 403, why)
        limit = body_limit(method, path)
        try:
            declared = int(headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > limit:
            return await _send_json(send, 413, f"request body too large (limit {limit // MB or 1} MB)")
        seen = 0

        async def recv():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body") or b"")
                if seen > limit:
                    raise _TooLarge()
            return msg

        started = False
        cors = method in ("GET", "HEAD") and path.startswith(CORS_PREFIXES)
        sandbox = path.startswith("/templates/")

        async def snd(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
                hs = list(msg.get("headers") or [])
                names = {k.lower() for k, _ in hs}
                add = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin")]
                if b"x-frame-options" not in names:
                    add.append((b"x-frame-options", b"SAMEORIGIN"))
                if cors:
                    add.append((b"access-control-allow-origin", b"*"))
                if sandbox and b"content-security-policy" not in names:
                    add.append((b"content-security-policy", b"sandbox"))
                msg = {**msg, "headers": hs + add}
            await send(msg)

        try:
            await self.app(scope, recv, snd)
        except _TooLarge:
            if not started:
                await _send_json(send, 413, f"request body too large (limit {limit // MB or 1} MB)")
