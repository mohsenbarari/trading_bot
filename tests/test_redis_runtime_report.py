import unittest

from scripts import report_redis_runtime as report


class RedisRuntimeReportTests(unittest.TestCase):
    def test_normalize_config_payload_accepts_dict_and_list(self):
        self.assertEqual(
            report.normalize_config_payload({"appendonly": "yes"}),
            {"appendonly": "yes"},
        )
        self.assertEqual(
            report.normalize_config_payload(["appendfsync", "everysec", "maxmemory-policy", "noeviction"]),
            {"appendfsync": "everysec", "maxmemory-policy": "noeviction"},
        )

    def test_evaluate_settings_flags_mismatches(self):
        checks = report.evaluate_settings(
            {
                "appendonly": "no",
                "appendfsync": "everysec",
                "maxmemory-policy": "noeviction",
            }
        )

        self.assertFalse(checks[0]["ok"])
        self.assertTrue(checks[1]["ok"])
        self.assertTrue(checks[2]["ok"])


if __name__ == "__main__":
    unittest.main()
