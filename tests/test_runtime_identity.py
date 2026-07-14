import unittest
from types import SimpleNamespace

from core.runtime_identity import (
    RuntimeIdentityError,
    resolve_runtime_identity,
)


class RuntimeIdentityTests(unittest.TestCase):
    def test_legacy_foreign_maps_to_bot_fi(self):
        identity = resolve_runtime_identity(SimpleNamespace(server_mode="foreign"))

        self.assertEqual(identity.logical_authority, "foreign")
        self.assertEqual(identity.physical_site, "bot_fi")
        self.assertTrue(identity.compatibility_inferred)

    def test_legacy_iran_maps_to_webapp_fi(self):
        identity = resolve_runtime_identity(SimpleNamespace(server_mode="iran"))

        self.assertEqual(identity.logical_authority, "webapp")
        self.assertEqual(identity.physical_site, "webapp_fi")
        self.assertTrue(identity.compatibility_inferred)

    def test_explicit_iran_site_is_valid_webapp_authority(self):
        identity = resolve_runtime_identity(
            SimpleNamespace(
                server_mode="iran",
                logical_authority="webapp",
                physical_site="webapp_ir",
            )
        )

        self.assertEqual(identity.physical_site, "webapp_ir")
        self.assertTrue(identity.is_webapp_authority)
        self.assertTrue(identity.is_webapp_site)
        self.assertFalse(identity.compatibility_inferred)

    def test_impossible_authority_site_combinations_fail_closed(self):
        invalid_settings = (
            SimpleNamespace(
                server_mode="iran",
                logical_authority="foreign",
                physical_site="webapp_ir",
            ),
            SimpleNamespace(
                server_mode="foreign",
                logical_authority="foreign",
                physical_site="webapp_fi",
            ),
            SimpleNamespace(
                server_mode="iran",
                logical_authority="webapp",
                physical_site="bot_fi",
            ),
        )

        for settings_obj in invalid_settings:
            with self.subTest(settings=settings_obj):
                with self.assertRaises(RuntimeIdentityError):
                    resolve_runtime_identity(settings_obj)

    def test_unknown_legacy_server_mode_fails_closed(self):
        with self.assertRaises(RuntimeIdentityError):
            resolve_runtime_identity(SimpleNamespace(server_mode="webapp-ish"))


if __name__ == "__main__":
    unittest.main()
