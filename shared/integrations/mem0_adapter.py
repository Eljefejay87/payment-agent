from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from shared.config import get_bool, get_str, load_environment


DEFAULT_MEM0_BASE_URL = "https://api.mem0.ai"


@dataclass(frozen=True)
class Mem0Adapter:
    """Dormant Mem0 adapter for future memory integration.

    Phase 1 keeps this adapter disabled by default and no-op unless explicitly
    enabled and configured. It is intentionally not wired to any active agent.
    """

    enabled: bool
    api_key: str = ""
    base_url: str = DEFAULT_MEM0_BASE_URL
    org_id: str = ""
    project_id: str = ""
    client_factory: Callable[..., Any] | None = None

    @property
    def is_enabled(self) -> bool:
        return self.enabled and bool(self.api_key)

    @classmethod
    def from_environment(cls) -> "Mem0Adapter":
        load_environment()
        return cls(
            enabled=get_bool("MEM0_ENABLED", False),
            api_key=get_str("MEM0_API_KEY", ""),
            base_url=get_str("MEM0_BASE_URL", DEFAULT_MEM0_BASE_URL),
            org_id=get_str("MEM0_ORG_ID", ""),
            project_id=get_str("MEM0_PROJECT_ID", ""),
        )

    def build_safe_metadata(self, **metadata: Any) -> dict[str, Any]:
        """Allowlist only simple future-safe metadata values.

        This keeps Phase 1 privacy-safe while preserving a reusable shape for
        future integrations.
        """

        safe: dict[str, Any] = {}
        for key in ("agent", "operation", "request_id", "session_id", "user_alias"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                safe[key] = value.strip()
        return safe

    def add_memory(self, *, text: str, user_id: str, metadata: dict[str, Any] | None = None) -> Any | None:
        """No-op placeholder for future write integration.

        Returns None when disabled, not configured, or if a future Mem0 client
        fails to initialize or write.
        """

        if not self.is_enabled:
            return None

        if not text or not user_id:
            return None

        if self.client_factory is None:
            return None

        try:
            client = self.client_factory(
                api_key=self.api_key,
                base_url=self.base_url,
                org_id=self.org_id,
                project_id=self.project_id,
            )
            return client.add(
                user_id=user_id,
                text=text,
                metadata=self.build_safe_metadata(**(metadata or {})),
            )
        except Exception:
            return None