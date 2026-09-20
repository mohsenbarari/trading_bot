"""Characterization tests for disable_push_subscription with real AsyncSession.

Seed: API-NOTIFY-DELETE-NOTIFICATIONS-DISABLE-PUSH-SUBSCRIPTION-L178
Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario: P1-00B.API.NOTIFY.DISABLE_PUSH_SUBSCRIPTION.OWNED_ENABLED_DISABLE_REAL_ASYNCSESSION

Explicit exclusions:
- Whitespace-empty, no-match, already-disabled, and mocked enabled-row cases already covered by tests/test_p1_00b_characterization_notification_disable_push_subscription.py.
- Other notification handlers; non-owner rows; duplicate endpoint constraints; retry, rollback, concurrency, reload/subprocess, origin-matrix cells, product/runtime source changes, existing-test edits, documentation/inventory/ledger/reconciliation/count changes, seed or blocker closure, Stage closure, production/operational data, network, deploy, DNS, Web Writer, secrets, and Git.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

# Canonical network fence must be installed before repository application imports.
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

import api.routers.notifications as notifications_module
from api.routers.notifications import (
    PushSubscriptionDelete,
    disable_push_subscription,
)
from core.web_push import hash_endpoint
from models.push_subscription import PushSubscription
from models.user import User
from tests.test_p1_00b_characterization_real_async_session_helper import (
    real_async_session,
)


class NotificationDisablePushSubscriptionRealAsyncSessionCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for owned enabled PushSubscription disable and persistence using real AsyncSession."""

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

        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        PushSubscription.__table__.create(bind=self.engine, checkfirst=True)
        self.addCleanup(self._dispose_engine)

    def _dispose_engine(self) -> None:
        try:
            PushSubscription.__table__.drop(bind=self.engine, checkfirst=True)
        finally:
            self.engine.dispose()

    async def test_owned_enabled_subscription_disables_and_persists_with_real_async_session(
        self,
    ) -> None:
        """Characterize owned enabled push subscription disable and persistence using real AsyncSession."""
        fixed_id = 88
        raw_endpoint = "   https://push.example.com/v1/sub-active-device-real-async-88   "
        trimmed_endpoint = "https://push.example.com/v1/sub-active-device-real-async-88"
        derived_endpoint_hash = hash_endpoint(trimmed_endpoint)
        fixed_p256dh = "synthetic-enabled-p256dh-key"
        fixed_auth = "synthetic-enabled-auth-secret"
        fixed_platform = "android-arm64"
        fixed_user_agent = "SyntheticMobile/2.0"
        fixed_failure_count = 1
        fixed_last_error = None

        # 1. Session 1: Persist one enabled PushSubscription owned by current_user.id
        async with real_async_session(self.engine) as session_1:
            initial_subscription = PushSubscription(
                id=fixed_id,
                user_id=self.current_user.id,
                endpoint=trimmed_endpoint,
                endpoint_hash=derived_endpoint_hash,
                p256dh=fixed_p256dh,
                auth=fixed_auth,
                platform=fixed_platform,
                user_agent=fixed_user_agent,
                enabled=True,
                failure_count=fixed_failure_count,
                last_error=fixed_last_error,
            )
            session_1.add(initial_subscription)
            await session_1.commit()
            self.assertIs(initial_subscription.enabled, True)

        # 2. Session 2: Call the actual disable_push_subscription handler with padded endpoint
        payload = PushSubscriptionDelete(endpoint=raw_endpoint)

        async with real_async_session(self.engine) as session_2:
            with mock.patch.object(
                notifications_module,
                "hash_endpoint",
                wraps=notifications_module.hash_endpoint,
            ) as spy_hash_endpoint, mock.patch.object(
                session_2,
                "execute",
                wraps=session_2.execute,
            ) as spy_execute, mock.patch.object(
                session_2,
                "commit",
                wraps=session_2.commit,
            ) as spy_commit:
                result = await disable_push_subscription(
                    payload=payload,
                    current_user=self.current_user,
                    db=session_2,
                )

            self.assertIsNone(result)
            spy_hash_endpoint.assert_called_once_with(trimmed_endpoint)
            self.assertEqual(spy_execute.await_count, 1)
            stmt = spy_execute.await_args.args[0]
            self.assertTrue(stmt.is_select)
            compiled_params = stmt.compile().params
            self.assertIn(derived_endpoint_hash, list(compiled_params.values()))
            self.assertIn(self.current_user.id, list(compiled_params.values()))
            self.assertEqual(spy_commit.await_count, 1)

            # Verify enabled mutated from True to False within session 2
            stmt_reload_s2 = select(PushSubscription).where(
                PushSubscription.id == fixed_id
            )
            res_2 = await session_2.execute(stmt_reload_s2)
            subscription_s2 = res_2.scalar_one_or_none()
            self.assertIsNotNone(subscription_s2)
            self.assertIs(subscription_s2.enabled, False)

        # 3. Session 3: In an independent real AsyncSession, reload and verify persistence
        async with real_async_session(self.engine) as session_3:
            stmt_reload_s3 = select(PushSubscription).where(
                PushSubscription.id == fixed_id
            )
            res_3 = await session_3.execute(stmt_reload_s3)
            persisted = res_3.scalar_one_or_none()

            self.assertIsNotNone(persisted)
            self.assertIs(persisted.enabled, False)
            self.assertEqual(persisted.id, fixed_id)
            self.assertEqual(persisted.user_id, self.current_user.id)
            self.assertEqual(persisted.endpoint, trimmed_endpoint)
            self.assertEqual(persisted.endpoint_hash, derived_endpoint_hash)
            self.assertEqual(persisted.p256dh, fixed_p256dh)
            self.assertEqual(persisted.auth, fixed_auth)
            self.assertEqual(persisted.platform, fixed_platform)
            self.assertEqual(persisted.user_agent, fixed_user_agent)
            self.assertEqual(persisted.failure_count, fixed_failure_count)
            self.assertEqual(persisted.last_error, fixed_last_error)
            self.assertIsNone(persisted.last_error)

        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()
        self.mock_get_current_user.assert_not_called()


if __name__ == "__main__":
    unittest.main()
