from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from telegram_price_collector.db import (
    connect,
    initialize,
    rebuild_minute_prices,
    replace_price_events,
    upsert_raw_post,
)
from telegram_price_collector.models import PriceEvent, RawPost


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "prices.sqlite3"
        self.connection = connect(self.db_path)
        initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp_dir.cleanup()

    def _post(
        self, message_id: int, timestamp: str, text: str, *, source_code: str = "TEST"
    ) -> int:
        return upsert_raw_post(
            self.connection,
            source_code=source_code,
            post=RawPost(
                message_id=message_id,
                published_at_utc=timestamp,
                raw_text=text,
            ),
        )

    def test_upsert_does_not_duplicate_posts(self) -> None:
        first_id = self._post(10, "2026-07-20T10:00:01Z", "first")
        second_id = self._post(10, "2026-07-20T10:00:01Z", "edited")

        self.assertEqual(first_id, second_id)
        count = self.connection.execute("SELECT COUNT(*) FROM raw_posts").fetchone()[0]
        text = self.connection.execute("SELECT raw_text FROM raw_posts").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(text, "edited")

    def test_xau_keeps_latest_raw_sample_per_15_second_bucket(self) -> None:
        first = self._post(
            1, "2026-07-20T10:00:01Z", "4500.10", source_code="XAUUSD"
        )
        second = self._post(
            2, "2026-07-20T10:00:14Z", "4501.25", source_code="XAUUSD"
        )

        self.assertEqual(first, second)
        row = self.connection.execute(
            "SELECT message_id, published_at_utc, raw_text FROM raw_posts"
        ).fetchone()
        self.assertEqual(row["message_id"], 2)
        self.assertEqual(row["published_at_utc"], "2026-07-20T10:00:14Z")
        self.assertEqual(row["raw_text"], "4501.25")

        third = self._post(
            3, "2026-07-20T10:00:15Z", "4502.00", source_code="XAUUSD"
        )
        self.assertNotEqual(third, second)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM raw_posts WHERE source_code='XAUUSD'"
            ).fetchone()[0],
            2,
        )

    def test_minute_aggregation_uses_open_high_low_close(self) -> None:
        event = PriceEvent(
            instrument="XAUUSD",
            market_label="اونس جهانی",
            price=Decimal("4500.10"),
            currency="USD",
            price_unit="USD_PER_TROY_OUNCE",
            settlement_term="SPOT",
            trade_form="NOT_APPLICABLE",
        )
        first_post_id = self._post(1, "2026-07-20T10:00:01Z", "4500.10")
        replace_price_events(
            self.connection,
            raw_post_id=first_post_id,
            event_time_utc="2026-07-20T10:00:01Z",
            events=[event],
        )
        second_post_id = self._post(2, "2026-07-20T10:00:59Z", "4501.25")
        replace_price_events(
            self.connection,
            raw_post_id=second_post_id,
            event_time_utc="2026-07-20T10:00:59Z",
            events=[
                PriceEvent(
                    instrument=event.instrument,
                    market_label=event.market_label,
                    price=Decimal("4501.25"),
                    currency=event.currency,
                    price_unit=event.price_unit,
                    settlement_term=event.settlement_term,
                    trade_form=event.trade_form,
                )
            ],
        )
        self.connection.commit()

        row_count = rebuild_minute_prices(self.connection)
        row = self.connection.execute("SELECT * FROM minute_prices").fetchone()

        self.assertEqual(row_count, 1)
        self.assertEqual(row["open"], 4500.10)
        self.assertEqual(row["low"], 4500.10)
        self.assertEqual(row["high"], 4501.25)
        self.assertEqual(row["close"], 4501.25)
        self.assertEqual(row["sample_count"], 2)

        event_row = self.connection.execute(
            "SELECT tehran_date, tehran_minute, tehran_weekday_name, source_text FROM price_events_review LIMIT 1"
        ).fetchone()
        self.assertEqual(event_row["tehran_date"], "2026-07-20")
        self.assertEqual(event_row["tehran_minute"], "13:30")
        self.assertEqual(event_row["tehran_weekday_name"], "دوشنبه")
        self.assertEqual(event_row["source_text"], "4500.10")

    def test_herat_abbreviation_uses_only_prior_same_book_temporal_range(self) -> None:
        def store(message_id: int, second: int, price: int) -> None:
            timestamp = f"2026-07-20T10:00:{second:02d}Z"
            raw_post_id = self._post(
                message_id, timestamp, str(price), source_code="USD_HERAT"
            )
            replace_price_events(
                self.connection,
                raw_post_id=raw_post_id,
                event_time_utc=timestamp,
                events=[
                    PriceEvent(
                        instrument="USD_HERAT",
                        market_label="دلار هرات فردایی کاغذی",
                        price=Decimal(price),
                        currency="TOMAN",
                        price_unit="TOMAN_PER_USD",
                        settlement_term="TOMORROW",
                        trade_form="PAPER",
                        parser_version="rules-test",
                    )
                ],
            )

        store(1, 1, 185_200)
        store(2, 2, 185_350)
        store(3, 3, 185_500)
        store(4, 4, 85_600)
        row = self.connection.execute(
            "SELECT price_value, parser_version FROM price_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["price_value"], "185600")
        self.assertIn("herat-temporal-range-v1", row["parser_version"])


if __name__ == "__main__":
    unittest.main()
