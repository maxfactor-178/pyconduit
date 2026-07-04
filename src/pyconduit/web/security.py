"""Security helpers: client-IP extraction, WebSocket Origin checks, HTTP headers.

Pure functions (no framework objects) so they can be unit-tested directly.
"""

from __future__ import annotations

from collections.abc import Mapping

# Hardening headers applied to every HTTP response. The frontend loads only
# same-origin JS/CSS and opens a same-origin WebSocket, so a strict policy fits.
SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "connect-src 'self'; "        # the WebSocket is same-origin
        "img-src 'self' data:; "
        "style-src 'self'; "
        "script-src 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"      # anti-clickjacking
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def client_ip(
    *, headers: Mapping[str, str], peer: str | None, ip_header: str | None
) -> str:
    """Resolve the real client IP.

    When ``ip_header`` is set (e.g. "X-Forwarded-For"), trust it — but only the
    FIRST entry, which the proxy fills with the originating client; the rest are
    intermediate hops. Falls back to the socket peer address.
    """
    if ip_header:
        lowered = {k.lower(): v for k, v in headers.items()}
        value = lowered.get(ip_header.lower())
        if value:
            return value.split(",")[0].strip()
    return peer or "unknown"


def origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    """Whether a WebSocket Origin may connect.

    Empty allowlist disables enforcement (dev). When enforcing, a missing Origin
    is rejected: browsers always send Origin on a WebSocket handshake, so its
    absence means the request did not come from a page we serve.
    """
    if not allowed:
        return True
    if not origin:
        return False
    return origin in allowed
