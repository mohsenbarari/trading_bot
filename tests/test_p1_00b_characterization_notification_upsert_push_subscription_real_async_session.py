"""Characterization tests for upsert_push_subscription with real AsyncSession.

Seed: API-NOTIFY-POST-NOTIFICATIONS-UPSERT-PUSH-SUBSCRIPTION-L138
Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario: P1-00B.API.NOTIFY.UPSERT_PUSH_SUBSCRIPTION.CONFIGURED_NEW_CREATE_REAL_ASYNCSESSION

Explicit exclusions:
- Lookup-hit/update, invalid/unconfigured, delete/disable, duplicate endpoint, retry/rollback/concurrency, reload/subprocess, origin-matrix, production/runtime source changes, existing-test edits, docs/inventory/ledger/reconciliation/count changes, blocker or Stage closure, network, deployment, DNS, Web Writer, secrets, and Git.
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

from fastapi import Request
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

import api.routers.notifications as notifications_module
from api.routers.notifications import (
    PushSubscriptionKeys,
    PushSubscriptionUpsert,
    upsert_push_subscription,
)
from core.web_push import hash_endpoint
from models.push_subscription import PushSubscription
from models.user import User
from tests.test_p1_00b_characterization_real_async_session_helper import (
    real_async_session,
)


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


class NotificationUpsertPushSubscriptionRealAsyncSessionCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for new PushSubscription creation and persistence using real AsyncSession."""

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

    async def test_configured_new_subscription_creates_and_persists_with_real_async_session(
        self,
    ) -> None:
        """Characterize configured lookup-miss branch creating and persisting a PushSubscription with real AsyncSession."""
        configured_config = {
            "enabled": True,
            "public_key": "synthetic-vapid-public-key-characterization-c42",
            "missing": [],
        }
        raw_endpoint = "   https://push.example.com/v1/sub-new-device-real-async-42   "
        trimmed_endpoint = "https://push.example.com/v1/sub-new-device-real-async-42"
        raw_p256dh = "   synthetic-p256dh-key-material-42   "
        trimmed_p256dh = "synthetic-p256dh-key-material-42"
        raw_auth = "   synthetic-auth-secret-42   "
        trimmed_auth = "synthetic-auth-secret-42"
        raw_platform = "   desktop-linux-x86_64-client-c42   "
        expected_platform = raw_platform.strip()[:80]
        fixed_user_agent = "Mozilla/5.0 (SyntheticAgent/1.0; RealAsyncSessionTest)"

        expected_endpoint_hash = hash_endpoint(trimmed_endpoint)

        payload = PushSubscriptionUpsert(
            endpoint=raw_endpoint,
            keys=PushSubscriptionKeys(
                p256dh=raw_p256dh,
                auth=raw_auth,
            ),
            platform=raw_platform,
        )
        request = _make_request(fixed_user_agent)

        # 1. Handler invocation in primary real AsyncSession
        async with real_async_session(self.engine) as handler_session:
            with mock.patch.object(
                notifications_module,
                "web_push_config_status",
                return_value=configured_config,
            ) as mock_config_status, mock.patch.object(
                notifications_module,
                "hash_endpoint",
                wraps=notifications_module.hash_endpoint,
            ) as spy_hash_endpoint, mock.patch.object(
                handler_session,
                "execute",
                wraps=handler_session.execute,
            ) as spy_execute, mock.patch.object(
                handler_session,
                "add",
                wraps=handler_session.add,
            ) as spy_add, mock.patch.object(
                handler_session,
                "commit",
                wraps=handler_session.commit,
            ) as spy_commit, mock.patch.object(
                handler_session,
                "refresh",
                wraps=handler_session.refresh,
            ) as spy_refresh:
                result = await upsert_push_subscription(
                    payload=payload,
                    request=request,
                    current_user=self.current_user,
                    db=handler_session,
                )

            mock_config_status.assert_called_once_with()
            spy_hash_endpoint.assert_called_once_with(trimmed_endpoint)

            self.assertEqual(spy_execute.await_count, 1)
            stmt = spy_execute.await_args.args[0]
            self.assertTrue(stmt.is_select)
            compiled_params = stmt.compile().params
            self.assertIn(expected_endpoint_hash, list(compiled_params.values()))

            self.assertEqual(len(spy_add.mock_calls), 1)
            added_instance = spy_add.call_args[0][0]
            self.assertIsInstance(added_instance, PushSubscription)
            self.assertIs(result, added_instance)

            self.assertEqual(spy_commit.await_count, 1)
            self.assertEqual(spy_refresh.await_count, 1)
            self.assertIs(spy_refresh.await_args.args[0], added_instance)

            # Assert live returned PushSubscription fields
            self.assertIsNotNone(result.id)
            self.assertIsInstance(result.id, int)
            self.assertGreater(result.id, 0)
            self.assertEqual(result.user_id, self.current_user.id)
            self.assertEqual(result.endpoint, trimmed_endpoint)
            self.assertEqual(result.endpoint_hash, expected_endpoint_hash)
            self.assertEqual(result.p256dh, trimmed_p256dh)
            self.assertEqual(result.auth, trimmed_auth)
            self.assertEqual(result.platform, expected_platform)
            self.assertEqual(result.user_agent, fixed_user_agent)
            self.assertIs(result.enabled, True)
            self.assertEqual(result.failure_count, 0)
            self.assertIsNone(result.last_error)
            self.assertIsNone(result.last_success_at)
            self.assertIsNone(result.last_failure_at)
            self.assertIsNotNone(result.created_at)
            self.assertIsNotNone(result.updated_at)

            created_subscription_id = result.id
            created_at_val = result.created_at
            updated_at_val = result.updated_at

        # 2. Independent real AsyncSession verification
        async with real_async_session(self.engine) as reload_session:
            stmt_all = select(PushSubscription)
            res_all = await reload_session.execute(stmt_all)
            persisted_records = res_all.scalars().all()

            self.assertEqual(len(persisted_records), 1)
            persisted = persisted_records[0]

            self.assertEqual(persisted.id, created_subscription_id)
            self.assertEqual(persisted.user_id, self.current_user.id)
            self.assertEqual(persisted.endpoint, trimmed_endpoint)
            self.assertEqual(persisted.endpoint_hash, expected_endpoint_hash)
            self.assertEqual(persisted.p256dh, trimmed_p256dh)
            self.assertEqual(persisted.auth, trimmed_auth)
            self.assertEqual(persisted.platform, expected_platform)
            self.assertEqual(persisted.user_agent, fixed_user_agent)
            self.assertIs(persisted.enabled, True)
            self.assertEqual(persisted.failure_count, 0)
            self.assertIsNone(persisted.last_error)
            self.assertIsNone(persisted.last_success_at)
            self.assertIsNone(persisted.last_failure_at)
            self.assertIsNotNone(persisted.created_at)
            self.assertIsNotNone(persisted.updated_at)
            self.assertEqual(persisted.created_at, created_at_val)
            self.assertEqual(persisted.updated_at, updated_at_val)

        self.mock_get_db.assert_not_called()
        self.mock_get_redis.assert_not_called()
        self.mock_get_current_user.assert_not_called()


if __name__ == "__main__":
    unittest.main()
