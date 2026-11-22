"""Safe HTTP streamer enforcing domain allowlist, no cross-domain redirects, size cap & SHA-256."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx


class SecurityError(RuntimeError):
    """Raised when an upstream fetch violates domain allowlist, redirect, size, or SHA-256 gates."""


_BANNED_HOSTS = frozenset(
    {
        "indiankanoon.org",
        "www.indiankanoon.org",
    }
)


class SafeHttpFetcher:
    """Domain-allowlisted HTTP client that refuses cross-domain redirects and verifies SHA-256."""

    def __init__(
        self,
        *,
        allowed_domains: Iterable[str],
        max_bytes: int = 5 * 1024 * 1024 * 1024,
        max_redirects: int = 3,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.allowed_domains = {d.strip().lower() for d in allowed_domains if d.strip()}
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.timeout_s = timeout_s
        self._transport = transport

    def _validate_url(self, url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme != "https":
            raise SecurityError(f"Disallowed non-HTTPS URL scheme in {url!r}")
        host = (parsed.hostname or "").lower()
        if not host or host in _BANNED_HOSTS or host not in self.allowed_domains:
            raise SecurityError(
                f"Disallowed domain {host!r} in URL {url!r}; allowed={sorted(self.allowed_domains)}"
            )
        return host

    def fetch_bytes(
        self,
        url: str,
        *,
        expected_sha256: str | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        cap = self.max_bytes if max_bytes is None else max_bytes
        current_url = url
        initial_host = self._validate_url(current_url)

        with httpx.Client(
            timeout=self.timeout_s,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            for _ in range(self.max_redirects + 1):
                host = self._validate_url(current_url)
                if host != initial_host:
                    raise SecurityError(
                        f"Cross-domain redirect blocked from {initial_host!r} to {host!r}"
                    )
                resp = client.get(current_url)
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location")
                    if not location:
                        raise SecurityError(f"Redirect response missing Location header for {current_url!r}")
                    next_url = urljoin(current_url, location)
                    next_host = self._validate_url(next_url)
                    if next_host != initial_host:
                        raise SecurityError(
                            f"Cross-domain redirect blocked from {initial_host!r} to {next_host!r}"
                        )
                    current_url = next_url
                    continue
                resp.raise_for_status()
                data = resp.content
                if len(data) > cap:
                    raise SecurityError(
                        f"Download from {current_url!r} exceeded size cap ({len(data)} > {cap} bytes)"
                    )
                actual_sha = hashlib.sha256(data).hexdigest()
                if expected_sha256 and actual_sha.lower() != expected_sha256.strip().lower():
                    raise SecurityError(
                        f"SHA-256 mismatch for {current_url!r}: expected {expected_sha256}, got {actual_sha}"
                    )
                return data

        raise SecurityError(f"Exceeded max_redirects ({self.max_redirects}) for {url!r}")

    def download_to_file(
        self,
        url: str,
        dest_path: Path,
        *,
        expected_sha256: str | None = None,
        max_bytes: int | None = None,
    ) -> str:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        try:
            data = self.fetch_bytes(url, expected_sha256=expected_sha256, max_bytes=max_bytes)
            digest = hashlib.sha256(data).hexdigest()
            tmp_path.write_bytes(data)
            tmp_path.replace(dest_path)
            return digest
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            if dest_path.exists():
                dest_path.unlink(missing_ok=True)
            raise
