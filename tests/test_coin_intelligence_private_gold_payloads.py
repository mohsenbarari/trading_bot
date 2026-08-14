"""Offline tests for strictly routed private melted-gold JSON envelopes."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.private_gold_payloads import (
    PrivateGoldPayloadEnvelope,
    decode_private_gold_payload,
    stage_private_gold_payload,
)
from core.market_intelligence.private_gold_staging import (
    connect_private_gold_staging,
    initialize_private_gold_staging,
    list_current_private_gold_staging,
)


def offer_event(*, message_id: str = "101", text: str = "80,300,000 فروش 5 تا با حواله", **changes: object) -> dict:
    gold = {
        "message_type": "offer",
        "message_id": message_id,
        "telegram_datetime": "2026-08-04T12:00:00Z",
        "text": text,
        **changes,
    }
    return {
        "schema_version": "1.0",
        "event_type": "message_created",
        "source": {"market": "gold", "source_key": "account1_channel"},
        "gold": gold,
    }


def trade_event(*, message_id: str = "101", **changes: object) -> dict:
    trade = {
        "status": "FULL",
        "traded_quantity": 5,
        "trade_detected_at": "2026-08-04T12:01:00Z",
        "telegram_edit_datetime": "2026-08-04T12:01:10Z",
        **changes,
    }
    return {
        "schema_version": "1.0",
        "event_type": "offer_verified",
        "source": {"market": "gold", "source_key": "account1_channel"},
        "gold": {"message_id": message_id, "verification": {"state": "DONE", "result": "traded"}, "trade": trade},
    }


class PrivateGoldPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect_private_gold_staging(Path(self.tempdir.name) / "private.sqlite3")
        initialize_private_gold_staging(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    @staticmethod
    def envelope(content: object, *, stream: str) -> PrivateGoldPayloadEnvelope:
        return PrivateGoldPayloadEnvelope(
            payload_text=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            available_at_utc="2026-08-04T12:02:00Z",
            stream=stream,
        )

    def test_offer_stream_stages_only_complete_routed_offer(self) -> None:
        envelope = self.envelope(offer_event(), stream="offer")
        decoded = decode_private_gold_payload(envelope)
        self.assertEqual((len(decoded.offers), decoded.trade_updates), (1, ()))
        report = stage_private_gold_payload(self.connection, envelope)
        self.connection.commit()
        row = list_current_private_gold_staging(self.connection, as_of_utc="2026-08-04T12:03:00Z")[0]
        self.assertEqual((report.decoded_offers, report.inserted_or_updated_offers), (1, 1))
        self.assertEqual((row.source_message_id, row.offer_available_at_utc), ("101", "2026-08-04T12:02:00Z"))

    def test_trade_stream_stages_verifier_without_needing_offer_text(self) -> None:
        report = stage_private_gold_payload(self.connection, self.envelope(trade_event(), stream="trade"))
        self.connection.commit()
        row = self.connection.execute(
            "SELECT offer_text, trade_status, traded_quantity FROM private_gold_staged_offers"
        ).fetchone()
        self.assertEqual((report.decoded_trade_updates, report.inserted_or_updated_trade_updates), (1, 1))
        self.assertEqual((row["offer_text"], row["trade_status"], row["traded_quantity"]), (None, "FULL", 5))

    def test_explicit_no_trade_without_trade_object_overrides_edit_inference(self) -> None:
        no_trade = {
            "schema_version": "1.0",
            "event_type": "offer_verified",
            "source": {"market": "gold", "source_key": "account1_channel"},
            "gold": {
                "message_id": "101",
                "verification": {"state": "completed", "result": "no_trade"},
                "trade": None,
            },
        }
        decoded = decode_private_gold_payload(self.envelope(no_trade, stream="trade"))
        self.assertEqual((len(decoded.trade_updates), decoded.trade_updates[0].trade_status), (1, "NONE"))
        report = stage_private_gold_payload(self.connection, self.envelope(no_trade, stream="trade"))
        self.connection.commit()
        row = self.connection.execute("SELECT trade_status FROM private_gold_staged_offers").fetchone()
        self.assertEqual((report.inserted_or_updated_trade_updates, row["trade_status"]), (1, "NONE"))

    def test_delimited_batch_deduplicates_and_later_edit_wins(self) -> None:
        original = offer_event()
        edited = offer_event(text="80,350,000 فروش 5 تا با حواله", telegram_edit_datetime="2026-08-04T12:01:10Z")
        payload = json.dumps(original, ensure_ascii=False) + "\n────\n" + json.dumps(edited, ensure_ascii=False)
        decoded = decode_private_gold_payload(self.envelope(payload, stream="OFFER"))
        self.assertEqual((len(decoded.offers), decoded.duplicate_items, decoded.conflicting_items), (1, 1, 0))
        self.assertEqual(decoded.offers[0].text, "80,350,000 فروش 5 تا با حواله")

    def test_wrong_stream_route_or_malformed_trade_cannot_reach_staging(self) -> None:
        wrong_stream = decode_private_gold_payload(self.envelope(offer_event(), stream="trade"))
        malformed = trade_event(traded_quantity=0)
        rejected = decode_private_gold_payload(self.envelope(malformed, stream="trade"))
        self.assertEqual((wrong_stream.offers, wrong_stream.trade_updates, wrong_stream.invalid_items), ((), (), 1))
        self.assertEqual((rejected.trade_updates, rejected.invalid_items), ((), 1))
        report = stage_private_gold_payload(self.connection, self.envelope(offer_event(), stream="trade"))
        self.assertEqual(report.inserted_or_updated_trade_updates, 0)

    def test_staging_rejection_is_not_mislabeled_as_an_idempotent_replay(self) -> None:
        oversized = offer_event(text="ف" * (32 * 1024 + 1))
        report = stage_private_gold_payload(self.connection, self.envelope(oversized, stream="offer"))

        self.assertEqual(
            (report.decoded_offers, report.inserted_or_updated_offers, report.staging_rejected_items, report.idempotent_replays),
            (1, 0, 1, 0),
        )

    def test_conflicting_same_message_without_ordered_update_is_dropped(self) -> None:
        first = trade_event(traded_quantity=5)
        second = trade_event(traded_quantity=4)
        decoded = decode_private_gold_payload(self.envelope([first, second], stream="trade"))
        self.assertEqual((decoded.trade_updates, decoded.conflicting_items), ((), 1))


if __name__ == "__main__":
    unittest.main()
