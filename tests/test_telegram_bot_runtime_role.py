from types import SimpleNamespace
import unittest

from core.telegram_bot_runtime_role import (
    TELEGRAM_BOT_RUNTIME_PRIMARY_IDENTITIES,
    TELEGRAM_BOT_RUNTIME_PUBLISHER_IDENTITIES,
    TelegramBotRuntimeRoleError,
    assert_telegram_bot_runtime_role_compatible,
    assert_telegram_bot_runtime_role_plans_are_disjoint,
    resolve_telegram_bot_runtime_role,
    role_owns_primary_surface,
    role_owns_publisher_surface,
    select_owned_bot_identities,
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

    def test_default_and_split_roles_are_explicit(self):
        self.assertEqual(
            resolve_telegram_bot_runtime_role(SimpleNamespace()),
            "all",
        )
        self.assertTrue(role_owns_primary_surface("all"))
        self.assertTrue(role_owns_publisher_surface("all"))
        self.assertTrue(role_owns_primary_surface("primary"))
        self.assertFalse(role_owns_publisher_surface("primary"))
        self.assertFalse(role_owns_primary_surface("publishers"))
        self.assertTrue(role_owns_publisher_surface("publishers"))

    def test_split_roles_own_disjoint_identities(self):
        assert_telegram_bot_runtime_role_plans_are_disjoint()
        self.assertFalse(
            TELEGRAM_BOT_RUNTIME_PRIMARY_IDENTITIES
            & TELEGRAM_BOT_RUNTIME_PUBLISHER_IDENTITIES
        )
        available = (
            "primary",
            "channel_editor",
            *TELEGRAM_PUBLISHER_IDENTITIES,
        )
        self.assertEqual(
            select_owned_bot_identities("primary", available),
            ("primary", "channel_editor"),
        )
        self.assertEqual(
            select_owned_bot_identities("publishers", available),
            TELEGRAM_PUBLISHER_IDENTITIES,
        )
        self.assertEqual(select_owned_bot_identities("all", available), available)
        self.assertNotIn(
            "primary",
            select_owned_bot_identities("publishers", available),
        )

    def test_publishers_role_requires_queue_and_b2b(self):
        settings_obj = SimpleNamespace(
            telegram_bot_runtime_role="publishers",
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
            "publishers",
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
                    telegram_bot_runtime_role="publishers",
                    telegram_multi_publisher_enabled=False,
                    telegram_b2b_dispatch_enabled=False,
                ),
                runtime=queue,
            )


if __name__ == "__main__":
    unittest.main()
