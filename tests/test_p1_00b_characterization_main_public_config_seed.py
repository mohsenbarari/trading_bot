"""Hermetic characterization unit for seed API-OPS-GET-MAIN-GET-PUBLIC-CONFIG-L531.

Provenance:
- Stage: P1-00b (Assignment WA-P1-00B-FREEZE-20260904-001)
- Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
- Seed: API-OPS-GET-MAIN-GET-PUBLIC-CONFIG-L531
- Target: main.get_public_config
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    _FENCE,
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import main


class MainPublicConfigSeedCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for main.get_public_config seed handler."""

    async def test_get_public_config_returns_exact_public_settings_without_side_effects(
        self,
    ) -> None:
        patched_bot_username = "characterization_seed_bot"
        patched_frontend_url = "https://frontend.seed-test.internal:9443"

        with (
            patch.object(main.settings, "bot_username", patched_bot_username),
            patch.object(main.settings, "frontend_url", patched_frontend_url),
        ):
            denied_before = len(_FENCE["denied_connects"])
            redis_constructs_before = len(_FENCE["live_redis_constructs"])

            result = await main.get_public_config()

            self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
            self.assertEqual(
                len(_FENCE["live_redis_constructs"]), redis_constructs_before
            )

            self.assertIs(type(result), dict)
            self.assertEqual(
                result,
                {
                    "bot_username": patched_bot_username,
                    "frontend_url": patched_frontend_url,
                },
            )
            self.assertEqual(set(result.keys()), {"bot_username", "frontend_url"})
            self.assertEqual(result["bot_username"], patched_bot_username)
            self.assertEqual(result["frontend_url"], patched_frontend_url)

            for sensitive_key in (
                "jwt_secret_key",
                "postgres_password",
                "telegram_otp_queue_secret",
                "sync_api_key",
                "dev_api_key",
                "database_url",
                "redis_url",
            ):
                self.assertNotIn(sensitive_key, result)


if __name__ == "__main__":
    unittest.main()
