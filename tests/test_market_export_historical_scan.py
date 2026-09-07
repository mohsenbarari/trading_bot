"""Historical export stays equivalent without scanning every raw XAU tick."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.market_fact_projection import (
    XAU_MODEL_INPUT_BUCKET_SECONDS, XAU_MODEL_INPUT_EXPORT_SETTLE_SECONDS,
    _pending_export_rows, initialize_export_ledger,
)
from core.market_intelligence.market_store import (
    MarketObservation, connect_market_store, derive_event_key,
    initialize_market_store, upsert_observation,
)


class HistoricalExportScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.market = connect_market_store(Path(self.temp.name) / "market.sqlite")
        initialize_market_store(self.market)
        initialize_export_ledger(self.market)
        base = datetime(2026, 8, 26, tzinfo=timezone.utc)
        for index in range(300):
            source = "USD_HERAT" if index % 7 == 0 else "XAUUSD"
            timestamp = (base + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
            upsert_observation(self.market, MarketObservation(
                event_key=derive_event_key("scan-parity", str(index)),
                source_code=source, source_family="TELEGRAM_PUBLIC",
                event_time_utc=timestamp, available_at_utc=timestamp,
                instrument=source, market_label=source + "_SPOT",
                settlement_term="SPOT", trade_form="NOT_APPLICABLE",
                event_type="QUOTE", side="MID",
                price="3500.10" if source == "XAUUSD" else "95000",
                price_unit="USD_PER_TROY_OUNCE" if source == "XAUUSD" else "TOMAN_PER_USD",
                currency="USD" if source == "XAUUSD" else "TOMAN",
                parser_version="scan-parity-v1",
            ))
        self.market.commit()
        # Initialize the existing bucket projection, then vary ledger outcomes.
        _pending_export_rows(self.market, max_rows=1000)

    def tearDown(self):
        self.market.close()
        self.temp.cleanup()

    def reference(self, limit):
        return self.market.execute(f"""
            SELECT o.* FROM market_observations o
            LEFT JOIN market_fact_export_ledger l ON l.event_key=o.event_key
            LEFT JOIN market_fact_export_semantics s ON s.event_key=o.event_key
            WHERE (o.source_code<>'XAUUSD' OR EXISTS (
                SELECT 1 FROM market_xau_model_input_buckets b
                WHERE b.selected_event_key=o.event_key
                  AND CAST(strftime('%s','now') AS INTEGER) >=
                    (b.bucket_number+1)*{XAU_MODEL_INPUT_BUCKET_SECONDS}
                    +{XAU_MODEL_INPUT_EXPORT_SETTLE_SECONDS}))
            AND (l.event_key IS NULL
                OR l.observation_inserted_at_utc<>o.inserted_at_utc
                OR (l.status='SUCCESS' AND (s.event_key IS NULL
                    OR s.observation_inserted_at_utc<>o.inserted_at_utc))
                OR (l.status='REJECTED' AND
                    instr(COALESCE(l.reason_code,''),'fact_payload_hash_mismatch')>0))
            ORDER BY o.id LIMIT ?
        """, (limit,)).fetchall()

    def test_same_rows_and_order_for_mixed_history_and_ledger_states(self):
        rows = self.market.execute("SELECT * FROM market_observations ORDER BY id").fetchall()
        for row in rows:
            variant = row["id"] % 7
            if not variant:
                continue  # Not exported yet.
            status = "SUCCESS" if variant in (1, 2, 3) else "REJECTED"
            timestamp = row["inserted_at_utc"] if variant != 4 else "2020-01-01T00:00:00Z"
            reason = "fact_payload_hash_mismatch" if variant == 5 else "permanent_rejection"
            self.market.execute(
                "INSERT INTO market_fact_export_ledger VALUES(?,?,?,?,?,?,?,?)",
                (row["event_key"], timestamp, status, "a" * 64, 1, reason, 1, timestamp),
            )
            if variant in (1, 3):
                semantic_time = row["inserted_at_utc"] if variant == 1 else "2020-01-01T00:00:00Z"
                self.market.execute(
                    "INSERT INTO market_fact_export_semantics VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (row["event_key"], semantic_time, "a" * 64, 1, row["id"], row["id"],
                     "b" * 64, "ELIGIBLE", "c" * 64, "d" * 64, timestamp),
                )
        self.market.commit()
        for limit in (1, 2, 7, 20, 63, 1000):
            with self.subTest(limit=limit):
                expected = [tuple(row) for row in self.reference(limit)]
                actual = [tuple(row) for row in _pending_export_rows(self.market, max_rows=limit)]
                self.assertEqual(actual, expected)

    def test_historical_queries_use_non_xau_index_and_bucket_key_lookup(self):
        queries = []
        self.market.set_trace_callback(queries.append)
        _pending_export_rows(self.market, max_rows=1000)
        self.market.set_trace_callback(None)
        historical = [sql for sql in queries if "SELECT o.*" in sql and "ORDER BY o.id" in sql]
        self.assertEqual(len(historical), 2)
        plans = [[row[3] for row in self.market.execute("EXPLAIN QUERY PLAN " + sql)]
                 for sql in historical]
        self.assertTrue(any("idx_market_observations_export_non_xau_id" in item for item in plans[0]))
        self.assertFalse(any(item == "SCAN o" or item.startswith("SCAN o ") for item in plans[1]))
        self.assertTrue(any("SEARCH o USING INDEX" in item for item in plans[1]))
        self.assertEqual(self.market.execute("SELECT count(*) FROM market_observations").fetchone()[0], 300)


if __name__ == "__main__":
    unittest.main()
