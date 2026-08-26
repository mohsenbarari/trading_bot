from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
