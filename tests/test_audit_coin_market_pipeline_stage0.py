import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import audit_coin_market_pipeline_stage0 as stage0


class CoinMarketPipelineStage0AuditTests(unittest.TestCase):
    def test_jsonl_summary_never_emits_payload_or_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            private_text = "متن خصوصی که نباید خارج شود"
            private_source = "-100-secret-telegram-id"
            event = {
                "event_id": "sensitive-event-id",
                "event_type": "message_created",
                "schema_version": 2,
                "occurred_at_utc": "2026-08-25T08:00:00Z",
                "source": {"source_id": private_source, "market": "private-market"},
                "message": {
                    "message_id": 123,
                    "text": private_text,
                    "entities": [{"type": "phone", "value": "09120000000"}],
                },
                "producer": {
                    "available_at_utc": "2026-08-25T08:00:01Z",
                    "is_backfill": False,
                },
            }
            path.write_text(
                json.dumps(event, ensure_ascii=False) + "\n{broken-json\n",
                encoding="utf-8",
            )

            summary = stage0.summarize_jsonl_files([path])
            rendered = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["records"], 1)
        self.assertEqual(summary["invalid_records"], 1)
        self.assertEqual(summary["source_count"], 1)
        self.assertEqual(summary["daily_files"][0]["records"], 1)
        self.assertEqual(summary["daily_files"][0]["invalid_records"], 1)
        self.assertFalse(summary["quality_metric_availability"]["duplicate_rate"])
        self.assertNotIn(private_text, rendered)
        self.assertNotIn(private_source, rendered)
        self.assertNotIn("09120000000", rendered)
        self.assertNotIn("private-market", rendered)

    def test_sqlite_summary_reads_only_count_and_time_bounds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "capture.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE events (id INTEGER, event_time_utc TEXT, raw_text TEXT)"
            )
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?)",
                (1, "2026-08-25T09:00:00Z", "private payload"),
            )
            connection.commit()
            connection.close()

            summary = stage0.summarize_sqlite(
                path,
                (("events", "event_time_utc"),),
            )
            rendered = json.dumps(summary)

        self.assertEqual(summary["tables"]["events"]["rows"], 1)
        self.assertEqual(
            summary["tables"]["events"]["time_bounds"]["maximum"],
            "2026-08-25T09:00:00Z",
        )
        self.assertNotIn("private payload", rendered)

    def test_sanitizer_rejects_forbidden_nested_keys(self):
        with self.assertRaisesRegex(ValueError, "message_id"):
            stage0.assert_sanitized({"safe": [{"message_id": 1}]})

    def test_heartbeat_summary_allowlists_metrics_and_discards_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "heartbeat.json"
            path.write_text(
                json.dumps(
                    {
                        "connected": True,
                        "outbox_count": 0,
                        "text": "must-not-leak",
                        "sources": {
                            "XAUUSD": {"created": 12, "duplicate": 2, "payload": "secret"},
                            "not a contract code": {"created": 99},
                        },
                    }
                ),
                encoding="utf-8",
            )
            summary = stage0.summarize_heartbeat(path)
            rendered = json.dumps(summary)

        self.assertTrue(summary["connected"])
        self.assertEqual(summary["sources"]["XAUUSD"]["duplicate"], 2)
        self.assertNotIn("must-not-leak", rendered)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("not a contract code", rendered)

    def test_model_state_summary_excludes_paths_and_health_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "generated_at_utc": "2026-08-25T10:00:00Z",
                        "service_status": "ready",
                        "input_health": {
                            "status": "healthy",
                            "collectors": {
                                "coin_group_projection": {
                                    "status": "healthy",
                                    "details": "private detail",
                                    "last_success_at_utc": "2026-08-25T09:59:59Z",
                                }
                            },
                        },
                        "shadow_parallel": {
                            "enabled": True,
                            "status": "ready",
                            "shadow_model_path": "/private/model/path",
                        },
                    }
                ),
                encoding="utf-8",
            )
            summary = stage0.summarize_model_state(path)
            rendered = json.dumps(summary)

        self.assertEqual(summary["input_health"]["status"], "healthy")
        self.assertTrue(summary["shadow_parallel"]["enabled"])
        self.assertNotIn("private detail", rendered)
        self.assertNotIn("/private/model/path", rendered)


if __name__ == "__main__":
    unittest.main()
