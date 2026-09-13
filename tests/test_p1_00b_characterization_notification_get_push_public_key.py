"""Characterization tests for get_push_public_key handler.

Seed: API-NOTIFY-GET-NOTIFICATIONS-GET-PUSH-PUBLIC-KEY-L133
Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001

This bounded characterization directly executes and freezes only the handler's
current pass-through and exception behavior for seed
API-NOTIFY-GET-NOTIFICATIONS-GET-PUSH-PUBLIC-KEY-L133.
This bounded characterization does not close P1-00b or the complete seed blocker.
"""

from __future__ import annotations

import unittest
from unittest import mock

# Install the existing P1-00 fail-closed network fence before application imports
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import api.routers.notifications as notifications_module  # noqa: E402
from api.routers.notifications import get_push_public_key  # noqa: E402


class NotificationGetPushPublicKeyCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for seed API-NOTIFY-GET-NOTIFICATIONS-GET-PUSH-PUBLIC-KEY-L133."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_get_db = mock.Mock(
            side_effect=AssertionError("Fail-fast: get_db called unexpectedly")
        )
        self.mock_get_redis = mock.Mock(
            side_effect=AssertionError("Fail-fast: get_redis called unexpectedly")
        )
        self._patcher_db = mock.patch.object(
            notifications_module, "get_db", self.mock_get_db
        )
        self._patcher_redis = mock.patch.object(
            notifications_module, "get_redis", self.mock_get_redis
        )
        self._patcher_db.start()
        self._patcher_redis.start()
        self.addCleanup(self._patcher_db.stop)
        self.addCleanup(self._patcher_redis.stop)

    async def test_get_push_public_key_configured_scenario(self) -> None:
        """Configured scenario: returns exact configured status payload without modification."""
        configured_payload = {
            "enabled": True,
            "public_key": "test-vapid-public-key-abcdef123456",
            "missing": [],
        }

        with mock.patch.object(
            notifications_module,
            "web_push_config_status",
            return_value=configured_payload,
        ) as mock_status:
            result = await get_push_public_key()

        self.assertIs(result, configured_payload)
        self.assertEqual(result, configured_payload)
        mock_status.assert_called_once_with()
        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()

    async def test_get_push_public_key_disabled_scenario(self) -> None:
        """Disabled scenario: returns exact disabled payload without normalization or copying."""
        disabled_payload = {
            "enabled": False,
            "public_key": None,
            "missing": ["SERVER_MODE_IRAN", "WEB_PUSH_ENABLED", "pywebpush"],
        }

        with mock.patch.object(
            notifications_module,
            "web_push_config_status",
            return_value=disabled_payload,
        ) as mock_status:
            result = await get_push_public_key()

        self.assertIs(result, disabled_payload)
        self.assertEqual(result, disabled_payload)
        mock_status.assert_called_once_with()
        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()

    async def test_get_push_public_key_failure_scenario_propagates_exception(self) -> None:
        """Failure scenario: identical exception instance propagates unchanged from handler."""
        error_sentinel = RuntimeError("distinctive-web-push-failure-instance")

        with mock.patch.object(
            notifications_module,
            "web_push_config_status",
            side_effect=error_sentinel,
        ) as mock_status:
            with self.assertRaises(RuntimeError) as context:
                await get_push_public_key()

        self.assertIs(context.exception, error_sentinel)
        mock_status.assert_called_once_with()
        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()


if __name__ == "__main__":
    unittest.main()
