"""A rejected public quote must not poison the entire durable parse queue."""
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.capture_event_adapter import (
    decode_market_channel_event, initialize_capture_adapter, project_capture_changes,
    stage_capture_event,
)
from core.market_intelligence.coin_group_staging import connect_coin_group_staging
from core.market_intelligence.market_store import connect_market_store, initialize_market_store
from core.market_intelligence.market_contracts import MarketStoreContractError
from core.market_intelligence.private_capture_telegram import SOURCE_POLICIES, build_market_event
from tests.test_market_pipeline_stage4_capture import snapshot
from core.market_intelligence import private_pipeline_foundation as foundation


class PublicPriceFailureIsolationTests(unittest.TestCase):
    def test_unexpected_failure_logs_code_location_without_exception_payload(self):
        def fail(_role):
            raise ValueError("sensitive-fixture-payload-must-not-appear")
        output = io.StringIO()
        with patch.object(foundation, "run_service", side_effect=fail), redirect_stderr(output):
            self.assertEqual(foundation.main(["service", "--role", "market-processor"]), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["reason_code"], "runtime_dependency_failure")
        self.assertEqual(report["error_type"], "ValueError")
        self.assertEqual(report["origin"]["function"], "fail")
        self.assertNotIn("sensitive-fixture-payload", output.getvalue())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.staging = connect_coin_group_staging(self.root / "capture.sqlite")
        self.market = connect_market_store(self.root / "market.sqlite")
        initialize_capture_adapter(self.staging)
        initialize_market_store(self.market)
        self.now = datetime(2026, 9, 7, 6, tzinfo=timezone.utc)

    def tearDown(self):
        self.market.close()
        self.staging.close()
        self.temp.cleanup()

    def stage(self, identity, text, source="USD_HERAT"):
        event = decode_market_channel_event(build_market_event(
            SOURCE_POLICIES[source], snapshot(identity,
                published=self.now - timedelta(minutes=1), text=text),
            event_type="message_created", received_at=self.now, backfill=False,
        ))
        stage_capture_event(self.staging, event)
        return event

    def test_xau_out_of_range_message_cannot_poison_other_quotes(self):
        bad=self.stage(1,"XAUUSD 9999.50",source="XAUUSD")
        good=self.stage(2,"XAUUSD 3500.50",source="XAUUSD")
        report=project_capture_changes(self.staging,self.market,as_of_utc=self.now)
        self.assertEqual(report.public_price_policy_rejections,1)
        self.assertEqual(report.market_facts_upserted,1)
        self.assertEqual(self.market.execute('SELECT price_value FROM market_observations').fetchone()[0],'3500.50')
        self.assertEqual(tuple(self.staging.execute('SELECT status,disposition_code FROM capture_event_lineage WHERE event_id=?',(bad.event_id,)).fetchone()),('FILTERED','PRICE_OUT_OF_CANONICAL_RANGE'))
        self.assertEqual(self.staging.execute('SELECT status FROM capture_event_lineage WHERE event_id=?',(good.event_id,)).fetchone()[0],'PARSED')
        self.assertEqual(self.staging.execute('SELECT count(*) FROM capture_dirty_market_messages').fetchone()[0],0)
        self.assertEqual(self.staging.execute('SELECT count(*) FROM capture_market_messages').fetchone()[0],2)

    def test_xau_unrelated_contract_failure_remains_visible(self):
        self.stage(1,"XAUUSD 3500.50",source="XAUUSD")
        with patch('core.market_intelligence.capture_event_adapter.ingest_xau_messages_batch',
                   side_effect=MarketStoreContractError('instrument_price_unit_mismatch')):
            with self.assertRaisesRegex(MarketStoreContractError,'instrument_price_unit_mismatch'):
                project_capture_changes(self.staging,self.market,as_of_utc=self.now)

    def test_invalid_and_partially_valid_messages_do_not_block_next_valid_message(self):
        bad = self.stage(1, "هرات فروش 22400")
        mixed = self.stage(2, "هرات فروش 95000\nهرات فروش 22400")
        good = self.stage(3, "هرات فروش 96000")
        report = project_capture_changes(self.staging, self.market, as_of_utc=self.now)
        self.assertEqual(report.public_price_policy_rejections, 2)
        self.assertEqual(report.market_facts_upserted, 1)
        self.assertEqual([row[0] for row in self.market.execute(
            "SELECT price_value FROM market_observations")], ["96000"])
        self.assertEqual(self.staging.execute("SELECT count(*) FROM capture_dirty_market_messages").fetchone()[0], 0)
        for event in (bad, mixed):
            row = self.staging.execute("SELECT status,disposition_code FROM capture_event_lineage WHERE event_id=?",
                                       (event.event_id,)).fetchone()
            self.assertEqual(tuple(row), ("FILTERED", "PRICE_OUT_OF_CANONICAL_RANGE"))
            self.assertIsNotNone(self.staging.execute("SELECT 1 FROM capture_market_messages WHERE message_id=?",
                                                      (event.message_id,)).fetchone())
        self.assertEqual(self.staging.execute("SELECT status FROM capture_event_lineage WHERE event_id=?",
                                              (good.event_id,)).fetchone()[0], "PARSED")

    def test_unrelated_store_contract_errors_still_fail_closed(self):
        self.stage(1, "هرات فروش 95000")
        with patch("core.market_intelligence.capture_event_adapter._project_public_row_inner",
                   side_effect=MarketStoreContractError("instrument_price_unit_mismatch")):
            with self.assertRaisesRegex(MarketStoreContractError, "instrument_price_unit_mismatch"):
                project_capture_changes(self.staging, self.market, as_of_utc=self.now)
        self.assertEqual(self.staging.execute("SELECT count(*) FROM capture_dirty_market_messages").fetchone()[0], 1)
        self.assertEqual(self.market.execute("SELECT count(*) FROM market_observations").fetchone()[0], 0)

    def test_message_savepoint_does_not_commit_the_outer_transaction(self):
        self.stage(1, "هرات فروش 95000")
        self.staging.commit()
        project_capture_changes(self.staging, self.market, as_of_utc=self.now)
        self.assertTrue(self.market.in_transaction)
        self.market.rollback()
        self.staging.rollback()
        self.assertEqual(self.market.execute("SELECT count(*) FROM market_observations").fetchone()[0], 0)
        self.assertEqual(self.staging.execute("SELECT count(*) FROM capture_dirty_market_messages").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
