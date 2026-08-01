import unittest

from pydantic import ValidationError

from core.config import Settings


def _settings(**overrides):
    values = {
        "database_url": "postgresql+asyncpg://test:test@127.0.0.1/test",
        "sync_database_url": "postgresql+psycopg2://test:test@127.0.0.1/test",
        "postgres_db": "test",
        "postgres_user": "test",
        "postgres_password": "test",
        "frontend_url": "http://localhost:3000",
        "redis_url": "redis://127.0.0.1:6379/15",
        "jwt_secret_key": "test-only-not-production",
        "emergency_ir_standalone": True,
        "emergency_auth_profile": "telegram-only",
        "emergency_sms_otp_enabled": False,
        "background_jobs_enabled": False,
        "trading_bot_disable_direct_sync_push": True,
        "invitation_sms_standard_enabled": False,
        "invitation_sms_customer_tier1_enabled": False,
        "invitation_sms_accountant_enabled": False,
        "invitation_sms_customer_tier2_enabled": False,
        "telegram_direct_registration_enabled": False,
        "telegram_registration_reconciliation_enabled": False,
        "registration_sync_v2_enabled": False,
        "registration_sync_accept_unversioned": False,
        "invitation_contract_v2_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


class EmergencyIrSmsProfileConfigTests(unittest.TestCase):
    def test_default_profile_remains_telegram_only_without_sms(self):
        settings = _settings()
        self.assertEqual(settings.emergency_auth_profile, "telegram-only")
        self.assertFalse(settings.emergency_sms_otp_enabled)

    def test_sms_profile_requires_all_fixed_relay_controls(self):
        settings = _settings(
            emergency_auth_profile="sms-otp",
            emergency_sms_otp_enabled=True,
            telegram_login_otp_enabled=True,
            otp_sms_auto_fallback_enabled=False,
            smsir_base_url="http://sms-egress:8080",
            smsir_trust_env=False,
            smsir_api_key="test-only-smsir-key",
            smsir_otp_template_id="123456",
            otp_delivery_state_secret="test-only-otp-delivery-state-secret-0123456789",
        )
        self.assertTrue(settings.emergency_sms_otp_enabled)

        invalid = (
            {"emergency_sms_otp_enabled": False},
            {"telegram_login_otp_enabled": False},
            {"otp_sms_auto_fallback_enabled": True},
            {"smsir_base_url": "https://api.sms.ir"},
            {"smsir_trust_env": True},
            {"smsir_api_key": None},
            {"otp_delivery_state_secret": "too-short"},
            {"sync_api_key": "forbidden"},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                sms_values = {
                    "emergency_auth_profile": "sms-otp",
                    "emergency_sms_otp_enabled": True,
                    "telegram_login_otp_enabled": True,
                    "otp_sms_auto_fallback_enabled": False,
                    "smsir_base_url": "http://sms-egress:8080",
                    "smsir_trust_env": False,
                    "smsir_api_key": "test-only-smsir-key",
                    "smsir_otp_template_id": "123456",
                    "otp_delivery_state_secret": "test-only-otp-delivery-state-secret-0123456789",
                }
                sms_values.update(changes)
                _settings(**sms_values)

    def test_telegram_only_profile_rejects_sms_and_worker_drift(self):
        for changes in (
            {"smsir_api_key": "forbidden"},
            {"emergency_sms_otp_enabled": True},
            {"telegram_login_otp_enabled": True},
            {"background_jobs_enabled": True},
            {"trading_bot_disable_direct_sync_push": False},
            {"invitation_sms_accountant_enabled": True},
            {"registration_sync_v2_enabled": True},
            {"three_site_dr_enabled": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                _settings(**changes)


if __name__ == "__main__":
    unittest.main()
