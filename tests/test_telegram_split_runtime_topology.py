"""Desired one-executor split topology.

This test is written against the required architecture. On the broken
split (primary and publishers both building a Queue worker and both
calling the same global owner acquire) it fails. After the fix it
proves the new topology is runnable.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from core.telegram_bot_runtime_role import (
    TELEGRAM_BOT_RUNTIME_ROLE_ALL,
    TELEGRAM_BOT_RUNTIME_ROLE_EXECUTOR,
    TELEGRAM_BOT_RUNTIME_ROLE_PRIMARY,
    TelegramBotRuntimeRoleError,
    assert_telegram_bot_runtime_role_compatible,
    role_owns_local_ack_surface,
    role_owns_otp_worker,
    role_owns_primary_surface,
    role_owns_publisher_surface,
    role_owns_queue_executor,
    select_polling_bot_identities,
    select_queue_execution_bot_identities,
)
from core.telegram_bot_runtime_topology import (
    describe_telegram_bot_runtime_topology,
)
from core.telegram_delivery_queue_owner import acquire_telegram_delivery_queue_owner
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)
from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES
from run_bot import (
    configured_telegram_delivery_queue_worker_factory,
    telegram_execution_worker_factories,
)
from tests.test_telegram_delivery_queue_config import _settings


_ALL_IDENTITIES = ("primary", "channel_editor", *TELEGRAM_PUBLISHER_IDENTITIES)
_QUEUE_V1 = TelegramDeliveryRuntimeDecision(
    mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
    legacy_workers_enabled=False,
    queue_worker_enabled=True,
)
_LEGACY = TelegramDeliveryRuntimeDecision(
    mode=TelegramDeliveryRuntimeMode.LEGACY,
    legacy_workers_enabled=True,
    queue_worker_enabled=False,
)


def _queue_settings(**overrides):
    values = {
        "bot_token": "primary:test-token",
        "channel_id": -1001234567890,
        "redis_url": "redis://queue.test/15",
        "telegram_bot_runtime_role": "all",
        "telegram_bot_split_enabled": False,
        "telegram_multi_publisher_enabled": True,
        "telegram_b2b_dispatch_enabled": True,
        "telegram_delivery_queue_channel_editor_enabled": False,
        "telegram_delivery_queue_channel_editor_bot_token": None,
        "telegram_delivery_queue_bot_min_interval_seconds": 0.035,
        "telegram_delivery_queue_destination_min_interval_seconds": 1.05,
        "telegram_delivery_queue_rate_limit_probe_delay_seconds": 0.1,
        "telegram_delivery_queue_global_rate_limit_window_seconds": 2.0,
        "telegram_delivery_queue_worker_lease_seconds": 30.0,
        "telegram_delivery_queue_worker_request_timeout_seconds": 10.0,
        "telegram_delivery_queue_limiter_key_ttl_seconds": 86400,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TelegramSplitRuntimeContradictionTests(unittest.TestCase):
    def test_split_roles_cannot_both_build_a_queue_worker(self):
        """Current broken split fails here: both roles return a Queue worker."""
        primary = telegram_execution_worker_factories(
            _QUEUE_V1,
            settings_obj=_queue_settings(
                telegram_bot_runtime_role="primary",
                telegram_bot_split_enabled=True,
            ),
            runtime_role="primary",
        )
        with patch(
            "run_bot.configured_telegram_delivery_queue_worker_factory",
            return_value=lambda: None,
        ) as factory:
            executor = telegram_execution_worker_factories(
                _QUEUE_V1,
                settings_obj=_queue_settings(
                    telegram_bot_runtime_role="executor",
                    telegram_bot_split_enabled=True,
                ),
                runtime_role="executor",
            )

        self.assertEqual(primary, ())
        self.assertEqual(len(executor), 2)
        factory.assert_called_once()
        self.assertEqual(executor[1].__name__, "run_telegram_otp_ephemeral_worker")
        self.assertFalse(role_owns_queue_executor("primary"))
        self.assertTrue(role_owns_queue_executor("executor"))
        self.assertFalse(role_owns_otp_worker("primary"))
        self.assertTrue(role_owns_otp_worker("executor"))

    def test_primary_factory_must_not_bind_queue_lanes_or_call_acquire(self):
        with self.assertRaises(TelegramDeliveryRuntimeConfigurationError):
            configured_telegram_delivery_queue_worker_factory(
                _queue_settings(
                    telegram_bot_runtime_role="primary",
                    telegram_bot_split_enabled=True,
                ),
                runtime_role="primary",
            )

        acquire_source = acquire_telegram_delivery_queue_owner.__code__.co_filename
        from pathlib import Path
        import inspect
        from run_bot import telegram_execution_worker_factories as factories

        source = inspect.getsource(factories)
        self.assertNotIn("acquire_telegram_delivery_queue_owner", source)
        self.assertTrue(
            Path(acquire_source).name.endswith("telegram_delivery_queue_owner.py")
        )

    def test_executor_queue_worker_binds_every_identity(self):
        composition = SimpleNamespace(
            bot_identities=_ALL_IDENTITIES,
            freshness_validators={identity: object() for identity in _ALL_IDENTITIES},
            lifecycle_feedbacks={identity: object() for identity in _ALL_IDENTITIES},
            credential_registry=SimpleNamespace(bot_identities=_ALL_IDENTITIES),
        )
        settings_obj = _queue_settings(
            telegram_bot_runtime_role="executor",
            telegram_bot_split_enabled=True,
        )

        with (
            patch(
                "run_bot.build_configured_telegram_delivery_runtime",
                return_value=composition,
            ),
            patch("run_bot.redis.Redis.from_url") as redis_from_url,
            patch("run_bot.configured_redis_telegram_delivery_limiter"),
            patch("run_bot.telegram_delivery_queue_loop", new=AsyncMock()) as loop,
        ):
            redis_client = AsyncMock()
            redis_from_url.return_value = redis_client
            runner = configured_telegram_delivery_queue_worker_factory(
                settings_obj,
                runtime_role="executor",
            )
            import asyncio

            asyncio.run(runner())

        self.assertEqual(loop.await_args.kwargs["bot_identities"], _ALL_IDENTITIES)
        self.assertEqual(
            set(loop.await_args.kwargs["freshness_validators"]),
            set(_ALL_IDENTITIES),
        )

    def test_second_queue_owner_is_still_the_same_global_lock(self):
        from core.telegram_delivery_queue_owner import (
            TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY,
        )

        self.assertEqual(TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY, 0x5447515545554531)
        self.assertFalse(role_owns_queue_executor("primary"))
        self.assertTrue(role_owns_queue_executor("all"))
        self.assertTrue(role_owns_queue_executor("executor"))


class TelegramSplitRuntimeRoleMatrixTests(unittest.TestCase):
    def test_role_surface_matrix(self):
        rows = {
            "all": (True, True, True, True, True),
            "primary": (True, False, False, False, True),
            "executor": (False, True, True, True, False),
        }
        for role, expected in rows.items():
            self.assertEqual(
                (
                    role_owns_primary_surface(role),
                    role_owns_publisher_surface(role),
                    role_owns_queue_executor(role),
                    role_owns_otp_worker(role),
                    role_owns_local_ack_surface(role),
                ),
                expected,
                role,
            )

    def test_polling_and_execution_identities_are_separate(self):
        self.assertEqual(
            select_polling_bot_identities("primary", _ALL_IDENTITIES),
            ("primary",),
        )
        self.assertEqual(
            select_polling_bot_identities("executor", _ALL_IDENTITIES),
            TELEGRAM_PUBLISHER_IDENTITIES,
        )
        self.assertEqual(
            select_queue_execution_bot_identities("primary", _ALL_IDENTITIES),
            (),
        )
        self.assertEqual(
            select_queue_execution_bot_identities("executor", _ALL_IDENTITIES),
            _ALL_IDENTITIES,
        )
        self.assertEqual(
            select_queue_execution_bot_identities("all", _ALL_IDENTITIES),
            _ALL_IDENTITIES,
        )

    def test_unknown_and_retired_publishers_role_fail_closed(self):
        with self.assertRaises(TelegramBotRuntimeRoleError):
            role_owns_queue_executor("sidecar")
        with self.assertRaises(TelegramBotRuntimeRoleError):
            role_owns_queue_executor("publishers")
        with self.assertRaises(Exception):
            _settings(telegram_bot_runtime_role="publishers")
        with self.assertRaises(Exception):
            _settings(telegram_bot_runtime_role="sidecar")

    def test_split_flag_combinations(self):
        queue_flags = dict(
            telegram_multi_publisher_enabled=True,
            telegram_b2b_dispatch_enabled=True,
        )
        self.assertEqual(_settings().telegram_bot_runtime_role, "all")
        self.assertFalse(_settings().telegram_bot_split_enabled)
        _settings(telegram_bot_runtime_role="all", telegram_bot_split_enabled=False)
        _settings(
            telegram_bot_runtime_role="primary",
            telegram_bot_split_enabled=True,
            **queue_flags,
        )
        _settings(
            telegram_bot_runtime_role="executor",
            telegram_bot_split_enabled=True,
            **queue_flags,
        )
        with self.assertRaises(Exception):
            _settings(telegram_bot_runtime_role="all", telegram_bot_split_enabled=True)
        with self.assertRaises(Exception):
            _settings(
                telegram_bot_runtime_role="primary",
                telegram_bot_split_enabled=False,
            )
        with self.assertRaises(Exception):
            _settings(
                telegram_bot_runtime_role="executor",
                telegram_bot_split_enabled=False,
                **queue_flags,
            )

    def test_legacy_rejects_split_roles_before_workers(self):
        with self.assertRaises(TelegramBotRuntimeRoleError):
            assert_telegram_bot_runtime_role_compatible(
                settings_obj=_queue_settings(
                    telegram_bot_runtime_role="primary",
                    telegram_bot_split_enabled=True,
                ),
                runtime=_LEGACY,
                role="primary",
            )
        with self.assertRaises(TelegramBotRuntimeRoleError):
            assert_telegram_bot_runtime_role_compatible(
                settings_obj=_queue_settings(
                    telegram_bot_runtime_role="executor",
                    telegram_bot_split_enabled=True,
                ),
                runtime=_LEGACY,
                role="executor",
            )
        self.assertEqual(
            assert_telegram_bot_runtime_role_compatible(
                settings_obj=_queue_settings(),
                runtime=_LEGACY,
            ),
            "all",
        )

    def test_all_plus_queue_v1_keeps_queue_and_otp_workers(self):
        with patch(
            "run_bot.configured_telegram_delivery_queue_worker_factory",
            return_value=lambda: None,
        ):
            factories = telegram_execution_worker_factories(
                _QUEUE_V1,
                settings_obj=_queue_settings(),
                runtime_role="all",
            )
        self.assertEqual(len(factories), 2)
        self.assertEqual(factories[1].__name__, "run_telegram_otp_ephemeral_worker")

    def test_primary_without_executor_is_not_promotable(self):
        report = describe_telegram_bot_runtime_topology(
            role="primary",
            split_enabled=True,
            queue_owner_present=False,
        )
        self.assertTrue(report.can_start)
        self.assertFalse(report.topology_complete)
        self.assertFalse(report.healthy)
        self.assertFalse(report.promotable)
        self.assertEqual(report.polling_identities, ("primary",))
        self.assertEqual(report.queue_execution_identities, ())

    def test_executor_without_queue_v1_is_rejected(self):
        with self.assertRaises(TelegramBotRuntimeRoleError):
            assert_telegram_bot_runtime_role_compatible(
                settings_obj=_queue_settings(
                    telegram_bot_runtime_role="executor",
                    telegram_bot_split_enabled=True,
                    telegram_multi_publisher_enabled=False,
                    telegram_b2b_dispatch_enabled=False,
                ),
                runtime=_QUEUE_V1,
                role="executor",
            )


if __name__ == "__main__":
    unittest.main()
