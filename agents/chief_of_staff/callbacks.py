"""Explicit local callback-resolution actions for Chief of Staff."""

from __future__ import annotations

from dataclasses import replace

from agents.voicemail_tracker_agent.runtime_store import (
    CallbackState,
    VoicemailRuntimeStore,
    utc_now,
)
from agents.voicemail_tracker_agent.status_store import VoicemailStatusStore


class CallbackResolutionError(ValueError):
    """Raised when a requested callback cannot be resolved safely."""


class CallbackResolutionService:
    """Resolve existing callback records without scanning or contacting consumers."""

    def __init__(
        self,
        runtime_store: VoicemailRuntimeStore,
        status_store: VoicemailStatusStore,
    ) -> None:
        self.runtime_store = runtime_store
        self.status_store = status_store

    def list_pending(self) -> list[CallbackState]:
        """Return current pending callbacks from local runtime state only."""

        return sorted(
            self.runtime_store.read().pending_callbacks(),
            key=lambda callback: (callback.created_at, callback.voicemail_id),
        )

    def complete(self, voicemail_id: str) -> tuple[CallbackState, bool]:
        """Mark one existing callback complete and return whether state changed."""

        callback_id = voicemail_id.strip()
        if not callback_id:
            raise CallbackResolutionError("A voicemail ID is required.")

        state = self.runtime_store.read()
        callback = state.callbacks.get(callback_id)
        if callback is None:
            raise CallbackResolutionError("Callback record was not found.")
        if not callback.is_pending:
            return callback, False

        callback.status = "completed"
        callback.completed_at = utc_now()
        self.runtime_store.write(state)
        self._sync_pending_count(len(state.pending_callbacks()))
        return callback, True

    def _sync_pending_count(self, pending_count: int) -> None:
        """Keep the existing non-sensitive status snapshot aligned with runtime state."""

        snapshot = self.status_store.read()
        if snapshot is not None:
            self.status_store.write(
                replace(snapshot, pending_callback_count=pending_count)
            )
