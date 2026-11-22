"""Security headers middleware for the Nyaya v2.0 ASGI app.

Sets standard security headers on every HTTP response:
- Content-Security-Policy
- X-Frame-Options: DENY
- X-Content-Type-Options: nosniff
- Referrer-Policy: strict-origin-when-cross-origin
- Permissions-Policy: camera=(), microphone=(), geolocation=()
- Cross-Origin-Opener-Policy: same-origin
- Cross-Origin-Resource-Policy: same-origin
- Strict-Transport-Security: HSTS over HTTPS or trusted X-Forwarded-Proto=https
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import get_rate_limit_settings
from .ratelimit import BodySizeLimitMiddleware as MaxBodySizeMiddleware

__all__ = ["MaxBodySizeMiddleware", "SecurityHeadersMiddleware"]

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
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _is_https_request(scope: Scope, trusted_proxy_hops: int) -> bool:
    if scope.get("scheme") == "https":
        return True
    if trusted_proxy_hops <= 0:
        return False
    for k, v in scope.get("headers") or []:
        if k.lower() == b"x-forwarded-proto":
            protos = [p.strip().lower() for p in v.decode("latin-1").split(",") if p.strip()]
            if protos:
                idx = max(0, len(protos) - trusted_proxy_hops)
                return protos[idx] == "https"
    return False


class SecurityHeadersMiddleware:
    """Add security headers to every HTTP response (pure ASGI)."""

    def __init__(self, app: ASGIApp, *, trusted_proxy_hops: int | None = None) -> None:
        self.app = app
        self.trusted_proxy_hops = trusted_proxy_hops

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        hops = (
            get_rate_limit_settings().trusted_proxy_hops
            if self.trusted_proxy_hops is None
            else max(0, int(self.trusted_proxy_hops))
        )
        is_https = _is_https_request(scope, hops)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in _SECURITY_HEADERS.items():
                    headers.setdefault(key, value)
                if is_https:
                    headers.setdefault(
                        "Strict-Transport-Security",
                        "max-age=31536000; includeSubDomains",
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)
