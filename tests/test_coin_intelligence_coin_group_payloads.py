"""Offline tests for single/batched private group event decoding."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.coin_group_payloads import (
    CoinGroupPayloadEnvelope,
    decode_coin_group_payload,
    stage_coin_group_payload,
)
from core.market_intelligence.coin_group_staging import (
    connect_coin_group_staging,
    initialize_coin_group_staging,
    list_current_staged_coin_group_messages,
)


def event(
    *,
    source_key: str = "account2_group1",
    message_id: str = "17",
    text: str = "امام فروش 186,900 / 5 تا",
    at: str = "2026-08-04T10:00:00Z",
    **payload_changes: object,
) -> dict:
    payload = {
        "message_id": message_id,
        "telegram_datetime": at,
        "text": text,
        "sender_name": "private sender",
        **payload_changes,
    }
    return {
        "event_type": "message_created",
        "source": {"market": "coin", "source_key": source_key},
        "coin": payload,
    }


class CoinGroupPayloadTests(unittest.TestCase):
    def envelope(self, content: object) -> CoinGroupPayloadEnvelope:
        return CoinGroupPayloadEnvelope(
            payload_text=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            available_at_utc="2026-08-04T10:00:05Z",
        )

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect_coin_group_staging(Path(self.tempdir.name) / "private.sqlite3")
        initialize_coin_group_staging(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def test_one_event_uses_collector_availability_and_stages_only_coin_group(self) -> None:
        decoded = decode_coin_group_payload(self.envelope(event()))
        self.assertEqual(len(decoded.messages), 1)
        message = decoded.messages[0]
        self.assertEqual((message.group_number, message.message_id, message.available_at_utc), (1, 17, "2026-08-04T10:00:05Z"))
        report = stage_coin_group_payload(self.connection, self.envelope(event()))
        self.connection.commit()
        self.assertEqual((report.decoded_messages, report.inserted_or_updated_messages), (1, 1))
        staged = list_current_staged_coin_group_messages(self.connection, as_of_utc="2026-08-04T10:01:00Z")
        self.assertEqual([(item.group_number, item.message_id) for item in staged], [(1, 17)])

    def test_array_and_documented_delimiter_split_without_duplicate_insertion(self) -> None:
        first = event(message_id="18")
        second = event(source_key="account2_group2", message_id="19", at="2026-08-04T10:00:01Z")
        array = decode_coin_group_payload(self.envelope([first, first, second]))
        self.assertEqual((len(array.messages), array.duplicate_items, array.invalid_items), (2, 1, 0))
        divided = json.dumps(first, ensure_ascii=False) + "\n────\n" + json.dumps(second, ensure_ascii=False)
        decoded = decode_coin_group_payload(self.envelope(divided))
        self.assertEqual([(item.group_number, item.message_id) for item in decoded.messages], [(1, 18), (2, 19)])

    def test_cross_routed_or_malformed_items_cannot_reach_staging(self) -> None:
        cross = event()
        cross["source"] = {"market": "gold", "source_key": "account2_group1"}
        missing_time = event(message_id="18")
        del missing_time["coin"]["telegram_datetime"]
        decoded = decode_coin_group_payload(self.envelope([cross, missing_time, {"bad": "shape"}]))
        self.assertEqual(decoded.messages, ())
        self.assertEqual(decoded.invalid_items, 3)
        report = stage_coin_group_payload(self.connection, self.envelope([cross, missing_time]))
        self.assertEqual(report.inserted_or_updated_messages, 0)

    def test_conflicting_same_message_without_ordered_edit_is_dropped(self) -> None:
        first = event(text="امام فروش 186,900 / 5 تا")
        second = event(text="امام فروش 187,000 / 5 تا")
        decoded = decode_coin_group_payload(self.envelope([first, second]))
        self.assertEqual((decoded.messages, decoded.conflicting_items), ((), 1))

    def test_later_edit_wins_and_unresolved_reply_never_invents_parent(self) -> None:
        original = event()
        edited = event(
            text="امام فروش 187,000 / 5 تا",
            telegram_edit_datetime="2026-08-04T10:00:03Z",
            reply_detected=True,
            reply_reference_status="ambiguous_preview_match",
            reply_message_id="12",
        )
        decoded = decode_coin_group_payload(self.envelope([original, edited]))
        self.assertEqual(len(decoded.messages), 1)
        self.assertEqual((decoded.messages[0].text, decoded.messages[0].reply_to_message_id), ("امام فروش 187,000 / 5 تا", None))


if __name__ == "__main__":
    unittest.main()
