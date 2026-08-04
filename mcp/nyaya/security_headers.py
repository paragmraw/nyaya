"""Security headers middleware for the nyaya ASGI app.

Sets standard security headers on every HTTP response:
- Content-Security-Policy: restricts all resource loading to same-origin.
- X-Frame-Options: DENY (clickjacking protection).
- X-Content-Type-Options: nosniff (MIME-sniffing protection).
- Referrer-Policy: strict-origin-when-cross-origin.
- Permissions-Policy: denies camera, microphone, geolocation.
- Strict-Transport-Security: HSTS (only over HTTPS).

This is the single source of truth for the static-export Next.js SPA served
from the same origin — the build artifact doesn't need its own ``_headers``
file.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# 'unsafe-inline' is allowed because Next.js injects runtime styles and
# hydration scripts. Tighten in the future by switching to nonce-based CSP.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        # HSTS only over HTTPS. Behind a reverse proxy, request.url.scheme
        # reflects the original protocol via X-Forwarded-Proto.
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response
