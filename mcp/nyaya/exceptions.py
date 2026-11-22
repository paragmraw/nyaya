"""Server-side exceptions that map onto structured MCP tool error responses.

Nyaya v2.0 defines exactly 9 canonical ``NyayaError`` subclasses so MCP
clients and the chat agent can branch deterministically on ``.code`` without
parsing human-readable error strings.
"""

from __future__ import annotations

from typing import Any, Literal

PdfErrorReason = Literal[
    "timeout",
    "size_limit",
    "corrupt_pdf",
    "disallowed_key",
    "http_error",
]


class ConfigurationError(ValueError):
    """Raised when an environment variable or startup setting is invalid."""


class NyayaError(Exception):
    """Base class for all nyaya-raised MCP domain and storage exceptions."""

    code: str = "nyaya_error"
    hint: str | None = None

    def __init__(self, message: str = "", *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if hint is not None:
            self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        """Serialize the exception into a structured JSON-safe error payload."""
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.hint:
            payload["hint"] = self.hint
        return payload


class NotFound(NyayaError):
    """A requested act, provision, amendment, or judgment was not found."""

    code = "not_found"

    def __init__(
        self,
        message: str = "",
        *,
        kind: str = "unknown",
        did_you_mean: list[str] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.kind = kind
        self.did_you_mean = list(did_you_mean or [])

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["kind"] = self.kind
        if self.did_you_mean:
            data["did_you_mean"] = self.did_you_mean
        return data


class AmbiguousReference(NyayaError):
    """A case name or citation matched multiple distinct records."""

    code = "ambiguous_reference"

    def __init__(
        self,
        message: str = "",
        *,
        kind: str = "unknown",
        candidates: list[dict[str, Any]] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.kind = kind
        self.candidates = list(candidates or [])

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["kind"] = self.kind
        data["candidates"] = self.candidates
        return data


class TextUnavailableMetadataOnly(NyayaError):
    """Metadata exists, but full text is absent or PDF is scanned/image-only."""

    code = "text_unavailable_metadata_only"

    def __init__(
        self,
        message: str = "",
        *,
        kind: str = "judgment",
        metadata: dict[str, Any] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.kind = kind
        self.metadata = dict(metadata or {})

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["kind"] = self.kind
        if self.metadata:
            data["metadata"] = self.metadata
        return data


class PdfFetchError(NyayaError):
    """Direct S3 PDF retrieval or validation failed."""

    code = "pdf_fetch_error"

    def __init__(
        self,
        message: str = "",
        *,
        reason: PdfErrorReason = "http_error",
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["reason"] = self.reason
        return data


class InvalidFilter(NyayaError):
    """A caller-supplied filter value or parameter combination is invalid."""

    code = "invalid_filter"

    def __init__(
        self,
        message: str = "",
        *,
        kind: str = "filter",
        allowed_values: list[str] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.kind = kind
        self.allowed_values = list(allowed_values or [])

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["kind"] = self.kind
        if self.allowed_values:
            data["allowed_values"] = self.allowed_values
        return data


class QueryTooBroad(NyayaError):
    """A cold Parquet/HC/District scan lacked mandatory narrowing filters."""

    code = "query_too_broad"

    def __init__(
        self,
        message: str = "",
        *,
        kind: str = "query",
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.kind = kind

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["kind"] = self.kind
        return data


class QueryTimeout(NyayaError):
    """A storage query exceeded its hard wall-clock execution budget."""

    code = "query_timeout"

    def __init__(
        self,
        message: str = "",
        *,
        kind: str = "query",
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.kind = kind

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["kind"] = self.kind
        return data


class StorageUnavailable(NyayaError):
    """A required local or remote storage backend could not be opened or reached."""

    code = "storage_unavailable"

    def __init__(
        self,
        message: str = "",
        *,
        degraded_capabilities: list[str] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.degraded_capabilities = list(degraded_capabilities or [])

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        if self.degraded_capabilities:
            data["degraded_capabilities"] = self.degraded_capabilities
        return data


class CommercialLicenseBlocked(NyayaError):
    """Target record/dataset is non-commercial while commercial_safe_only is active."""

    code = "commercial_license_blocked"

    def __init__(
        self,
        message: str = "",
        *,
        license_spdx: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.license_spdx = license_spdx

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        if self.license_spdx:
            data["license_spdx"] = self.license_spdx
        return data
