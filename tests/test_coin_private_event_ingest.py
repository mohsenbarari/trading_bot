from __future__ import annotations

import unittest

from scripts.coin_intelligence_private_ingest.gold_offer_parser import parse
from scripts.coin_intelligence_private_ingest.listen_json_events import load_channel_config
from scripts.coin_intelligence_private_ingest.telegram_event_pipeline import (
    accepted_for_event_channel,
    decode_live_payloads,
    merge_account1_trade_update,
)


class PrivateEventConfigurationTests(unittest.TestCase):
    def test_private_channel_ids_are_loaded_only_from_environment(self) -> None:
        configured = load_channel_config(
            {
                "COIN_PRIVATE_EVENT_CHANNELS_JSON": (
                    '{"offer":{"id":-1001,"anchor_message_id":10},'
                    '"trade":{"id":-1002,"anchor_message_id":20},'
                    '"coin":{"id":-1003,"anchor_message_id":30}}'
                )
            }
        )
        self.assertEqual(configured["coin"]["anchor_message_id"], 30)

    def test_missing_or_incomplete_channel_config_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            load_channel_config({})
        with self.assertRaises(ValueError):
            load_channel_config(
                {"COIN_PRIVATE_EVENT_CHANNELS_JSON": '{"offer":{"id":-1,"anchor_message_id":1}}'}
            )


class PrivateEventTransportTests(unittest.TestCase):
    def test_single_array_and_delimited_batches_decode(self) -> None:
        self.assertEqual(list(decode_live_payloads('{"id":1}')), [{"id": 1}])
        self.assertEqual(
            list(decode_live_payloads('[{"id":1},{"id":2}]')),
            [{"id": 1}, {"id": 2}],
        )
        self.assertEqual(
            list(decode_live_payloads('{"id":1}\n────\n{"id":2}')),
            [{"id": 1}, {"id": 2}],
        )

    def test_stream_routing_rejects_misrouted_events(self) -> None:
        self.assertTrue(accepted_for_event_channel("trade", 10, "gold", "offer_verified"))
        self.assertFalse(accepted_for_event_channel("trade", 10, "gold", "message_created"))
        self.assertTrue(accepted_for_event_channel("coin", 10, "coin", "message_created"))
        self.assertFalse(accepted_for_event_channel("coin", 10, "gold", "message_created"))

    def test_trade_update_preserves_original_offer_fields(self) -> None:
        merged = merge_account1_trade_update(
            {"message_id": 9, "initial_offer_text": "فروش 100,000,000 با حواله"},
            {
                "message_id": 9,
                "trade": {
                    "status": "FULL",
                    "traded_quantity": 2,
                    "telegram_edit_datetime": "2026-08-01T10:01:00Z",
                },
            },
        )
        self.assertIn("فروش", merged["initial_offer_text"])
        self.assertEqual(merged["trade_status"], "FULL")
        self.assertEqual(merged["trade_time_source"], "telegram_edit_metadata")


class GoldOfferParserTests(unittest.TestCase):
    def test_undated_havale_defaults_to_paper_tomorrow(self) -> None:
        result = parse(
            {
                "message_id": 5_000_000_000,
                "initial_offer_text": "10 تا فروش 100,000,000 با حواله",
                "telegram_datetime": "2026-08-01T10:00:00Z",
            }
        )
        self.assertEqual(result["trade_form"], "PAPER")
        self.assertEqual(result["settlement"], "TOMORROW")

    def test_explicit_no_havale_is_physical_tomorrow(self) -> None:
        result = parse(
            {
                "message_id": 5_000_000_001,
                "initial_offer_text": "10 تا خرید 100,000,000 بی حواله فردا",
                "telegram_datetime": "2026-08-01T10:00:00Z",
            }
        )
        self.assertEqual(result["trade_form"], "PHYSICAL")
        self.assertEqual(result["settlement"], "TOMORROW")


if __name__ == "__main__":
    unittest.main()
