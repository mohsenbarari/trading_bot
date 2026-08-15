from scripts.collect_coin_group_event_telegram import (
    _event_message,
    collector_failure_reason,
    decode_event_envelopes,
)
from core.market_intelligence.coin_group_staging import CoinGroupStagingError


def _envelope() -> dict[str, object]:
    return {
        "event_type": "message_created",
        "occurred_at_utc": "2026-08-13T09:00:00Z",
        "source": {"market": "coin", "group_number": 1},
        "coin": {
            "message_id": "42",
            "telegram_datetime": "2026-08-13T09:00:00Z",
            "telegram_edit_datetime": None,
            "reply_message_id": "41",
            "sender_peer_id": "opaque-user",
            "text": "خ 186900 / 5 تا",
        },
    }


def test_decode_event_envelopes_accepts_known_batch_delimiter() -> None:
    import json

    payload = json.dumps(_envelope(), ensure_ascii=False) + "\n━━━\n" + json.dumps(
        _envelope(), ensure_ascii=False
    )
    assert len(tuple(decode_event_envelopes(payload))) == 2


def test_event_message_preserves_group_semantics_for_bounded_staging() -> None:
    message = _event_message(
        _envelope(), received_at_utc="2026-08-13T09:00:03Z"
    )
    assert message is not None
    assert message.group_number == 1
    assert message.message_id == 42
    assert message.reply_to_message_id == 41
    assert message.available_at_utc == "2026-08-13T09:00:03Z"


def test_event_message_rejects_unrelated_market() -> None:
    envelope = _envelope()
    envelope["source"] = {"market": "gold", "group_number": 1}
    assert _event_message(envelope, received_at_utc="2026-08-13T09:00:03Z") is None


def test_event_message_rejects_contradictory_edit_without_poisoning_batch() -> None:
    envelope = _envelope()
    envelope["coin"]["telegram_edit_datetime"] = "2026-08-13T08:59:59Z"

    assert _event_message(envelope, received_at_utc="2026-08-13T09:00:03Z") is None


def test_collector_exposes_operator_safe_staging_reason() -> None:
    reason = collector_failure_reason(
        CoinGroupStagingError("coin_group_staging_timestamp_order_invalid")
    )

    assert reason == "coin_group_staging_timestamp_order_invalid"


def test_collector_redacts_unclassified_failure_detail() -> None:
    reason = collector_failure_reason(RuntimeError("sensitive upstream detail"))

    assert reason == "coin_group_event_collect_failed:RuntimeError"
