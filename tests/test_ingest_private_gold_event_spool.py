"""Tests for the explicit, local-only private-gold spool command."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.market_store import connect_market_store, initialize_market_store
from scripts.ingest_private_gold_event_spool import main


def _outer(payload: dict, *, at: str = "2026-08-04T12:01:00Z") -> dict:
    return {"published_at_utc": at, "payload_text": json.dumps(payload, ensure_ascii=False)}


def _offer() -> dict:
    return {
        "schema_version": "1.0",
        "event_type": "message_created",
        "source": {"market": "gold", "source_key": "account1_channel"},
        "gold": {
            "message_type": "offer",
            "message_id": "101",
            "telegram_datetime": "2026-08-04T12:00:00Z",
            "text": "80,300,000 فروش 5 تا با حواله",
        },
    }


def _trade() -> dict:
    return {
        "schema_version": "1.0",
        "event_type": "offer_verified",
        "source": {"market": "gold", "source_key": "account1_channel"},
        "gold": {
            "message_id": "101",
            "verification": {"state": "DONE"},
            "trade": {
                "status": "FULL",
                "traded_quantity": 5,
                "trade_detected_at": "2026-08-04T12:00:40Z",
                "telegram_edit_datetime": "2026-08-04T12:00:45Z",
            },
        },
    }


class IngestPrivateGoldEventSpoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "market").mkdir()
        (self.root / "private").mkdir()
        (self.root / "spool").mkdir()
        self.market_path = self.root / "market" / "market.sqlite3"
        self.staging_path = self.root / "private" / "gold-staging.sqlite3"
        self.connection = connect_market_store(self.market_path)
        initialize_market_store(self.connection)
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def _spool(self, name: str, *records: object) -> Path:
        path = self.root / "spool" / name
        path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
        return path

    def _invoke(self, *arguments: str) -> tuple[int, dict[str, object]]:
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = main(arguments)
        return result, json.loads(stream.getvalue())

    def test_ingests_root_bound_spools_in_retry_safe_order_and_redacts_output(self) -> None:
        self._spool("offer.jsonl", _outer(_offer()))
        self._spool("trade.jsonl", _outer(_trade()))
        arguments = (
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--staging-store", "private/gold-staging.sqlite3",
            "--offer-spool", "spool/offer.jsonl",
            "--trade-spool", "spool/trade.jsonl",
            "--as-of-utc", "2026-08-04T12:01:00Z",
        )
        result, payload = self._invoke(*arguments)
        self.assertEqual(
            (
                result,
                payload["status"],
                payload["records_read"],
                payload["promoted_offer_facts"],
                payload["promoted_trade_facts"],
                payload["refreshed_paper_minutes"],
            ),
            (0, "INGESTED", 2, 1, 1, 1),
        )
        self.assertNotIn(str(self.root), json.dumps(payload))
        self.assertNotIn("80,300,000", json.dumps(payload))

        result, repeated = self._invoke(*arguments)
        self.assertEqual((result, repeated["status"], repeated["staged_offer_changes"]), (0, "INGESTED", 0))
        facts = self.connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0]
        self.assertEqual(facts, 3)

    def test_outside_or_missing_paths_fail_before_any_ingestion(self) -> None:
        result, payload = self._invoke(
            "--runtime-root", str(self.root),
            "--market-store", str(self.root.parent / "outside.sqlite3"),
            "--staging-store", "private/gold-staging.sqlite3",
            "--offer-spool", "spool/missing.jsonl",
        )
        self.assertEqual((result, payload["reason"]), (2, "market_store_outside_runtime_root"))
        result, payload = self._invoke(
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--staging-store", "private/gold-staging.sqlite3",
            "--offer-spool", "spool/missing.jsonl",
        )
        self.assertEqual((result, payload["reason"]), (2, "offer_spool_unavailable"))

    def test_bad_records_are_counted_without_poisoning_valid_siblings(self) -> None:
        self._spool("offer.jsonl", {"wrong": "shape"}, _outer(_offer()))
        result, payload = self._invoke(
            "--runtime-root", str(self.root),
            "--market-store", "market/market.sqlite3",
            "--staging-store", "private/gold-staging.sqlite3",
            "--offer-spool", "spool/offer.jsonl",
            "--as-of-utc", "2026-08-04T12:01:00Z",
        )
        self.assertEqual((result, payload["records_read"], payload["records_rejected"], payload["promoted_offer_facts"]), (0, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
