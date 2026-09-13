"""Characterization tests for upsert_push_subscription handler.

Seed: API-NOTIFY-POST-NOTIFICATIONS-UPSERT-PUSH-SUBSCRIPTION-L138
Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001

This bounded characterization directly executes and freezes only the handler's
current unconfigured, trimmed-invalid, and enabled existing-subscription update
behavior for seed API-NOTIFY-POST-NOTIFICATIONS-UPSERT-PUSH-SUBSCRIPTION-L138.
This bounded characterization does not close P1-00b or the complete seed blocker.
"""

from __future__ import annotations

import hashlib
import unittest
from typing import Any
from unittest import mock

# Install the existing P1-00 fail-closed network fence before application imports
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException, Request  # noqa: E402
import api.routers.notifications as notifications_module  # noqa: E402
from api.routers.notifications import (  # noqa: E402
    PushSubscriptionKeys,
    PushSubscriptionUpsert,
    upsert_push_subscription,
)
from models.push_subscription import PushSubscription  # noqa: E402


def _make_request(user_agent: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if user_agent is not None:
        headers.append((b"user-agent", user_agent.encode("utf-8")))
    return Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/push/subscription",
            "headers": headers,
        }
    )


class ScalarOneOrNoneResult:
    """Hermetic result double for db.execute(...)."""

    def __init__(self, subscription: PushSubscription | None) -> None:
        self._subscription = subscription
        self.scalar_one_or_none_calls: int = 0

    def scalar_one_or_none(self) -> PushSubscription | None:
        self.scalar_one_or_none_calls += 1
        return self._subscription


