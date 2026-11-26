"""Safe HTTP streamer enforcing domain allowlist, no cross-domain redirects, size cap & SHA-256."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

log = logging.getLogger("nyaya.ingest.http")

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_EXCEPTIONS = (
    httpx.RemoteProtocolError,
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
)


class SecurityError(RuntimeError):
    """Raised when an upstream fetch violates domain allowlist, redirect, size, or SHA-256 gates."""


class ChecksumMismatchError(SecurityError):
    """Raised when an upstream response fails SHA-256 verification."""


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
        max_retries: int = 4,
        retry_backoff_s: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.allowed_domains = {d.strip().lower() for d in allowed_domains if d.strip()}
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.timeout_s = timeout_s
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_s = max(0.0, float(retry_backoff_s))
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

    def _stream_once(
        self,
        url: str,
        initial_host: str,
        *,
        cap: int,
        expected_sha256: str | None,
        write_chunk: Callable[[bytes], object],
    ) -> str:
        current_url = url
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
                with client.stream("GET", current_url) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            raise SecurityError(
                                f"Redirect response missing Location header for {current_url!r}"
                            )
                        next_url = urljoin(current_url, location)
                        next_host = self._validate_url(next_url)
                        if next_host != initial_host:
                            raise SecurityError(
                                f"Cross-domain redirect blocked from {initial_host!r} to {next_host!r}"
                            )
                        current_url = next_url
                        continue
                    resp.raise_for_status()
                    hasher = hashlib.sha256()
                    total_bytes = 0
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        if not chunk:
                            continue
                        total_bytes += len(chunk)
                        if total_bytes > cap:
                            raise SecurityError(
                                f"Download from {current_url!r} exceeded size cap "
                                f"({total_bytes} > {cap} bytes)"
                            )
                        hasher.update(chunk)
                        write_chunk(chunk)
                    actual_sha = hasher.hexdigest()
                    if expected_sha256 and actual_sha.lower() != expected_sha256.strip().lower():
                        raise ChecksumMismatchError(
                            f"SHA-256 mismatch for {current_url!r}: "
                            f"expected {expected_sha256}, got {actual_sha}"
                        )
                    return actual_sha

        raise SecurityError(f"Exceeded max_redirects ({self.max_redirects}) for {url!r}")

    def _stream_with_retry(
        self,
        url: str,
        *,
        expected_sha256: str | None,
        max_bytes: int | None,
        write_chunk: Callable[[bytes], object],
        reset_sink: Callable[[], object],
    ) -> str:
        cap = self.max_bytes if max_bytes is None else max_bytes
        initial_host = self._validate_url(url)

        for attempt in range(self.max_retries + 1):
            reset_sink()
            try:
                return self._stream_once(
                    url,
                    initial_host,
                    cap=cap,
                    expected_sha256=expected_sha256,
                    write_chunk=write_chunk,
                )
            except SecurityError:
                raise
            except _RETRYABLE_EXCEPTIONS as exc:
                if attempt >= self.max_retries:
                    raise
                delay = self.retry_backoff_s * (2**attempt)
                log.warning(
                    "Transient HTTP error fetching %s (attempt %d/%d), retrying in %.2fs: %s",
                    url,
                    attempt + 1,
                    self.max_retries + 1,
                    delay,
                    exc,
                )
                if delay > 0:
                    time.sleep(delay)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS_CODES or attempt >= self.max_retries:
                    raise
                delay = self.retry_backoff_s * (2**attempt)
                log.warning(
                    "Transient HTTP %d fetching %s (attempt %d/%d), retrying in %.2fs",
                    exc.response.status_code,
                    url,
                    attempt + 1,
                    self.max_retries + 1,
                    delay,
                )
                if delay > 0:
                    time.sleep(delay)

        raise RuntimeError(f"Unreachable retry loop exit for {url!r}")  # pragma: no cover

    def fetch_bytes(
        self,
        url: str,
        *,
        expected_sha256: str | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        buf = bytearray()
        self._stream_with_retry(
            url,
            expected_sha256=expected_sha256,
            max_bytes=max_bytes,
            write_chunk=buf.extend,
            reset_sink=buf.clear,
        )
        return bytes(buf)

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
            with tmp_path.open("wb") as fh:
                def _reset_file() -> None:
                    fh.seek(0)
                    fh.truncate(0)

                digest = self._stream_with_retry(
                    url,
                    expected_sha256=expected_sha256,
                    max_bytes=max_bytes,
                    write_chunk=fh.write,
                    reset_sink=_reset_file,
                )
            tmp_path.replace(dest_path)
            return digest
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            if dest_path.exists():
                dest_path.unlink(missing_ok=True)
            raise
