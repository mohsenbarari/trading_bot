import copy
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from core.market_intelligence.private_pipeline_contracts import (
    ESTIMATOR_RATE_GRID_V1,
    EstimatorSnapshotV1,
    EstimatorSnapshotV2,
    MarketCaptureRecordV1,
    MarketFactBatchV1,
    MarketFactDeliveryV1,
    MarketFactV1,
    batch_items_hash,
    content_hash,
    estimator_snapshot_id,
    exported_schemas,
    load_source_registry,
)
from scripts.export_market_private_pipeline_schemas import render, run as export_schemas


FIXTURES = Path(__file__).parent / "fixtures" / "market_private_pipeline"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def estimator_snapshot_fixture():
    rates = []
    for index, (instrument, settlement) in enumerate(ESTIMATOR_RATE_GRID_V1):
        center = 187_450 - index * 5_000
        rates.append(
            {
                "instrument": instrument,
                "settlement": settlement,
                "status": "ESTIMATED",
                "value": str(center),
                "unit": "PROJECT_THOUSAND_TOMAN",
                "lower_bound": str(center - 500),
                "upper_bound": str(center + 500),
                "confidence": "HIGH",
                "method": "WEIGHTED_BOOK",
                "reason_code": None,
                "underlying_source": "PRIVATE_PHYSICAL_TODAY",
                "underlying_age_seconds": 4.0,
                "anchor_age_seconds": 30.0,
                "market_regime": "RANGE",
            }
        )
    payload = {
        "contract": "estimator_snapshot/2.0",
        "snapshot_version": 1,
        "generated_at_utc": "2026-08-26T05:00:05Z",
        "input_snapshot_hash": content_hash([]),
        "model_version": "fixture-model-v1",
        "feed_mode": "PRIVATE_SHADOW",
        "status": "OK",
        "rates": rates,
        "health": [],
        "inputs": [],
        "reason_codes": [],
    }
    payload["snapshot_id"] = estimator_snapshot_id(payload)
    return payload


