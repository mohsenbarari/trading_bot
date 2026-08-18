import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

from scripts.collect_coin_group_event_telegram import (
    _event_message,
    _incremental_channel_messages,
    build_parser,
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


def test_collector_reads_prediction_ledger_path_from_environment() -> None:
    with patch.dict(
        os.environ,
        {"COIN_GROUP_ESTIMATOR_CALIBRATION_DB": "/runtime/predictions.sqlite3"},
    ):
        args = build_parser().parse_args(["--runtime-root", "/runtime"])

    assert args.estimator_calibration_db == "/runtime/predictions.sqlite3"


def test_incremental_reader_fetches_newest_first_and_restores_causal_order() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def iter_messages(self, _entity: object, **kwargs: object):
            self.calls.append(kwargs)
            for message_id in (14, 13, 12):
                yield SimpleNamespace(id=message_id)

    client = FakeClient()
    messages = asyncio.run(
        _incremental_channel_messages(
            client,
            object(),
            minimum_id=11,
            maximum_messages=10,
        )
    )

    assert [message.id for message in messages] == [12, 13, 14]
    assert client.calls == [{"min_id": 11, "reverse": False, "limit": 11}]


def test_incremental_reader_refetches_oldest_batch_when_backlog_exceeds_limit() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def iter_messages(self, _entity: object, **kwargs: object):
            self.calls.append(kwargs)
            ids = (15, 14, 13, 12) if kwargs["reverse"] is False else (12, 13, 14)
            for message_id in ids:
                yield SimpleNamespace(id=message_id)

    client = FakeClient()
    messages = asyncio.run(
        _incremental_channel_messages(
            client,
            object(),
            minimum_id=11,
            maximum_messages=3,
        )
    )

    assert [message.id for message in messages] == [12, 13, 14]
    assert client.calls == [
        {"min_id": 11, "reverse": False, "limit": 4},
        {"min_id": 11, "reverse": True, "limit": 3},
    ]
