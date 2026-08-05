from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.config import get_bool, get_str, load_environment


@dataclass(frozen=True)
class LangfuseAdapter:
    """Optional Langfuse adapter for future tracing integration.

    Phase 1 keeps this adapter as a no-op unless Langfuse is explicitly
    enabled and fully configured. It never sends transcript text or other
    sensitive content to an external service.
    """

    enabled: bool
    public_key: str = ""
    secret_key: str = ""
    host: str = ""

    @property
    def is_enabled(self) -> bool:
        return self.enabled and bool(self.public_key) and bool(self.secret_key) and bool(self.host)

    @classmethod
    def from_environment(cls) -> "LangfuseAdapter":
        load_environment()
        return cls(
            enabled=get_bool("LANGFUSE_ENABLED", False),
            public_key=get_str("LANGFUSE_PUBLIC_KEY", ""),
            secret_key=get_str("LANGFUSE_SECRET_KEY", ""),
            host=get_str("LANGFUSE_HOST", ""),
        )

    def build_safe_metadata(
        self,
        *,
        operation_name: str | None = None,
        model_name: str | None = None,
        duration_ms: int | None = None,
        success: bool | None = None,
        request_id: str | None = None,
        **_ignored: Any,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}

        for key, value in (
            ("operation_name", operation_name),
            ("model_name", model_name),
            ("duration_ms", duration_ms),
            ("success", success),
            ("request_id", request_id),
        ):
            if value is not None:
                metadata[key] = value

        return metadata

    def record_event(
        self,
        operation_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> Any | None:
        if not self.is_enabled:
            return None

        safe_metadata = self.build_safe_metadata(
            operation_name=operation_name,
            **(metadata or {}),
        )
        _ = safe_metadata
        return None
