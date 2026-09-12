"""Hermetic characterization unit for seed API-OPS-GET-MAIN-GET-PUBLIC-CONFIG-L531.

Provenance:
- Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
- Seed: API-OPS-GET-MAIN-GET-PUBLIC-CONFIG-L531
- Target: main.get_public_config

Characterization only: this unit freezes exact two-key response behavior and
the absence of DB, Redis, network, audit, and metrics side effects under
authoritative settings. This unit does not promote inventory evidence or close
P1-00b.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    _FENCE,
    install_p1_00_network_fence,
)

# Install the existing P1-00 fail-closed network fence before importing main.
install_p1_00_network_fence()

import main


class TestP100bCharacterizationMainPublicConfig(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for main.get_public_config handler and side effects."""

    def setUp(self) -> None:
        super().setUp()
        install_p1_00_network_fence()

    async def test_get_public_config_distinctive_strings_with_zero_side_effects(self) -> None:
        """Scenario 1: Configured distinctive string values for bot_username and frontend_url."""
        bot_username = "test_characterization_bot_user_c41"
        frontend_url = "https://frontend.char-scenario-1.test:8443/portal"

        orig_bot_username = main.settings.bot_username
        orig_frontend_url = main.settings.frontend_url
        orig_async_session = main.AsyncSessionLocal
        orig_init_db = main.init_db
        orig_init_redis = main.init_redis
        orig_close_redis = main.close_redis
        orig_get_redis_client = main.get_redis_client
        orig_audit_log = main.audit_log
        orig_metrics_response_body = main.metrics_response_body
        orig_uptime_seconds = main.uptime_seconds
        orig_registry = main.registry

        with (
            patch.object(main.settings, "bot_username", bot_username),
            patch.object(main.settings, "frontend_url", frontend_url),
            patch.object(main, "AsyncSessionLocal") as mock_async_session,
            patch.object(main, "init_db") as mock_init_db,
            patch.object(main, "init_redis") as mock_init_redis,
            patch.object(main, "close_redis") as mock_close_redis,
            patch.object(main, "get_redis_client") as mock_get_redis_client,
            patch.object(main, "audit_log") as mock_audit_log,
            patch.object(main, "metrics_response_body") as mock_metrics_response_body,
            patch.object(main, "uptime_seconds") as mock_uptime_seconds,
            patch.object(main, "registry") as mock_registry,
        ):
            denied_before = len(_FENCE["denied_connects"])
            redis_constructs_before = len(_FENCE["live_redis_constructs"])

            result = await main.get_public_config()

            self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
            self.assertEqual(len(_FENCE["live_redis_constructs"]), redis_constructs_before)

            # Assert returned object is exactly a plain dict with exact two keys
            self.assertIs(type(result), dict)
            self.assertEqual(
                result,
                {
                    "bot_username": bot_username,
                    "frontend_url": frontend_url,
                },
            )
            self.assertEqual(set(result.keys()), {"bot_username", "frontend_url"})
            self.assertEqual(result["bot_username"], bot_username)
            self.assertEqual(result["frontend_url"], frontend_url)

            # Assert no sensitive fields are present
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

            # Assert zero calls and zero side effects across all dependencies
            for mock_dep in (
                mock_async_session,
                mock_init_db,
                mock_init_redis,
                mock_close_redis,
                mock_get_redis_client,
                mock_audit_log,
                mock_metrics_response_body,
                mock_uptime_seconds,
                mock_registry,
            ):
                mock_dep.assert_not_called()
                self.assertEqual(mock_dep.mock_calls, [])

        # Assert settings and dependencies are restored after context manager exits
        self.assertEqual(main.settings.bot_username, orig_bot_username)
        self.assertEqual(main.settings.frontend_url, orig_frontend_url)
        self.assertIs(main.AsyncSessionLocal, orig_async_session)
        self.assertIs(main.init_db, orig_init_db)
        self.assertIs(main.init_redis, orig_init_redis)
        self.assertIs(main.close_redis, orig_close_redis)
        self.assertIs(main.get_redis_client, orig_get_redis_client)
        self.assertIs(main.audit_log, orig_audit_log)
        self.assertIs(main.metrics_response_body, orig_metrics_response_body)
        self.assertIs(main.uptime_seconds, orig_uptime_seconds)
        self.assertIs(main.registry, orig_registry)

    async def test_get_public_config_optional_bot_username_none_with_zero_side_effects(self) -> None:
        """Scenario 2: Valid optional bot_username=None with distinctive frontend_url."""
        bot_username = None
        frontend_url = "https://frontend.char-scenario-2.test:9443/app"

        orig_bot_username = main.settings.bot_username
        orig_frontend_url = main.settings.frontend_url
        orig_async_session = main.AsyncSessionLocal
        orig_init_db = main.init_db
        orig_init_redis = main.init_redis
        orig_close_redis = main.close_redis
        orig_get_redis_client = main.get_redis_client
        orig_audit_log = main.audit_log
        orig_metrics_response_body = main.metrics_response_body
        orig_uptime_seconds = main.uptime_seconds
        orig_registry = main.registry

        with (
            patch.object(main.settings, "bot_username", bot_username),
            patch.object(main.settings, "frontend_url", frontend_url),
            patch.object(main, "AsyncSessionLocal") as mock_async_session,
            patch.object(main, "init_db") as mock_init_db,
            patch.object(main, "init_redis") as mock_init_redis,
            patch.object(main, "close_redis") as mock_close_redis,
            patch.object(main, "get_redis_client") as mock_get_redis_client,
            patch.object(main, "audit_log") as mock_audit_log,
            patch.object(main, "metrics_response_body") as mock_metrics_response_body,
            patch.object(main, "uptime_seconds") as mock_uptime_seconds,
            patch.object(main, "registry") as mock_registry,
        ):
            denied_before = len(_FENCE["denied_connects"])
            redis_constructs_before = len(_FENCE["live_redis_constructs"])

            result = await main.get_public_config()

            self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
            self.assertEqual(len(_FENCE["live_redis_constructs"]), redis_constructs_before)

            # Assert returned object is exactly a plain dict with exact two keys
            self.assertIs(type(result), dict)
            self.assertEqual(
                result,
                {
                    "bot_username": None,
                    "frontend_url": frontend_url,
                },
            )
            self.assertEqual(set(result.keys()), {"bot_username", "frontend_url"})
            self.assertIsNone(result["bot_username"])
            self.assertEqual(result["frontend_url"], frontend_url)

            # Assert no sensitive fields are present
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

            # Assert zero calls and zero side effects across all dependencies
            for mock_dep in (
                mock_async_session,
                mock_init_db,
                mock_init_redis,
                mock_close_redis,
                mock_get_redis_client,
                mock_audit_log,
                mock_metrics_response_body,
                mock_uptime_seconds,
                mock_registry,
            ):
                mock_dep.assert_not_called()
                self.assertEqual(mock_dep.mock_calls, [])

        # Assert settings and dependencies are restored after context manager exits
        self.assertEqual(main.settings.bot_username, orig_bot_username)
        self.assertEqual(main.settings.frontend_url, orig_frontend_url)
        self.assertIs(main.AsyncSessionLocal, orig_async_session)
        self.assertIs(main.init_db, orig_init_db)
        self.assertIs(main.init_redis, orig_init_redis)
        self.assertIs(main.close_redis, orig_close_redis)
        self.assertIs(main.get_redis_client, orig_get_redis_client)
        self.assertIs(main.audit_log, orig_audit_log)
        self.assertIs(main.metrics_response_body, orig_metrics_response_body)
        self.assertIs(main.uptime_seconds, orig_uptime_seconds)
        self.assertIs(main.registry, orig_registry)


if __name__ == "__main__":
    unittest.main()
