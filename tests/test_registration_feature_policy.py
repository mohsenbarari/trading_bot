import unittest
from types import SimpleNamespace

from core.registration_feature_policy import (
    direct_registration_runtime_ready,
    registration_reconciliation_runtime_ready,
)


class RegistrationFeaturePolicyTests(unittest.TestCase):
    def test_direct_registration_requires_all_three_local_capabilities(self):
        for direct, reconciliation, sync_v2, expected in (
            (True, True, True, True),
            (False, True, True, False),
            (True, False, True, False),
            (True, True, False, False),
        ):
            with self.subTest(
                direct=direct,
                reconciliation=reconciliation,
                sync_v2=sync_v2,
            ):
                settings = SimpleNamespace(
                    telegram_direct_registration_enabled=direct,
                    telegram_registration_reconciliation_enabled=reconciliation,
                    registration_sync_v2_enabled=sync_v2,
                )
                self.assertEqual(direct_registration_runtime_ready(settings), expected)

    def test_reconciliation_can_drain_when_direct_collection_is_disabled(self):
        settings = SimpleNamespace(
            telegram_direct_registration_enabled=False,
            telegram_registration_reconciliation_enabled=True,
            registration_sync_v2_enabled=True,
        )
        self.assertTrue(registration_reconciliation_runtime_ready(settings))
        self.assertFalse(direct_registration_runtime_ready(settings))
