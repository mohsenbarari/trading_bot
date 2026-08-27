from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from scripts.compare_coin_offer_parser_stream import (
    EconomicOffer,
    classify_change,
    compare_stream,
)


def offer(instrument: str, price: int) -> EconomicOffer:
    return EconomicOffer(
        instrument=instrument,
        price=price,
        quantity=10,
        side="SELL",
        settlement="CASH",
        trade_form="PHYSICAL",
        conditional=False,
        quality="ELIGIBLE",
    )


class CoinOfferParserStreamComparisonTests(unittest.TestCase):
    @staticmethod
    def module(path: Path, *, version: str):
        def parse(source):
            return [
                SimpleNamespace(
                    commodity_code="IMAM",
                    price_project_thousand_toman=188_600,
                    quantity=5,
                    side="SELL",
                    settlement_term="CASH",
                    trade_form="PHYSICAL",
                    is_conditional=False,
                    quality_state="ELIGIBLE",
                )
            ]

        return SimpleNamespace(
            __file__=str(path),
            COIN_GROUP_PARSER_VERSION=version,
            CoinGroupMessageInput=SimpleNamespace,
            parse_coin_group_offers=parse,
        )

    @staticmethod
    def event() -> bytes:
        return (
            json.dumps(
                {
                    "schema": "coin_group_event",
                    "schema_version": "2.0",
                    "event_id": "event-id-long-enough",
                    "event_type": "message_created",
                    "occurred_at_utc": "2026-08-25T10:00:01Z",
                    "source": {"source_id": "GROUP_1"},
                    "message": {
                        "message_id": "10",
                        "published_at_utc": "2026-08-25T10:00:00Z",
                        "text": "5 امام نقدی ف 188600",
                    },
                    "producer": {},
                },
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )

    def test_exact_economics_are_equal(self):
        value = (offer("QUARTER_BAHAR", 51_900),)
        self.assertEqual(classify_change("10 ربع ف 51900", value, value), "EXACT")

    def test_only_reviewed_additive_shapes_are_accepted_as_gains(self):
        value = (offer("QUARTER_BAHAR", 51_900),)
        self.assertEqual(
            classify_change(
                "10 تا ربع نقدی\nف ۵۱.۹۰۰",
                (),
                value,
                collapsed_baseline=value,
            ),
            "REVIEWED_MULTILINE_SINGLE_OFFER_GAIN",
        )
        self.assertEqual(
            classify_change(
                "10 تا ربع ف ۵۱۹۰۰۰۰",
                (),
                value,
                corrected_zero_baselines=(value,),
            ),
            "REVIEWED_LOW_PRICE_DUPLICATED_ZERO_GAIN",
        )

    def test_shape_labels_are_not_enough_without_baseline_equivalence_proof(self):
        value = (offer("QUARTER_BAHAR", 51_900),)
        self.assertEqual(
            classify_change("10 تا ربع نقدی\nف ۵۱.۹۰۰", (), value),
            "UNREVIEWED_COVERAGE_GAIN",
        )
        self.assertEqual(
            classify_change("10 تا ربع ف ۵۱۹۰۰۰۰", (), value),
            "UNREVIEWED_COVERAGE_GAIN",
        )

    def test_unreviewed_addition_removal_or_mutation_blocks(self):
        quarter = (offer("QUARTER_BAHAR", 51_900),)
        half = (offer("HALF_BAHAR", 95_000),)
        self.assertEqual(
            classify_change("متن تازه", (), quarter), "UNREVIEWED_COVERAGE_GAIN"
        )
        self.assertEqual(
            classify_change("10 ربع ف 51900", quarter, ()),
            "UNREVIEWED_ECONOMIC_DRIFT",
        )
        self.assertEqual(
            classify_change("10 ربع ف 51900", quarter, half),
            "UNREVIEWED_ECONOMIC_DRIFT",
        )

    def test_stream_gate_accepts_exact_economics_and_counts_legacy_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_file = root / "baseline.py"
            candidate_file = root / "candidate.py"
            baseline_file.write_text("baseline", encoding="utf-8")
            candidate_file.write_text("candidate", encoding="utf-8")
            report = compare_stream(
                BytesIO(self.event()),
                baseline_module=self.module(baseline_file, version="v9"),
                candidate_module=self.module(candidate_file, version="v10"),
            )

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["economically_exact_messages"], 1)
        self.assertEqual(report["available_timestamp_fallbacks"], 1)
        self.assertFalse(report["sensitive_payload_emitted"])

    def test_stream_gate_rejects_changed_code_with_reused_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_file = root / "baseline.py"
            candidate_file = root / "candidate.py"
            baseline_file.write_text("baseline", encoding="utf-8")
            candidate_file.write_text("candidate", encoding="utf-8")
            report = compare_stream(
                BytesIO(self.event()),
                baseline_module=self.module(baseline_file, version="same"),
                candidate_module=self.module(candidate_file, version="same"),
            )

        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["parser_version_collision"])


if __name__ == "__main__":
    unittest.main()
