from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from shared.integrations.mem0_adapter import Mem0Adapter


class Mem0AdapterTests(unittest.TestCase):
    def test_adapter_is_disabled_by_default_when_not_configured(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in (
                "MEM0_ENABLED",
                "MEM0_API_KEY",
                "MEM0_BASE_URL",
                "MEM0_ORG_ID",
                "MEM0_PROJECT_ID",
            ):
                os.environ.pop(key, None)

            adapter = Mem0Adapter.from_environment()

            self.assertFalse(adapter.is_enabled)
            self.assertIsNone(adapter.add_memory(text="hello", user_id="u-1"))

    def test_adapter_detects_enabled_configuration(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MEM0_ENABLED": "true",
                "MEM0_API_KEY": "mem0-key",
                "MEM0_BASE_URL": "https://api.mem0.ai",
                "MEM0_ORG_ID": "org-123",
                "MEM0_PROJECT_ID": "proj-123",
            },
            clear=False,
        ):
            adapter = Mem0Adapter.from_environment()

            self.assertTrue(adapter.is_enabled)
            self.assertEqual(adapter.api_key, "mem0-key")
            self.assertEqual(adapter.base_url, "https://api.mem0.ai")
            self.assertEqual(adapter.org_id, "org-123")
            self.assertEqual(adapter.project_id, "proj-123")

    def test_adapter_fails_closed_when_client_setup_or_write_fails(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MEM0_ENABLED": "true",
                "MEM0_API_KEY": "mem0-key",
            },
            clear=False,
        ):
            adapter = Mem0Adapter.from_environment()

            def raising_factory(**_kwargs):
                raise RuntimeError("connection failure")

            failing = Mem0Adapter(
                enabled=adapter.enabled,
                api_key=adapter.api_key,
                base_url=adapter.base_url,
                org_id=adapter.org_id,
                project_id=adapter.project_id,
                client_factory=raising_factory,
            )
            self.assertIsNone(
                failing.add_memory(
                    text="sensitive transcript",
                    user_id="u-1",
                    metadata={"operation": "voicemail_summary", "debtor_name": "Jane"},
                )
            )


if __name__ == "__main__":
    unittest.main()