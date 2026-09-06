from datetime import datetime, timezone
import unittest

from api.routers.users import convert_to_utc


class TestConvertToUtc(unittest.TestCase):
    def test_convert_to_utc_none(self):
        self.assertIsNone(convert_to_utc(None))

    def test_convert_to_utc_aware_utc(self):
        aware_dt = datetime(2026, 5, 20, 10, 15, 30, tzinfo=timezone.utc)
        result = convert_to_utc(aware_dt)
        self.assertIsInstance(result, datetime)
        self.assertEqual(result, datetime(2026, 5, 20, 10, 15, 30))
        self.assertIsNone(result.tzinfo)

    def test_convert_to_utc_naive_tehran(self):
        naive_dt = datetime(2026, 1, 15, 12, 30, 0)
        result = convert_to_utc(naive_dt)
        self.assertIsInstance(result, datetime)
        self.assertEqual(result, datetime(2026, 1, 15, 9, 0, 0))
        self.assertIsNone(result.tzinfo)


if __name__ == "__main__":
    unittest.main()
