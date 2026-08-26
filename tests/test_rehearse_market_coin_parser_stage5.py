from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.capture_event_adapter import decode_coin_group_event
from scripts import rehearse_market_coin_parser_stage5 as rehearsal


class Stage5CoinParserRehearsalTests(unittest.TestCase):
    def test_fixture_contains_both_groups_reply_chain_and_valid_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("os.chown"):
                spool, feedback, prediction, partial = rehearsal.prepare_fixture(root)
            lines = spool.read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(lines[0]), {"invalid": "sibling"})
            decoded = [decode_coin_group_event(json.loads(line)) for line in lines[1:-1]]
            self.assertEqual({item.source_id for item in decoded}, {"GROUP_1", "GROUP_2"})
            self.assertEqual(sum(item.reply_to_message_id is not None for item in decoded), 2)
            self.assertFalse(lines[-1].endswith("}"))
            self.assertTrue(partial.endswith("}"))
            connection = sqlite3.connect(feedback)
            try:
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='coin_group_parser_feedback'"
                    ).fetchone()
                )
            finally:
                connection.close()
            connection = sqlite3.connect(prediction)
            try:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM coin_estimate_predictions"
                    ).fetchone()[0],
                    2,
                )
            finally:
                connection.close()

    def test_main_reports_only_bounded_reason_code(self) -> None:
        output = io.StringIO()
        with patch.object(
            rehearsal,
            "run_rehearsal",
            side_effect=rehearsal.Stage5RehearsalError("bounded_failure"),
        ), patch("sys.stderr", output):
            self.assertEqual(rehearsal.main(), 1)
        document = json.loads(output.getvalue())
        self.assertEqual(document, {"reason_code": "bounded_failure", "status": "fail"})


if __name__ == "__main__":
    unittest.main()
