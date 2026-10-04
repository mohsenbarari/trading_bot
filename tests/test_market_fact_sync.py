import json
from datetime import datetime, timezone
import unittest
from pathlib import Path
from unittest.mock import patch

from core.market_intelligence.market_fact_sync import (
    ESTIMATOR_PRIORITY_STREAMS,
    MAX_BATCH_ITEMS,
    MarketFactSyncError,
    batch_items_hash,
    load_next_batch,
    outbox_metrics,
)


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "market_private_pipeline"
    / "market_fact_batch.json"
)


class _Cursor:
    def __init__(self, envelope):
        self.envelope = envelope
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, parameters=None):
        self.executions.append((statement, parameters))

    def fetchone(self):
        # The database has selected the stream waiting longest for an ACK.
        # The test asserts the SQL scheduling rule below, rather than relying
        # on a particular PostgreSQL plan.
        return (self.envelope["stream_id"], 0, 1)

    def fetchall(self):
        return [(1, self.envelope)]


class _Connection:
    def __init__(self, envelope):
        self.cursor_instance = _Cursor(envelope)
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def rollback(self):
        self.rollbacks += 1


class MarketFactSyncSchedulingTests(unittest.TestCase):
    def batch_connection(self, count, first=1):
        envelope = json.loads(FIXTURE.read_text())["items"][0]["fact"]
        connection = _Connection(envelope)
        connection.cursor_instance.fetchone = lambda: (envelope["stream_id"], first - 1, first)
        connection.cursor_instance.fetchall = lambda: [(i, envelope) for i in range(first, first + count)]
        return connection

    def test_full_contract_batch_hashes_once_without_waiting_for_more_data(self):
        connection = self.batch_connection(MAX_BATCH_ITEMS, first=9998)
        with patch("core.market_intelligence.market_fact_sync.batch_items_hash", wraps=batch_items_hash) as hashing:
            batch = load_next_batch(connection, sender_instance_id="test-sync")
        self.assertEqual((batch.first_sequence, batch.last_sequence, batch.item_count), (9998, 10497, 500))
        self.assertEqual(hashing.call_count, 1)
        self.assertEqual(connection.cursor_instance.executions[1][1][-1], 500)
        self.assertEqual(load_next_batch(self.batch_connection(1), sender_instance_id="test-sync").item_count, 1)

    def test_linear_size_accounting_preserves_exact_document_limit(self):
        with patch("core.market_intelligence.market_fact_sync._utc_now", return_value=datetime(2026, 1, 1, tzinfo=timezone.utc)):
            single = load_next_batch(self.batch_connection(1), sender_instance_id="test-sync")
            limit = len(single.model_dump_json().encode("utf-8"))
            bounded = load_next_batch(self.batch_connection(10), sender_instance_id="test-sync", max_document_bytes=limit)
            self.assertEqual(bounded.item_count, 1)
            self.assertEqual(len(bounded.model_dump_json().encode("utf-8")), limit)
            with self.assertRaisesRegex(MarketFactSyncError, "single_item_too_large"):
                load_next_batch(self.batch_connection(1), sender_instance_id="test-sync", max_document_bytes=limit - 1)

    def test_sequence_gap_ends_batch_without_skipping_original_delivery(self):
        connection = self.batch_connection(3)
        original_rows = connection.cursor_instance.fetchall()
        connection.cursor_instance.fetchall = lambda: [original_rows[0], original_rows[2]]
        batch = load_next_batch(connection, sender_instance_id="test-sync")
        self.assertEqual((batch.first_sequence, batch.last_sequence), (1, 1))

    def test_operational_metrics_do_not_scan_acknowledged_history(self):
        from unittest.mock import MagicMock
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (10, 5.0, 2, 0, 4)
        report = outbox_metrics(connection)
        self.assertEqual(cursor.execute.call_count, 1)
        self.assertIn("WHERE acknowledged_at_utc IS NULL", cursor.execute.call_args[0][0])
        self.assertEqual(report["queue_depth"], 10)
        self.assertEqual(report["send_attempt_count"], 4)
        self.assertEqual(report["send_attempt_count_scope"], "UNACKNOWLEDGED")

    def test_estimator_streams_precede_fair_scheduling_of_other_streams(self):
        envelope = json.loads(FIXTURE.read_text(encoding="utf-8"))["items"][0]["fact"]
        connection = _Connection(envelope)

        batch = load_next_batch(
            connection,
            sender_instance_id="market-fact-sync-test",
        )

        self.assertIsNotNone(batch)
        self.assertEqual(batch.stream_id, envelope["stream_id"])
        head_selection = connection.cursor_instance.executions[0][0]
        self.assertIn("c.updated_at_utc AS last_acknowledged_at_utc", head_selection)
        self.assertEqual(
            ESTIMATOR_PRIORITY_STREAMS,
            ("market.fact.coin.group.1", "market.fact.coin.group.2"),
        )
        self.assertIn(
            "ORDER BY CASE WHEN stream_id IN ('market.fact.coin.group.1',",
            head_selection,
        )
        self.assertIn("last_acknowledged_at_utc ASC NULLS FIRST,", head_selection)
        self.assertEqual(connection.rollbacks, 1)