class MarketPrivatePipelineContractTests(unittest.TestCase):
    def test_fixture_contracts_and_exported_schemas_are_current(self):
        MarketCaptureRecordV1.model_validate(fixture("capture_record.json"))
        MarketFactV1.model_validate(fixture("market_fact.json"))
        MarketFactBatchV1.model_validate(fixture("market_fact_batch.json"))
        EstimatorSnapshotV1.model_validate(fixture("estimator_snapshot.json"))
        EstimatorSnapshotV2.model_validate(estimator_snapshot_fixture())

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.assertEqual(export_schemas(output, write=True), 0)
            self.assertEqual(export_schemas(output, write=False), 0)
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                sorted(exported_schemas()),
            )
            for name, schema in exported_schemas().items():
                self.assertEqual((output / name).read_text(), render(schema))
                references = []

                def collect_references(value):
                    if isinstance(value, dict):
                        for key, child in value.items():
                            if key == "$ref":
                                references.append(child)
                            collect_references(child)
                    elif isinstance(value, list):
                        for child in value:
                            collect_references(child)

                collect_references(schema)
                self.assertTrue(
                    all(reference.startswith("#/$defs/") for reference in references),
                    f"{name} must remain a self-contained JSON Schema",
                )

    def test_source_registry_matches_capture_and_retention_decisions(self):
        sources = load_source_registry().by_code()
        expected = {
            "GROUP_1",
            "GROUP_2",
            "PRIVATE_GOLD_CHANNEL",
            "USD_HERAT",
            "XAUUSD",
            "WALLEX_PUBLIC_API",
            "BINANCE_PAXG_PUBLIC_API",
            "MELTED_AGGREGATE",
            "MELTED_FLOW",
            "PRIVATE_GOLD_PAPER_MINUTE",
            "IME_REALTIME_BOARD",
        }
        self.assertEqual(set(sources), expected)
        self.assertTrue(sources["GROUP_1"].permanent_archive)
        self.assertTrue(sources["XAUUSD"].permanent_archive)
        self.assertTrue(sources["MELTED_AGGREGATE"].permanent_archive)
        self.assertTrue(sources["MELTED_FLOW"].permanent_archive)
        self.assertFalse(sources["IME_REALTIME_BOARD"].capture_enabled)
        self.assertEqual(sources["GROUP_2"].raw_retention_seconds, 259_200)

    def test_capture_hash_time_retention_and_source_binding_fail_closed(self):
        original = fixture("capture_record.json")
        for mutation in (
            {"payload_hash": "0" * 64},
            {"retention_until_utc": "2026-08-28T05:00:02Z"},
            {"stream_id": "capture.coin.group.2"},
            {"available_at_utc": "2026-08-26T04:59:59Z"},
        ):
            payload = {**original, **mutation}
            with self.assertRaises(ValidationError):
                MarketCaptureRecordV1.model_validate(payload)

    def test_fact_rejects_raw_identity_and_non_string_price(self):
        original = fixture("market_fact.json")
        raw_identity = copy.deepcopy(original)
        raw_identity["payload"]["telegram_id"] = "123456"
        with self.assertRaises(ValidationError):
            MarketFactV1.model_validate(raw_identity)

        numeric_price = copy.deepcopy(original)
        numeric_price["payload"]["offered_price_value"] = 187500.0
        numeric_price["payload_hash"] = content_hash(numeric_price["payload"])
        with self.assertRaises(ValidationError):
            MarketFactV1.model_validate(numeric_price)

    def test_coin_trade_can_publish_agreed_terms_different_from_offer(self):
        payload = fixture("market_fact.json")
        payload["fact_id"] = "f" * 64
        payload["source_sequence"] = 2
        payload["payload"] = {
            "kind": "COIN_TRADE",
            "offer_fact_id": "b" * 64,
            "outcome": "CONFIRMED_PARTIAL",
            "agreed_price_value": "187300",
            "price_unit": "PROJECT_THOUSAND_TOMAN",
            "agreed_quantity_value": "2",
            "quantity_unit": "COIN",
        }
        payload["payload_hash"] = content_hash(payload["payload"])
        fact = MarketFactV1.model_validate(payload)
        self.assertEqual(fact.payload.agreed_price_value, "187300")
        self.assertEqual(fact.payload.agreed_quantity_value, "2")

    def test_private_gold_offer_has_no_final_price_or_quantity(self):
        payload = fixture("market_fact.json")
        payload.update(
            fact_id="1" * 64,
            source_code="PRIVATE_GOLD_CHANNEL",
            stream_id="market.fact.private-gold",
        )
        payload["payload"] = {
            "kind": "PRIVATE_GOLD_OFFER",
            "instrument": "MELTED_GOLD_PRIVATE",
            "side": "SELL",
            "settlement": "TOMORROW",
            "trade_form": "PAPER_NORMAL",
            "offered_price_value": "95200000",
            "price_unit": "TOMAN_PER_MESGHAL_750",
            "quantity_value": "10",
            "quantity_unit": "MESGHAL",
            "lifetime_seconds": 120,
        }
        payload["payload_hash"] = content_hash(payload["payload"])
        MarketFactV1.model_validate(payload)
        payload["payload"]["final_price"] = "95300000"
        payload["payload_hash"] = content_hash(payload["payload"])
        with self.assertRaises(ValidationError):
            MarketFactV1.model_validate(payload)

    def test_batch_requires_contiguous_exactly_hashed_items(self):
        batch = fixture("market_fact_batch.json")
        delivery = MarketFactDeliveryV1.model_validate(batch["items"][0])
        self.assertEqual(batch["items_hash"], batch_items_hash([delivery]))

        gap = copy.deepcopy(batch)
        gap["items"][0]["delivery_sequence"] = 2
        gap["items_hash"] = content_hash(gap["items"])
        with self.assertRaises(ValidationError):
            MarketFactBatchV1.model_validate(gap)

    def test_fact_revision_keeps_source_sequence_but_gets_new_delivery_sequence(self):
        batch = fixture("market_fact_batch.json")
        first = copy.deepcopy(batch["items"][0])
        revised = copy.deepcopy(first)
        revised["delivery_sequence"] = 2
        revised["fact"]["fact_revision"] = 2
        revised["fact"]["payload"]["offered_price_value"] = "187600"
        revised["fact"]["payload_hash"] = content_hash(revised["fact"]["payload"])
        batch["last_sequence"] = 2
        batch["item_count"] = 2
        batch["items"] = [first, revised]
        batch["items_hash"] = content_hash(batch["items"])
        validated = MarketFactBatchV1.model_validate(batch)
        self.assertEqual(
            [item.fact.source_sequence for item in validated.items], [1, 1]
        )

    def test_safe_no_data_cannot_carry_rates(self):
        snapshot = estimator_snapshot_fixture()
        snapshot["status"] = "SAFE_NO_DATA"
        with self.assertRaises(ValidationError):
            EstimatorSnapshotV2.model_validate(snapshot)

    def test_v1_and_v2_reject_cross_version_documents(self):
        legacy = fixture("estimator_snapshot.json")
        current = estimator_snapshot_fixture()
        with self.assertRaises(ValidationError):
            EstimatorSnapshotV2.model_validate(legacy)
        with self.assertRaises(ValidationError):
            EstimatorSnapshotV1.model_validate(current)

    def test_v1_keeps_its_published_toman_per_coin_unit(self):
        legacy = fixture("estimator_snapshot.json")
        legacy["rates"][0]["unit"] = "TOMAN_PER_COIN"
        EstimatorSnapshotV1.model_validate(legacy)


if __name__ == "__main__":
    unittest.main()