class StrictFailClosedDatabaseSession:
    """Hermetic test double that fails closed on every database operation."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Fail-closed: db.execute called unexpectedly")

    def add(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("Fail-closed: db.add called unexpectedly")

    async def commit(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("Fail-closed: db.commit called unexpectedly")

    async def refresh(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("Fail-closed: db.refresh called unexpectedly")

    async def rollback(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("Fail-closed: db.rollback called unexpectedly")

    async def close(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("Fail-closed: db.close called unexpectedly")

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("Fail-closed: db.delete called unexpectedly")


class HermeticDatabaseSession:
    """Hermetic in-process test double for AsyncSession that records operations and fails closed."""

    def __init__(self, execute_result: Any = None) -> None:
        self.execute_calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        self.add_calls: list[Any] = []
        self.commit_calls: int = 0
        self.refresh_calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        self._execute_result = execute_result

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.execute_calls.append((statement, args, kwargs))
        if self._execute_result is not None:
            return self._execute_result
        raise AssertionError("Fail-closed: db.execute called unexpectedly")

    def add(self, instance: Any) -> None:
        self.add_calls.append(instance)
        raise AssertionError("Fail-closed: db.add called unexpectedly")

    async def commit(self) -> None:
        self.commit_calls += 1

    async def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.refresh_calls.append((instance, args, kwargs))

    async def rollback(self) -> None:
        raise AssertionError("Fail-closed: db.rollback called unexpectedly")

    async def close(self) -> None:
        raise AssertionError("Fail-closed: db.close called unexpectedly")

    async def delete(self, instance: Any) -> None:
        raise AssertionError("Fail-closed: db.delete called unexpectedly")


class NotificationUpsertPushSubscriptionCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for seed API-NOTIFY-POST-NOTIFICATIONS-UPSERT-PUSH-SUBSCRIPTION-L138."""

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

        self.current_user = mock.MagicMock(spec=notifications_module.User)
        self.current_user.id = 42

    async def test_upsert_push_subscription_unconfigured_raises_503(self) -> None:
        """Unconfigured scenario: raises HTTPException 503 without database or hashing actions."""
        disabled_payload = {
            "enabled": False,
            "public_key": None,
            "missing": ["SERVER_MODE_IRAN", "WEB_PUSH_ENABLED"],
        }
        valid_payload = PushSubscriptionUpsert(
            endpoint="  https://push.example.com/v1/sub-valid-endpoint  ",
            keys=PushSubscriptionKeys(
                p256dh="  valid-sample-p256dh-key  ",
                auth="  valid-sample-auth-secret  ",
            ),
            platform="android-mobile",
        )
        request = _make_request("Mozilla/5.0 (Android 14; Mobile) TestBrowser/1.0")
        db = StrictFailClosedDatabaseSession()

        with mock.patch.object(
            notifications_module,
            "web_push_config_status",
            return_value=disabled_payload,
        ) as mock_config_status, mock.patch.object(
            notifications_module,
            "hash_endpoint",
            wraps=notifications_module.hash_endpoint,
        ) as spy_hash_endpoint:
            with self.assertRaises(HTTPException) as ctx:
                await upsert_push_subscription(
                    payload=valid_payload,
                    request=request,
                    current_user=self.current_user,
                    db=db,
                )

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail, "Web Push is not configured")
        mock_config_status.assert_called_once_with()
        spy_hash_endpoint.assert_not_called()
        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()

    async def test_upsert_push_subscription_configured_whitespace_endpoint_raises_422(
        self,
    ) -> None:
        """Whitespace endpoint passes model min_length but strips empty, raising 422."""
        configured_payload = {
            "enabled": True,
            "public_key": "test-vapid-public-key-sample",
            "missing": [],
        }
        payload = PushSubscriptionUpsert(
            endpoint="   \t\n   ",
            keys=PushSubscriptionKeys(
                p256dh="valid-sample-p256dh-key",
                auth="valid-sample-auth-secret",
            ),
            platform="web",
        )
        request = _make_request("TestAgent/1.0")
        db = StrictFailClosedDatabaseSession()

        with mock.patch.object(
            notifications_module,
            "web_push_config_status",
            return_value=configured_payload,
        ) as mock_config_status, mock.patch.object(
            notifications_module,
            "hash_endpoint",
            wraps=notifications_module.hash_endpoint,
        ) as spy_hash_endpoint:
            with self.assertRaises(HTTPException) as ctx:
                await upsert_push_subscription(
                    payload=payload,
                    request=request,
                    current_user=self.current_user,
                    db=db,
                )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail, "Invalid push subscription")
        mock_config_status.assert_called_once_with()
        spy_hash_endpoint.assert_not_called()
        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()

    async def test_upsert_push_subscription_configured_whitespace_p256dh_raises_422(
        self,
    ) -> None:
        """Whitespace p256dh passes model min_length but strips empty, raising 422."""
        configured_payload = {
            "enabled": True,
            "public_key": "test-vapid-public-key-sample",
            "missing": [],
        }
        payload = PushSubscriptionUpsert(
            endpoint="https://push.example.com/v1/sub-device-123",
            keys=PushSubscriptionKeys(
                p256dh="   \t   ",
                auth="valid-sample-auth-secret",
            ),
            platform="web",
        )
        request = _make_request("TestAgent/1.0")
        db = StrictFailClosedDatabaseSession()

        with mock.patch.object(
            notifications_module,
            "web_push_config_status",
            return_value=configured_payload,
        ) as mock_config_status, mock.patch.object(
            notifications_module,
            "hash_endpoint",
            wraps=notifications_module.hash_endpoint,
        ) as spy_hash_endpoint:
            with self.assertRaises(HTTPException) as ctx:
                await upsert_push_subscription(
                    payload=payload,
                    request=request,
                    current_user=self.current_user,
                    db=db,
                )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail, "Invalid push subscription")
        mock_config_status.assert_called_once_with()
        spy_hash_endpoint.assert_not_called()
        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()

    async def test_upsert_push_subscription_configured_whitespace_auth_raises_422(
        self,
    ) -> None:
        """Whitespace auth passes model min_length but strips empty, raising 422."""
        configured_payload = {
            "enabled": True,
            "public_key": "test-vapid-public-key-sample",
            "missing": [],
        }
        payload = PushSubscriptionUpsert(
            endpoint="https://push.example.com/v1/sub-device-123",
            keys=PushSubscriptionKeys(
                p256dh="valid-sample-p256dh-key",
                auth="   \r\n\t   ",
            ),
            platform="web",
        )
        request = _make_request("TestAgent/1.0")
        db = StrictFailClosedDatabaseSession()

        with mock.patch.object(
            notifications_module,
            "web_push_config_status",
            return_value=configured_payload,
        ) as mock_config_status, mock.patch.object(
            notifications_module,
            "hash_endpoint",
            wraps=notifications_module.hash_endpoint,
        ) as spy_hash_endpoint:
            with self.assertRaises(HTTPException) as ctx:
                await upsert_push_subscription(
                    payload=payload,
                    request=request,
                    current_user=self.current_user,
                    db=db,
                )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail, "Invalid push subscription")
        mock_config_status.assert_called_once_with()
        spy_hash_endpoint.assert_not_called()
        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()

    async def test_upsert_push_subscription_configured_existing_subscription_updates_and_enables(
        self,
    ) -> None:
        """Configured existing-subscription branch updates all fields and enables the subscription."""
        configured_payload = {
            "enabled": True,
            "public_key": "test-vapid-public-key-sample",
            "missing": [],
        }
        raw_endpoint = "   https://push.example.com/v1/sub-device-99   "
        trimmed_endpoint = "https://push.example.com/v1/sub-device-99"
        raw_p256dh = "   sample-p256dh-trimmed-key   "
        trimmed_p256dh = "sample-p256dh-trimmed-key"
        raw_auth = "   sample-auth-trimmed-secret   "
        trimmed_auth = "sample-auth-trimmed-secret"
        raw_platform = "   desktop-linux-x86_64-platform-client   "
        expected_platform = raw_platform.strip()[:80]
        self.assertEqual(expected_platform, "desktop-linux-x86_64-platform-client")

        payload = PushSubscriptionUpsert(
            endpoint=raw_endpoint,
            keys=PushSubscriptionKeys(
                p256dh=raw_p256dh,
                auth=raw_auth,
            ),
            platform=raw_platform,
        )
        user_agent_header = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
        request = _make_request(user_agent_header)

        expected_endpoint_hash = hashlib.sha256(
            trimmed_endpoint.encode("utf-8")
        ).hexdigest()

        existing_subscription = PushSubscription(
            id=55,
            user_id=999,
            endpoint="https://push.example.com/v1/old-endpoint",
            endpoint_hash=expected_endpoint_hash,
            p256dh="old-p256dh-data",
            auth="old-auth-data",
            platform="old-platform-id",
            user_agent="OldBrowser/0.9",
            enabled=False,
            failure_count=7,
            last_error="410 Gone: expired endpoint",
        )

        execute_result = ScalarOneOrNoneResult(existing_subscription)
        db = HermeticDatabaseSession(execute_result=execute_result)

        with mock.patch.object(
            notifications_module,
            "web_push_config_status",
            return_value=configured_payload,
        ) as mock_config_status, mock.patch.object(
            notifications_module,
            "hash_endpoint",
            wraps=notifications_module.hash_endpoint,
        ) as spy_hash_endpoint:
            result = await upsert_push_subscription(
                payload=payload,
                request=request,
                current_user=self.current_user,
                db=db,
            )

        mock_config_status.assert_called_once_with()
        spy_hash_endpoint.assert_called_once_with(trimmed_endpoint)

        # Assert exactly one execute, commit, and refresh of that same instance
        self.assertEqual(len(db.execute_calls), 1)
        stmt, _, _ = db.execute_calls[0]
        compiled_params = stmt.compile().params
        self.assertIn(expected_endpoint_hash, list(compiled_params.values()))
        self.assertEqual(execute_result.scalar_one_or_none_calls, 1)

        self.assertEqual(db.add_calls, [])
        self.assertEqual(db.commit_calls, 1)
        self.assertEqual(len(db.refresh_calls), 1)
        self.assertIs(db.refresh_calls[0][0], existing_subscription)

        # Assert the same returned instance is updated with expected values
        self.assertIs(result, existing_subscription)
        self.assertEqual(existing_subscription.user_id, self.current_user.id)
        self.assertEqual(existing_subscription.endpoint, trimmed_endpoint)
        self.assertEqual(existing_subscription.p256dh, trimmed_p256dh)
        self.assertEqual(existing_subscription.auth, trimmed_auth)
        self.assertEqual(existing_subscription.platform, expected_platform)
        self.assertEqual(existing_subscription.user_agent, user_agent_header)
        self.assertIs(existing_subscription.enabled, True)
        self.assertIsNone(existing_subscription.last_error)

        # Assert unmutated fields retain their identity and values
        self.assertEqual(existing_subscription.id, 55)
        self.assertEqual(existing_subscription.endpoint_hash, expected_endpoint_hash)
        self.assertEqual(existing_subscription.failure_count, 7)

        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()

    async def test_upsert_push_subscription_configured_existing_subscription_none_platform_and_no_user_agent(
        self,
    ) -> None:
        """Existing subscription update handles None platform and missing request user-agent header."""
        configured_payload = {
            "enabled": True,
            "public_key": "test-vapid-public-key-sample",
            "missing": [],
        }
        raw_endpoint = "   https://push.example.com/v1/sub-device-100   "
        trimmed_endpoint = "https://push.example.com/v1/sub-device-100"
        raw_p256dh = "   p256dh-key-content   "
        trimmed_p256dh = "p256dh-key-content"
        raw_auth = "   auth-token-content   "
        trimmed_auth = "auth-token-content"

        payload = PushSubscriptionUpsert(
            endpoint=raw_endpoint,
            keys=PushSubscriptionKeys(
                p256dh=raw_p256dh,
                auth=raw_auth,
            ),
            platform=None,
        )
        request = _make_request(user_agent=None)

        expected_endpoint_hash = hashlib.sha256(
            trimmed_endpoint.encode("utf-8")
        ).hexdigest()

        existing_subscription = PushSubscription(
            id=60,
            user_id=123,
            endpoint="https://push.example.com/v1/previous-endpoint",
            endpoint_hash=expected_endpoint_hash,
            p256dh="prev-p256dh",
            auth="prev-auth",
            platform="previous-platform",
            user_agent="PreviousBrowser/2.0",
            enabled=False,
            failure_count=2,
            last_error="temporary timeout",
        )

        execute_result = ScalarOneOrNoneResult(existing_subscription)
        db = HermeticDatabaseSession(execute_result=execute_result)

        with mock.patch.object(
            notifications_module,
            "web_push_config_status",
            return_value=configured_payload,
        ) as mock_config_status, mock.patch.object(
            notifications_module,
            "hash_endpoint",
            wraps=notifications_module.hash_endpoint,
        ) as spy_hash_endpoint:
            result = await upsert_push_subscription(
                payload=payload,
                request=request,
                current_user=self.current_user,
                db=db,
            )

        mock_config_status.assert_called_once_with()
        spy_hash_endpoint.assert_called_once_with(trimmed_endpoint)

        self.assertEqual(len(db.execute_calls), 1)
        stmt, _, _ = db.execute_calls[0]
        compiled_params = stmt.compile().params
        self.assertIn(expected_endpoint_hash, list(compiled_params.values()))
        self.assertEqual(execute_result.scalar_one_or_none_calls, 1)

        self.assertEqual(db.add_calls, [])
        self.assertEqual(db.commit_calls, 1)
        self.assertEqual(len(db.refresh_calls), 1)
        self.assertIs(db.refresh_calls[0][0], existing_subscription)

        self.assertIs(result, existing_subscription)
        self.assertEqual(existing_subscription.user_id, self.current_user.id)
        self.assertEqual(existing_subscription.endpoint, trimmed_endpoint)
        self.assertEqual(existing_subscription.p256dh, trimmed_p256dh)
        self.assertEqual(existing_subscription.auth, trimmed_auth)
        self.assertIsNone(existing_subscription.platform)
        self.assertIsNone(existing_subscription.user_agent)
        self.assertIs(existing_subscription.enabled, True)
        self.assertIsNone(existing_subscription.last_error)

        self.assertEqual(existing_subscription.id, 60)
        self.assertEqual(existing_subscription.endpoint_hash, expected_endpoint_hash)
        self.assertEqual(existing_subscription.failure_count, 2)

        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()


if __name__ == "__main__":
    unittest.main()
