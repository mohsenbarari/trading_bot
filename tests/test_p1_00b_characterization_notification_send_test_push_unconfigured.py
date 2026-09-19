"""P1-00b characterization for unconfigured send_test_push_notification."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    _FENCE,
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.notifications import send_test_push_notification
from core.enums import UserRole
from core.web_push import web_push_config_status


class StrictDatabaseSentinel:
    """Strict sentinel session that raises AssertionError on any attribute access or call."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(
            f"Strict database sentinel was touched: attribute {name!r} was accessed"
        )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Strict database sentinel was called")


class NotificationSendTestPushUnconfiguredCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_push_fails_before_payload_delivery_or_database_work(self):
        fence_denied_before = len(_FENCE["denied_connects"])
        fence_redis_before = len(_FENCE["live_redis_constructs"])

        strict_db = StrictDatabaseSentinel()
        current_user = SimpleNamespace(
            id=42,
            telegram_id=987654321,
            role=UserRole.STANDARD,
            account_name="test_characterization_user",
        )

        source_backed_disabled = {
            "enabled": False,
            "public_key": None,
            "missing": [
                "SERVER_MODE_IRAN",
                "WEB_PUSH_ENABLED",
                "WEB_PUSH_VAPID_PUBLIC_KEY",
                "WEB_PUSH_VAPID_PRIVATE_KEY",
                "WEB_PUSH_VAPID_SUBJECT",
                "pywebpush",
            ],
        }

        with patch(
            "api.routers.notifications.web_push_config_status",
            return_value=source_backed_disabled,
        ) as mock_config_status, patch(
            "api.routers.notifications.build_push_payload",
        ) as mock_build_payload, patch(
            "api.routers.notifications.send_web_push_to_user",
            new=AsyncMock(),
        ) as mock_send_web_push:
            with self.assertRaises(HTTPException) as raised:
                await send_test_push_notification(
                    current_user=current_user,
                    db=strict_db,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Web Push is not configured")
        mock_config_status.assert_called_once_with()
        mock_build_payload.assert_not_called()
        mock_send_web_push.assert_not_called()
        mock_send_web_push.assert_not_awaited()

        # Prove network-fence counters do not change
        self.assertEqual(len(_FENCE["denied_connects"]), fence_denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), fence_redis_before)

    async def test_unconfigured_push_fails_with_live_unconfigured_status(self):
        fence_denied_before = len(_FENCE["denied_connects"])
        fence_redis_before = len(_FENCE["live_redis_constructs"])

        live_status = web_push_config_status()
        self.assertFalse(live_status["enabled"])
        self.assertIsNone(live_status["public_key"])
        self.assertIsInstance(live_status["missing"], list)
        self.assertGreater(len(live_status["missing"]), 0)

        strict_db = StrictDatabaseSentinel()
        current_user = SimpleNamespace(
            id=88,
            telegram_id=12345678,
            role=UserRole.STANDARD,
            account_name="live_test_user",
        )

        with patch(
            "api.routers.notifications.build_push_payload",
        ) as mock_build_payload, patch(
            "api.routers.notifications.send_web_push_to_user",
            new=AsyncMock(),
        ) as mock_send_web_push:
            with self.assertRaises(HTTPException) as raised:
                await send_test_push_notification(
                    current_user=current_user,
                    db=strict_db,
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Web Push is not configured")
        mock_build_payload.assert_not_called()
        mock_send_web_push.assert_not_called()
        mock_send_web_push.assert_not_awaited()

        # Prove network-fence counters do not change
        self.assertEqual(len(_FENCE["denied_connects"]), fence_denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), fence_redis_before)


if __name__ == "__main__":
    unittest.main()
