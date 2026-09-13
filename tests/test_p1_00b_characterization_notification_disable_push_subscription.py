"""Characterization tests for disable_push_subscription handler.

Seed: API-NOTIFY-DELETE-NOTIFICATIONS-DISABLE-PUSH-SUBSCRIPTION-L178
Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001

This bounded characterization directly executes and freezes only the handler's
current trimmed-empty no-op, no-match/already-disabled no-commit, and enabled
own-subscription disable-and-commit behavior for seed
API-NOTIFY-DELETE-NOTIFICATIONS-DISABLE-PUSH-SUBSCRIPTION-L178.
This bounded characterization does not close Stage P1-00b or the complete seed blocker.
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

import api.routers.notifications as notifications_module  # noqa: E402
from api.routers.notifications import (  # noqa: E402
    PushSubscriptionDelete,
    disable_push_subscription,
)
from models.push_subscription import PushSubscription  # noqa: E402
from models.user import User  # noqa: E402


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

    def __init__(self) -> None:
        self.execute_calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        self.add_calls: list[Any] = []
        self.commit_calls: int = 0
        self.refresh_calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        self.rollback_calls: int = 0
        self.close_calls: int = 0
        self.delete_calls: list[Any] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.execute_calls.append((statement, args, kwargs))
        raise AssertionError("Fail-closed: db.execute called unexpectedly")

    def add(self, *args: Any, **kwargs: Any) -> None:
        self.add_calls.append((args, kwargs))
        raise AssertionError("Fail-closed: db.add called unexpectedly")

    async def commit(self, *args: Any, **kwargs: Any) -> None:
        self.commit_calls += 1
        raise AssertionError("Fail-closed: db.commit called unexpectedly")

    async def refresh(self, *args: Any, **kwargs: Any) -> None:
        self.refresh_calls.append((args, kwargs))
        raise AssertionError("Fail-closed: db.refresh called unexpectedly")

    async def rollback(self, *args: Any, **kwargs: Any) -> None:
        self.rollback_calls += 1
        raise AssertionError("Fail-closed: db.rollback called unexpectedly")

    async def close(self, *args: Any, **kwargs: Any) -> None:
        self.close_calls += 1
        raise AssertionError("Fail-closed: db.close called unexpectedly")

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        self.delete_calls.append((args, kwargs))
        raise AssertionError("Fail-closed: db.delete called unexpectedly")


class HermeticDatabaseSession:
    """Hermetic in-process test double for AsyncSession that records operations and fails closed."""

    def __init__(self, execute_result: Any = None) -> None:
        self.execute_calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        self.add_calls: list[Any] = []
        self.commit_calls: int = 0
        self.refresh_calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        self.rollback_calls: int = 0
        self.close_calls: int = 0
        self.delete_calls: list[Any] = []
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
        raise AssertionError("Fail-closed: db.refresh called unexpectedly")

    async def rollback(self) -> None:
        self.rollback_calls += 1
        raise AssertionError("Fail-closed: db.rollback called unexpectedly")

    async def close(self) -> None:
        self.close_calls += 1
        raise AssertionError("Fail-closed: db.close called unexpectedly")

    async def delete(self, instance: Any) -> None:
        self.delete_calls.append(instance)
        raise AssertionError("Fail-closed: db.delete called unexpectedly")


class NotificationDisablePushSubscriptionCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for seed API-NOTIFY-DELETE-NOTIFICATIONS-DISABLE-PUSH-SUBSCRIPTION-L178."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_get_db = mock.Mock(
            side_effect=AssertionError("Fail-fast: get_db called unexpectedly")
        )
        self.mock_get_redis = mock.Mock(
            side_effect=AssertionError("Fail-fast: get_redis called unexpectedly")
        )
        self.mock_get_current_user = mock.Mock(
            side_effect=AssertionError("Fail-fast: get_current_user called unexpectedly")
        )
        self._patcher_db = mock.patch.object(
            notifications_module, "get_db", self.mock_get_db
        )
        self._patcher_redis = mock.patch.object(
            notifications_module, "get_redis", self.mock_get_redis
        )
        self._patcher_user = mock.patch.object(
            notifications_module, "get_current_user", self.mock_get_current_user
        )
        self._patcher_db.start()
        self._patcher_redis.start()
        self._patcher_user.start()
        self.addCleanup(self._patcher_db.stop)
        self.addCleanup(self._patcher_redis.stop)
        self.addCleanup(self._patcher_user.stop)

        self.current_user = mock.MagicMock(spec=User)
        self.current_user.id = 42

    async def test_disable_push_subscription_whitespace_endpoint_returns_none_and_no_db_or_hash(
        self,
    ) -> None:
        """Whitespace endpoint passes model min_length but trims empty: returns None without hashing or DB operations."""
        payload = PushSubscriptionDelete(endpoint="   \t\r\n   ")
        db = StrictFailClosedDatabaseSession()

        with mock.patch.object(
            notifications_module,
            "hash_endpoint",
            wraps=notifications_module.hash_endpoint,
        ) as spy_hash_endpoint:
            result = await disable_push_subscription(
                payload=payload,
                current_user=self.current_user,
                db=db,
            )

        self.assertIsNone(result)
        spy_hash_endpoint.assert_not_called()
        self.assertEqual(db.execute_calls, [])
        self.assertEqual(db.add_calls, [])
        self.assertEqual(db.commit_calls, 0)
        self.assertEqual(db.refresh_calls, [])
        self.assertEqual(db.rollback_calls, 0)
        self.assertEqual(db.close_calls, 0)
        self.assertEqual(db.delete_calls, [])
        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()
        self.mock_get_current_user.assert_not_called()

    async def test_disable_push_subscription_nonempty_endpoint_no_match_returns_none_without_commit(
        self,
    ) -> None:
        """Nonempty endpoint with no matching row: queries DB with hash and user_id, returns None, no commit."""
        raw_endpoint = "   https://push.example.com/v1/sub-not-found-token-123   "
        trimmed_endpoint = "https://push.example.com/v1/sub-not-found-token-123"
        expected_endpoint_hash = hashlib.sha256(
            trimmed_endpoint.encode("utf-8")
        ).hexdigest()

        payload = PushSubscriptionDelete(endpoint=raw_endpoint)
        execute_result = ScalarOneOrNoneResult(None)
        db = HermeticDatabaseSession(execute_result=execute_result)

        with mock.patch.object(
            notifications_module,
            "hash_endpoint",
            wraps=notifications_module.hash_endpoint,
        ) as spy_hash_endpoint:
            result = await disable_push_subscription(
                payload=payload,
                current_user=self.current_user,
                db=db,
            )

        self.assertIsNone(result)
        spy_hash_endpoint.assert_called_once_with(trimmed_endpoint)

        # Assert exactly one SELECT is executed and scalar_one_or_none is consumed once
        self.assertEqual(len(db.execute_calls), 1)
        stmt, _, _ = db.execute_calls[0]
        self.assertTrue(stmt.is_select)
        compiled_params = stmt.compile().params
        self.assertIn(expected_endpoint_hash, list(compiled_params.values()))
        self.assertIn(self.current_user.id, list(compiled_params.values()))
        self.assertEqual(execute_result.scalar_one_or_none_calls, 1)

        # Assert no commit or unrelated database action occurs
        self.assertEqual(db.commit_calls, 0)
        self.assertEqual(db.add_calls, [])
        self.assertEqual(db.refresh_calls, [])
        self.assertEqual(db.rollback_calls, 0)
        self.assertEqual(db.close_calls, 0)
        self.assertEqual(db.delete_calls, [])
        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()
        self.mock_get_current_user.assert_not_called()

    async def test_disable_push_subscription_nonempty_endpoint_already_disabled_returns_none_without_commit(
        self,
    ) -> None:
        """Nonempty endpoint with already-disabled subscription: returns None, no commit, row unchanged."""
        raw_endpoint = "   https://push.example.com/v1/sub-already-disabled-456   "
        trimmed_endpoint = "https://push.example.com/v1/sub-already-disabled-456"
        expected_endpoint_hash = hashlib.sha256(
            trimmed_endpoint.encode("utf-8")
        ).hexdigest()

        existing_subscription = PushSubscription(
            id=77,
            user_id=self.current_user.id,
            endpoint=trimmed_endpoint,
            endpoint_hash=expected_endpoint_hash,
            p256dh="synthetic-disabled-p256dh-key",
            auth="synthetic-disabled-auth-secret",
            platform="linux-x86_64",
            user_agent="SyntheticBrowser/1.0",
            enabled=False,
            failure_count=3,
            last_error="previous-sync-error",
        )

        payload = PushSubscriptionDelete(endpoint=raw_endpoint)
        execute_result = ScalarOneOrNoneResult(existing_subscription)
        db = HermeticDatabaseSession(execute_result=execute_result)

        with mock.patch.object(
            notifications_module,
            "hash_endpoint",
            wraps=notifications_module.hash_endpoint,
        ) as spy_hash_endpoint:
            result = await disable_push_subscription(
                payload=payload,
                current_user=self.current_user,
                db=db,
            )

        self.assertIsNone(result)
        spy_hash_endpoint.assert_called_once_with(trimmed_endpoint)

        # Assert exactly one SELECT is executed and scalar_one_or_none is consumed once
        self.assertEqual(len(db.execute_calls), 1)
        stmt, _, _ = db.execute_calls[0]
        self.assertTrue(stmt.is_select)
        compiled_params = stmt.compile().params
        self.assertIn(expected_endpoint_hash, list(compiled_params.values()))
        self.assertIn(self.current_user.id, list(compiled_params.values()))
        self.assertEqual(execute_result.scalar_one_or_none_calls, 1)

        # Assert no commit or unrelated database action occurs
        self.assertEqual(db.commit_calls, 0)
        self.assertEqual(db.add_calls, [])
        self.assertEqual(db.refresh_calls, [])
        self.assertEqual(db.rollback_calls, 0)
        self.assertEqual(db.close_calls, 0)
        self.assertEqual(db.delete_calls, [])

        # Assert disabled row state and representative unrelated fields remain unchanged
        self.assertIs(existing_subscription.enabled, False)
        self.assertEqual(existing_subscription.id, 77)
        self.assertEqual(existing_subscription.user_id, self.current_user.id)
        self.assertEqual(existing_subscription.endpoint, trimmed_endpoint)
        self.assertEqual(existing_subscription.endpoint_hash, expected_endpoint_hash)
        self.assertEqual(existing_subscription.p256dh, "synthetic-disabled-p256dh-key")
        self.assertEqual(existing_subscription.auth, "synthetic-disabled-auth-secret")
        self.assertEqual(existing_subscription.platform, "linux-x86_64")
        self.assertEqual(existing_subscription.user_agent, "SyntheticBrowser/1.0")
        self.assertEqual(existing_subscription.failure_count, 3)
        self.assertEqual(existing_subscription.last_error, "previous-sync-error")

        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()
        self.mock_get_current_user.assert_not_called()

    async def test_disable_push_subscription_nonempty_endpoint_enabled_row_disables_and_commits(
        self,
    ) -> None:
        """Nonempty endpoint with enabled subscription: binds hash/owner, flips enabled to False, commits, returns None."""
        raw_endpoint = "   https://push.example.com/v1/sub-active-device-789   "
        trimmed_endpoint = "https://push.example.com/v1/sub-active-device-789"
        expected_endpoint_hash = hashlib.sha256(
            trimmed_endpoint.encode("utf-8")
        ).hexdigest()

        existing_subscription = PushSubscription(
            id=88,
            user_id=self.current_user.id,
            endpoint=trimmed_endpoint,
            endpoint_hash=expected_endpoint_hash,
            p256dh="synthetic-enabled-p256dh-key",
            auth="synthetic-enabled-auth-secret",
            platform="android-arm64",
            user_agent="SyntheticMobile/2.0",
            enabled=True,
            failure_count=1,
            last_error=None,
        )

        payload = PushSubscriptionDelete(endpoint=raw_endpoint)
        execute_result = ScalarOneOrNoneResult(existing_subscription)
        db = HermeticDatabaseSession(execute_result=execute_result)

        with mock.patch.object(
            notifications_module,
            "hash_endpoint",
            wraps=notifications_module.hash_endpoint,
        ) as spy_hash_endpoint:
            result = await disable_push_subscription(
                payload=payload,
                current_user=self.current_user,
                db=db,
            )

        self.assertIsNone(result)
        spy_hash_endpoint.assert_called_once_with(trimmed_endpoint)

        # Assert query/hash/owner bindings and single SELECT
        self.assertEqual(len(db.execute_calls), 1)
        stmt, _, _ = db.execute_calls[0]
        self.assertTrue(stmt.is_select)
        compiled_params = stmt.compile().params
        self.assertIn(expected_endpoint_hash, list(compiled_params.values()))
        self.assertIn(self.current_user.id, list(compiled_params.values()))
        self.assertEqual(execute_result.scalar_one_or_none_calls, 1)

        # Assert the same row changes enabled from True to False
        self.assertIs(existing_subscription.enabled, False)

        # Assert exactly one commit occurs
        self.assertEqual(db.commit_calls, 1)

        # Assert no add, refresh, rollback, close, delete, or unrelated field mutation occurs
        self.assertEqual(db.add_calls, [])
        self.assertEqual(db.refresh_calls, [])
        self.assertEqual(db.rollback_calls, 0)
        self.assertEqual(db.close_calls, 0)
        self.assertEqual(db.delete_calls, [])

        self.assertEqual(existing_subscription.id, 88)
        self.assertEqual(existing_subscription.user_id, self.current_user.id)
        self.assertEqual(existing_subscription.endpoint, trimmed_endpoint)
        self.assertEqual(existing_subscription.endpoint_hash, expected_endpoint_hash)
        self.assertEqual(existing_subscription.p256dh, "synthetic-enabled-p256dh-key")
        self.assertEqual(existing_subscription.auth, "synthetic-enabled-auth-secret")
        self.assertEqual(existing_subscription.platform, "android-arm64")
        self.assertEqual(existing_subscription.user_agent, "SyntheticMobile/2.0")
        self.assertEqual(existing_subscription.failure_count, 1)
        self.assertIsNone(existing_subscription.last_error)

        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()
        self.mock_get_current_user.assert_not_called()


if __name__ == "__main__":
    unittest.main()
