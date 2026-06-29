import unittest
from datetime import datetime, timedelta, timezone

from scripts import check_production_data_hygiene as hygiene


class ProductionDataHygieneTests(unittest.TestCase):
    def test_exact_dev_mobile_user_is_critical_and_redacted(self):
        record = {
            "id": 7,
            "account_name": "dev",
            "mobile_number": "09999999999",
            "username": None,
            "full_name": "dev",
            "role": "SUPER_ADMIN",
            "is_deleted": False,
        }

        reasons = hygiene.reasons_for_record("users", record)
        finding = hygiene.build_finding("users", record, reasons)

        self.assertIn("mobile_number:exact_dev_mobile", reasons)
        self.assertEqual(finding["severity"], "critical")
        self.assertEqual(finding["record"]["mobile"], "099*****9999")
        self.assertNotIn("09999999999", str(finding))

    def test_soft_deleted_suspicious_user_is_warning(self):
        record = {
            "id": 8,
            "account_name": "pwSmokeUser",
            "mobile_number": "09999999999_del_8",
            "username": None,
            "full_name": "pwSmokeUser",
            "role": "SUPER_ADMIN",
            "is_deleted": True,
        }

        finding = hygiene.build_finding("users", record, hygiene.reasons_for_record("users", record))

        self.assertEqual(finding["severity"], "warning")
        self.assertEqual(finding["record"]["mobile"], "099*****9999_del")

    def test_persian_real_names_do_not_match_synthetic_rules(self):
        record = {
            "id": 9,
            "account_name": "ali_rezaei",
            "mobile_number": "09370809280",
            "username": None,
            "full_name": "علی رضایی",
            "role": "STANDARD",
            "is_deleted": False,
        }

        self.assertEqual(hygiene.reasons_for_record("users", record), [])

    def test_pending_synthetic_invitation_is_high(self):
        record = {
            "id": 3,
            "account_name": "TG9_STAGE_probe",
            "mobile_number": "09123456789",
            "role": "STANDARD",
            "created_by_id": 1,
            "is_used": False,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }

        finding = hygiene.build_finding("invitations", record, hygiene.reasons_for_record("invitations", record))

        self.assertEqual(finding["severity"], "high")
        self.assertIn("account_name:synthetic_probe_prefix", finding["reasons"])

    def test_status_and_exit_policy(self):
        findings = [
            {"severity": "warning"},
            {"severity": "high"},
        ]

        self.assertEqual(hygiene.overall_status(findings), "high")
        self.assertTrue(hygiene.should_fail("high", "high"))
        self.assertFalse(hygiene.should_fail("warning", "high"))
        self.assertFalse(hygiene.should_fail("critical", "never"))


if __name__ == "__main__":
    unittest.main()
