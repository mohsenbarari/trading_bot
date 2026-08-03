from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import unittest

from telegram_price_collector.collector import _emit_progress


class CollectorProgressTests(unittest.TestCase):
    def test_progress_contains_channel_and_tehran_date(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            _emit_progress(
                "channel_progress",
                channel_username="abshdh",
                channel_title="JUST IN TIME",
                source_time=datetime(2026, 7, 20, 9, 10, 43, tzinfo=timezone.utc),
                messages=1_000,
                price_events=1_850,
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["channel_username"], "@abshdh")
        self.assertEqual(payload["channel_title"], "JUST IN TIME")
        self.assertEqual(payload["source_date_tehran"], "2026-07-20")
        self.assertEqual(payload["source_datetime_tehran"], "2026-07-20T12:40:43+03:30")
        self.assertEqual(payload["messages"], 1_000)


if __name__ == "__main__":
    unittest.main()
