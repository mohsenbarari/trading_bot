import unittest
from datetime import datetime, timezone

from api.routers.users import IRAN_TZ, convert_to_utc


class UsersRouterConvertToUtcTests(unittest.TestCase):
    def test_convert_to_utc_returns_none_for_none(self):
        self.assertIsNone(convert_to_utc(None))

    def test_convert_to_utc_converts_naive_iran_and_aware_datetimes_to_naive_utc(self):
        naive = datetime(2026, 1, 1, 12, 0, 0)
        expected = IRAN_TZ.localize(naive).astimezone(timezone.utc).replace(tzinfo=None)
        self.assertEqual(convert_to_utc(naive), expected)

        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(convert_to_utc(aware), datetime(2026, 1, 1, 12, 0, 0))


if __name__ == "__main__":
    unittest.main()