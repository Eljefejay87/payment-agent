from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from shared.integrations.langfuse_adapter import LangfuseAdapter


class LangfuseAdapterTests(unittest.TestCase):
    def test_adapter_is_disabled_by_default_when_not_configured(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in (
                "LANGFUSE_ENABLED",
                "LANGFUSE_PUBLIC_KEY",
                "LANGFUSE_SECRET_KEY",
                "LANGFUSE_HOST",
            ):
                os.environ.pop(key, None)

            adapter = LangfuseAdapter.from_environment()

            self.assertFalse(adapter.is_enabled)
            self.assertIsNone(adapter.record_event("noop", {}))

    def test_adapter_is_enabled_when_config_present_and_sanitizes_metadata(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LANGFUSE_ENABLED": "true",
                "LANGFUSE_PUBLIC_KEY": "public-key",
                "LANGFUSE_SECRET_KEY": "secret-key",
                "LANGFUSE_HOST": "https://langfuse.example",
            },
            clear=False,
        ):
            adapter = LangfuseAdapter.from_environment()
            metadata = adapter.build_safe_metadata(
                operation_name="transcribe_audio",
                model_name="whisper-1",
                duration_ms=1250,
                success=True,
                request_id="req-123",
                transcript="top secret transcript",
                debtor_name="Jane Doe",
                payment_amount="100.00",
                extra_sensitive={"api_key": "abc123"},
            )

            self.assertTrue(adapter.is_enabled)
            self.assertEqual(metadata["operation_name"], "transcribe_audio")
            self.assertEqual(metadata["model_name"], "whisper-1")
            self.assertEqual(metadata["duration_ms"], 1250)
            self.assertEqual(metadata["success"], True)
            self.assertEqual(metadata["request_id"], "req-123")
            self.assertNotIn("transcript", metadata)
            self.assertNotIn("debtor_name", metadata)
            self.assertNotIn("payment_amount", metadata)
            self.assertNotIn("api_key", metadata)

    def test_adapter_is_disabled_when_feature_flag_is_off(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LANGFUSE_ENABLED": "false",
                "LANGFUSE_PUBLIC_KEY": "public-key",
                "LANGFUSE_SECRET_KEY": "secret-key",
            },
            clear=False,
        ):
            adapter = LangfuseAdapter.from_environment()

            self.assertFalse(adapter.is_enabled)


if __name__ == "__main__":
    unittest.main()
