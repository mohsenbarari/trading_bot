from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sqlite3

from scripts import audit_coin_group_parser_production as audit


def fact(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "event_type": "OFFER",
        "instrument": "COIN_IMAM",
        "settlement_term": "TOMORROW",
        "trade_form": "PHYSICAL",
        "side": "SELL",
        "price_value": "190000",
        "quantity_value": "2",
        "quality_state": "ELIGIBLE",
        "is_conditional": 0,
        "parser_version": "before",
    }
    value.update(overrides)
    return value


class ProductionCoinParserAuditTests(unittest.TestCase):
    def test_legacy_staging_is_cloned_to_current_schema_only_in_memory(self) -> None:
        source = sqlite3.connect(":memory:")
        source.row_factory = sqlite3.Row
        source.executescript(
            """
            CREATE TABLE coin_group_staging_metadata(
              singleton INTEGER PRIMARY KEY,
              schema_version INTEGER NOT NULL
            );
            INSERT INTO coin_group_staging_metadata VALUES(1,1);
            CREATE TABLE coin_group_staged_messages(
              group_number INTEGER NOT NULL,
              message_id INTEGER NOT NULL,
              event_time_utc TEXT NOT NULL,
              available_at_utc TEXT NOT NULL,
              edited_at_utc TEXT,
              reply_to_message_id INTEGER,
              sender_digest BLOB,
              message_text TEXT NOT NULL,
              content_digest BLOB NOT NULL,
              revision INTEGER NOT NULL,
              first_staged_at_utc TEXT NOT NULL,
              last_staged_at_utc TEXT NOT NULL,
              expires_at_utc TEXT NOT NULL,
              PRIMARY KEY(group_number,message_id)
            );
            """
        )
        source.execute(
            "INSERT INTO coin_group_staged_messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                10,
                "2026-08-25T10:00:00Z",
                "2026-08-25T10:00:01Z",
                None,
                None,
                b"s" * 32,
                "2 امام ف 188600",
                b"c" * 32,
                1,
                "2026-08-25T10:00:01Z",
                "2026-08-25T10:00:01Z",
                "2026-08-28T10:00:01Z",
            ),
        )
        source.commit()

        self.assertEqual(audit._verify_staging(source), 1)
        clone = audit._clone_staging_in_memory(source)
        try:
            self.assertEqual(
                clone.execute(
                    "SELECT schema_version FROM coin_group_staging_metadata"
                ).fetchone()[0],
                audit.COIN_GROUP_STAGING_SCHEMA_VERSION,
            )
            row = clone.execute(
                "SELECT message_text,sender_telegram_id,sender_display_name "
                "FROM coin_group_staged_messages"
            ).fetchone()
            self.assertEqual(tuple(row), ("2 امام ف 188600", None, None))
            self.assertEqual(source.total_changes, 2)
        finally:
            clone.close()
            source.close()

    def test_every_semantic_difference_gets_a_reason_code(self) -> None:
        baseline = {b"a" * 32: fact(), b"b" * 32: fact()}
        candidate = {
            b"a" * 32: fact(
                instrument="COIN_QUARTER_BAHAR",
                price_value="51900",
                parser_version="after",
            ),
            b"c" * 32: fact(),
        }
        result = audit._parity(baseline, candidate)
        self.assertEqual(result["events_changed"], 3)
        self.assertEqual(
            result["reason_code_counts"],
            {
                "BASELINE_EVENT_MISSING": 1,
                "CANDIDATE_EVENT_ADDED": 1,
                "INSTRUMENT_CHANGED": 1,
                "PARSER_VERSION_CHANGED": 1,
                "PRICE_CHANGED": 1,
            },
        )
        self.assertTrue(result["all_differences_reason_coded"])

    def test_failure_output_does_not_echo_paths_or_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret_path = Path(directory) / "sensitive-name.sqlite3"
            output = io.StringIO()
            with patch("sys.stdout", output):
                status = audit.main(
                    [
                        "--staging-database",
                        str(secret_path),
                        "--market-database",
                        str(secret_path),
                        "--feedback-database",
                        str(secret_path),
                        "--prediction-database",
                        str(secret_path),
                    ]
                )
            self.assertEqual(status, 2)
            document = json.loads(output.getvalue())
            self.assertEqual(document["reason_code"], "PRODUCTION_SHAPE_AUDIT_FAILED")
            self.assertNotIn("sensitive-name", output.getvalue())


if __name__ == "__main__":
    unittest.main()
