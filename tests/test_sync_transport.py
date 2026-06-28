import unittest

from core.sync_transport import (
    sync_tls_verify_setting_from_values,
    sync_transport_security_status_from_values,
)


class SyncTransportSecurityTests(unittest.TestCase):
    def test_sync_tls_verify_defaults_to_enabled(self):
        self.assertIs(sync_tls_verify_setting_from_values(), True)
        self.assertIs(sync_tls_verify_setting_from_values(sync_verify_tls="false"), False)
        self.assertEqual(
            sync_tls_verify_setting_from_values(sync_verify_tls=False, sync_ca_bundle="/etc/ssl/internal-ca.pem"),
            "/etc/ssl/internal-ca.pem",
        )

    def test_production_rejects_disabled_tls_without_ca_bundle(self):
        blocked = sync_transport_security_status_from_values(
            environment="production",
            sync_verify_tls="false",
            sync_ca_bundle="",
        )
        allowed_with_ca = sync_transport_security_status_from_values(
            environment="production",
            sync_verify_tls="false",
            sync_ca_bundle="/etc/ssl/internal-ca.pem",
        )
        allowed_staging = sync_transport_security_status_from_values(
            environment="staging",
            sync_verify_tls="false",
            sync_ca_bundle="",
        )

        self.assertFalse(blocked["production_allowed"])
        self.assertEqual(blocked["reason"], "sync_tls_verification_disabled")
        self.assertTrue(allowed_with_ca["production_allowed"])
        self.assertTrue(allowed_with_ca["ca_bundle_configured"])
        self.assertTrue(allowed_staging["production_allowed"])


if __name__ == "__main__":
    unittest.main()
