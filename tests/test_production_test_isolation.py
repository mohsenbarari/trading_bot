import unittest
from types import SimpleNamespace

from core.production_test_isolation import (
    ProductionTestIsolationConfig,
    isolation_block_payload,
    user_matches_isolation_allowlist,
)


class ProductionTestIsolationTests(unittest.TestCase):
    def test_disabled_config_does_not_allow_match(self):
        config = ProductionTestIsolationConfig(False, None, frozenset({1}), ("PFM_",), ("0999",))

        self.assertFalse(
            user_matches_isolation_allowlist(
                SimpleNamespace(id=1, account_name="PFM_user", mobile_number="09990000000"),
                config,
            )
        )

    def test_allowlist_matches_user_id_account_prefix_and_mobile_prefix(self):
        config = ProductionTestIsolationConfig(
            True,
            "matrix",
            frozenset({10}),
            ("PFM_",),
            ("099901",),
        )

        self.assertTrue(
            user_matches_isolation_allowlist(
                SimpleNamespace(id=10, account_name="real_user", mobile_number="09120000000"),
                config,
            )
        )
        self.assertTrue(
            user_matches_isolation_allowlist(
                SimpleNamespace(id=11, account_name="PFM_case_1", mobile_number="09120000000"),
                config,
            )
        )
        self.assertTrue(
            user_matches_isolation_allowlist(
                SimpleNamespace(id=12, account_name="other", mobile_number="09990123456"),
                config,
            )
        )
        self.assertFalse(
            user_matches_isolation_allowlist(
                SimpleNamespace(id=13, account_name="other", mobile_number="09120000000"),
                config,
            )
        )

    def test_block_payload_is_temporary_and_cache_safe_compatible(self):
        self.assertEqual(
            isolation_block_payload("holiday_matrix"),
            {
                "detail": "WEBAPP_TEMPORARILY_UNAVAILABLE",
                "temporary": True,
                "reason": "holiday_matrix",
            },
        )


if __name__ == "__main__":
    unittest.main()
