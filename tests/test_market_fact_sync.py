import json
import unittest
from pathlib import Path

from core.market_intelligence.market_fact_sync import load_next_batch


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
    def test_independent_streams_are_scheduled_by_least_recent_acknowledgement(self):
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
        self.assertIn(
            "ORDER BY last_acknowledged_at_utc ASC NULLS FIRST,",
            head_selection,
        )
        self.assertEqual(connection.rollbacks, 1)
