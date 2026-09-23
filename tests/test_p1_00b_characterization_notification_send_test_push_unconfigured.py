"""P1-00b characterization for send_test_push_notification (unconfigured and configured).

Provenance:
- Stage: P1-00b
- Assignment: WA-P1-00B-FREEZE-20260904-001
- Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
- Seed: API-NOTIFY-POST-NOTIFICATIONS-SEND-TEST-PUSH-NOTIFICATION-L201 (api/routers/notifications.py:send_test_push_notification)
- Scenarios:
  - unconfigured push failure before payload delivery or database work
  - unconfigured push failure with live unconfigured status
  - P1-00B-SINGLE.notification_send_test_push_configured_success: configured push builds exact payload and forwards delivery result

Exclusions and Non-Claims:
- No real database query or session execution
- No Web Push transport, HTTP calls, or socket activity
- No production-isolation evaluation or bypass
- No persistence, schema change, or table recreation
- No ledger reconciliation or inventory modification
- No blocker reduction or Stage closure
- No origin-matrix evidence, deploy, DNS, Writer, secrets, or external mutation
"""

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
from core.web_push import build_push_payload, web_push_config_status


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

    async def test_configured_push_builds_exact_payload_and_forwards_delivery_result(self):
        fence_denied_before = len(_FENCE["denied_connects"])
        fence_redis_before = len(_FENCE["live_redis_constructs"])

        call_order: list[str] = []

        configured_status = {
            "enabled": True,
            "public_key": "test-vapid-public-key",
            "missing": [],
        }

        def fake_web_push_config_status() -> dict[str, Any]:
            call_order.append("config_status")
            return configured_status

        def spy_build_push_payload(*args: Any, **kwargs: Any) -> dict[str, Any]:
            call_order.append("build_push_payload")
            return build_push_payload(*args, **kwargs)

        distinctive_send_result = {
            "total": 3,
            "sent": 2,
            "failed": 1,
            "disabled": 0,
        }

        async def fake_send_web_push(*args: Any, **kwargs: Any) -> dict[str, int]:
            call_order.append("send_web_push_to_user")
            return distinctive_send_result

        opaque_db = StrictDatabaseSentinel()
        current_user = SimpleNamespace(
            id=77,
            telegram_id=555123456,
            role=UserRole.STANDARD,
            account_name="configured_characterization_user",
        )

        with patch(
            "api.routers.notifications.web_push_config_status",
            side_effect=fake_web_push_config_status,
        ) as mock_config_status, patch(
            "api.routers.notifications.build_push_payload",
            wraps=spy_build_push_payload,
        ) as mock_build_payload, patch(
            "api.routers.notifications.send_web_push_to_user",
            new=AsyncMock(side_effect=fake_send_web_push),
        ) as mock_send_web_push:
            result = await send_test_push_notification(
                current_user=current_user,
                db=opaque_db,
            )

        # Distinctive send result returned unchanged
        self.assertIs(result, distinctive_send_result)
        self.assertEqual(result, distinctive_send_result)

        # Call counts and observable call order: config-status -> payload-build -> awaited send
        mock_config_status.assert_called_once_with()
        self.assertEqual(mock_build_payload.call_count, 1)
        mock_send_web_push.assert_awaited_once()

        self.assertEqual(
            call_order,
            ["config_status", "build_push_payload", "send_web_push_to_user"],
        )

        # Assert exact arguments passed to build_push_payload
        expected_title = "اعلان تست"
        expected_body = "تست دریافت اعلان دستگاه"
        expected_route = "/notifications"
        expected_tag = "push-test"
        expected_input_data = {"kind": "push_test"}

        mock_build_payload.assert_called_once_with(
            title=expected_title,
            body=expected_body,
            route=expected_route,
            tag=expected_tag,
            data=expected_input_data,
        )

        # Assert exact materialized payload including icon, badge, tag, route and route in data
        expected_materialized_payload = {
            "title": "اعلان تست",
            "body": "تست دریافت اعلان دستگاه",
            "icon": "/pwa-192x192.png",
            "badge": "/pwa-192x192.png",
            "tag": "push-test",
            "route": "/notifications",
            "data": {
                "kind": "push_test",
                "route": "/notifications",
            },
        }

        # Assert opaque db object and current_user.id forwarded exactly once to send_web_push_to_user
        mock_send_web_push.assert_awaited_once_with(
            opaque_db,
            current_user.id,
            expected_materialized_payload,
        )
        send_args, send_kwargs = mock_send_web_push.call_args
        self.assertIs(send_args[0], opaque_db)
        self.assertEqual(send_args[1], 77)
        self.assertEqual(send_args[2], expected_materialized_payload)
        self.assertEqual(send_args[2]["icon"], "/pwa-192x192.png")
        self.assertEqual(send_args[2]["badge"], "/pwa-192x192.png")
        self.assertEqual(send_args[2]["route"], "/notifications")
        self.assertEqual(send_args[2]["data"]["route"], "/notifications")
        self.assertEqual(send_args[2]["data"]["kind"], "push_test")

        # Prove network-fence counters remain unchanged
        self.assertEqual(len(_FENCE["denied_connects"]), fence_denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), fence_redis_before)


if __name__ == "__main__":
    unittest.main()
