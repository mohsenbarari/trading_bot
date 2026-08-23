from types import SimpleNamespace
import unittest

from core.telegram_bot_runtime_role import (
    TELEGRAM_BOT_RUNTIME_EXECUTOR_POLLING_IDENTITIES,
    TELEGRAM_BOT_RUNTIME_PRIMARY_POLLING_IDENTITIES,
    TelegramBotRuntimeRoleError,
    assert_telegram_bot_runtime_role_compatible,
    assert_telegram_bot_runtime_role_plans_are_disjoint,
    resolve_telegram_bot_runtime_role,
    role_owns_local_ack_surface,
    role_owns_otp_worker,
    role_owns_primary_surface,
    role_owns_publisher_surface,
    role_owns_queue_executor,
    select_owned_bot_identities,
    select_polling_bot_identities,
    select_queue_execution_bot_identities,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)
from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES


class TelegramBotRuntimeRoleTests(unittest.TestCase):
    def test_unknown_role_fails_closed(self):
        with self.assertRaises(TelegramBotRuntimeRoleError):
            resolve_telegram_bot_runtime_role(role="worker")
        with self.assertRaises(TelegramBotRuntimeRoleError):
            resolve_telegram_bot_runtime_role(SimpleNamespace(telegram_bot_runtime_role=""))
        with self.assertRaises(TelegramBotRuntimeRoleError):
            resolve_telegram_bot_runtime_role(role="publishers")

    def test_default_and_split_roles_are_explicit(self):
        self.assertEqual(
            resolve_telegram_bot_runtime_role(SimpleNamespace()),
            "all",
        )
        self.assertTrue(role_owns_primary_surface("all"))
        self.assertTrue(role_owns_publisher_surface("all"))
        self.assertTrue(role_owns_queue_executor("all"))
        self.assertTrue(role_owns_otp_worker("all"))
        self.assertTrue(role_owns_local_ack_surface("all"))
        self.assertTrue(role_owns_primary_surface("primary"))
        self.assertFalse(role_owns_publisher_surface("primary"))
        self.assertFalse(role_owns_queue_executor("primary"))
        self.assertFalse(role_owns_otp_worker("primary"))
        self.assertTrue(role_owns_local_ack_surface("primary"))
        self.assertFalse(role_owns_primary_surface("executor"))
        self.assertTrue(role_owns_publisher_surface("executor"))
        self.assertTrue(role_owns_queue_executor("executor"))
        self.assertTrue(role_owns_otp_worker("executor"))
        self.assertFalse(role_owns_local_ack_surface("executor"))

    def test_split_roles_keep_polling_disjoint_and_one_queue_owner(self):
        assert_telegram_bot_runtime_role_plans_are_disjoint()
        self.assertFalse(
            TELEGRAM_BOT_RUNTIME_PRIMARY_POLLING_IDENTITIES
            & TELEGRAM_BOT_RUNTIME_EXECUTOR_POLLING_IDENTITIES
        )
        available = (
            "primary",
            "channel_editor",
            *TELEGRAM_PUBLISHER_IDENTITIES,
        )
        self.assertEqual(select_polling_bot_identities("primary", available), ("primary",))
        self.assertEqual(
            select_polling_bot_identities("executor", available),
            TELEGRAM_PUBLISHER_IDENTITIES,
        )
        self.assertEqual(select_queue_execution_bot_identities("primary", available), ())
        self.assertEqual(select_owned_bot_identities("primary", available), ())
        self.assertEqual(
            select_queue_execution_bot_identities("executor", available),
            available,
        )
        self.assertEqual(select_owned_bot_identities("all", available), available)

    def test_executor_role_requires_queue_and_b2b(self):
        settings_obj = SimpleNamespace(
            telegram_bot_runtime_role="executor",
            telegram_bot_split_enabled=True,
            telegram_multi_publisher_enabled=True,
            telegram_b2b_dispatch_enabled=True,
        )
        queue = TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )
        self.assertEqual(
            assert_telegram_bot_runtime_role_compatible(
                settings_obj=settings_obj,
                runtime=queue,
            ),
            "executor",
        )
        with self.assertRaises(TelegramBotRuntimeRoleError):
            assert_telegram_bot_runtime_role_compatible(
                settings_obj=settings_obj,
                runtime=TelegramDeliveryRuntimeDecision(
                    mode=TelegramDeliveryRuntimeMode.LEGACY,
                    legacy_workers_enabled=True,
                    queue_worker_enabled=False,
                ),
            )
        with self.assertRaises(TelegramBotRuntimeRoleError):
            assert_telegram_bot_runtime_role_compatible(
                settings_obj=SimpleNamespace(
                    telegram_bot_runtime_role="executor",
                    telegram_bot_split_enabled=True,
                    telegram_multi_publisher_enabled=False,
                    telegram_b2b_dispatch_enabled=False,
                ),
                runtime=queue,
            )


if __name__ == "__main__":
    unittest.main()
