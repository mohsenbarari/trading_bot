from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from core.telegram_gateway import TelegramGatewayResult
from core.services.telegram_publisher_dispatch_service import (
    TelegramPublisherDispatchError,
    accept_telegram_publisher_dispatch,
    dispatch_claimed_telegram_publisher_command,
    render_telegram_publisher_dispatch,
    select_telegram_publisher_lane,
    TelegramPublisherDispatchLease,
)
from core.telegram_multi_publisher_contract import (
    TelegramPublisherB2BEnvelope,
    TelegramPublisherB2BMessageType,
    render_telegram_publisher_b2b_envelope,
)


NOW = datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)
PUBLISHERS = tuple(f"publisher_{index}" for index in range(1, 6))


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, command=None):
        self.command = command
        self.execute = AsyncMock(return_value=_Result(command))
        self.flush = AsyncMock()


def _command(*, state="pending"):
    return SimpleNamespace(
        command_id="123e4567-e89b-12d3-a456-426614174000",
        publisher_bot_identity="publisher_3",
        dispatch_sequence=37,
        state=state,
        created_at=NOW,
        acknowledged_at=None,
        receipt_sequence=None,
        receipt_received_at=None,
        lease_until=None,
        next_retry_at=None,
        updated_at=None,
    )


class TelegramPublisherDispatchServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_least_in_flight_selection_spreads_120_commands_evenly(self):
        counts = {identity: 0 for identity in PUBLISHERS}
        selections = []
        for sequence in range(1, 121):
            selected = select_telegram_publisher_lane(
                healthy_publishers=PUBLISHERS,
                in_flight_counts=counts,
                round_robin_sequence=sequence,
            )
            selections.append(selected.publisher_bot_identity)
            counts[selected.publisher_bot_identity] += 1

        self.assertEqual(set(selections), set(PUBLISHERS))
        self.assertEqual(set(counts.values()), {24})

    def test_dispatch_envelope_contains_only_contract_metadata(self):
        text = render_telegram_publisher_dispatch(_command())

        self.assertEqual(
            text,
            "tbq1|dispatch|123e4567-e89b-12d3-a456-426614174000|37|"
            "2026-08-11T18:00:00Z",
        )
        self.assertNotIn("price", text)
        self.assertNotIn("offer", text)

    async def test_worker_rejects_forged_sender_before_database_access(self):
        db = _DB()
        text = render_telegram_publisher_dispatch(_command())

        with self.assertRaisesRegex(
            TelegramPublisherDispatchError,
            "sender_not_allowlisted",
        ):
            await accept_telegram_publisher_dispatch(
                db,
                current_server="foreign",
                publisher_bot_identity="publisher_3",
                expected_primary_bot_id=100,
                sender_bot_id=101,
                text=text,
                now=NOW,
            )
        db.execute.assert_not_awaited()

    async def test_worker_acknowledges_assigned_command_idempotently(self):
        command = _command()
        db = _DB(command)
        text = render_telegram_publisher_dispatch(command)

        accepted = await accept_telegram_publisher_dispatch(
            db,
            current_server="foreign",
            publisher_bot_identity="publisher_3",
            expected_primary_bot_id=100,
            sender_bot_id=100,
            text=text,
            now=NOW,
        )

        self.assertFalse(accepted.duplicate)
        self.assertEqual(command.state, "acknowledged")
        self.assertEqual(command.receipt_sequence, 37)
        self.assertIn("tbq1|ack|123e4567-e89b-12d3-a456-426614174000|37|", accepted.acknowledgement_text)
        db.flush.assert_awaited_once()

        duplicate = await accept_telegram_publisher_dispatch(
            db,
            current_server="foreign",
            publisher_bot_identity="publisher_3",
            expected_primary_bot_id=100,
            sender_bot_id=100,
            text=text,
            now=NOW,
        )
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(db.flush.await_count, 1)

    async def test_dispatch_uses_primary_gateway_and_publisher_private_chat_only(self):
        command = _command()
        lease = TelegramPublisherDispatchLease(command=command, lease_token=2)
        gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 9}},
            )
        )

        await dispatch_claimed_telegram_publisher_command(
            lease,
            publisher_bot_ids={"publisher_3": 303},
            gateway_call=gateway,
            timeout_seconds=5,
        )

        method, payload = gateway.await_args.args
        self.assertEqual(method, "sendMessage")
        self.assertEqual(payload["chat_id"], 303)
        self.assertEqual(payload["text"], render_telegram_publisher_dispatch(command))
        self.assertEqual(gateway.await_args.kwargs["idempotency_key"], "telegram-b2b:123e4567-e89b-12d3-a456-426614174000:2")

    async def test_worker_rejects_ack_as_a_dispatch_command(self):
        db = _DB(_command())
        text = render_telegram_publisher_b2b_envelope(
            TelegramPublisherB2BEnvelope(
                message_type=TelegramPublisherB2BMessageType.ACK,
                command_id="123e4567-e89b-12d3-a456-426614174000",
                sequence=37,
                enqueued_at=NOW,
                ack_sent_at=NOW,
            )
        )

        with self.assertRaisesRegex(
            TelegramPublisherDispatchError,
            "dispatch_type_required",
        ):
            await accept_telegram_publisher_dispatch(
                db,
                current_server="foreign",
                publisher_bot_identity="publisher_3",
                expected_primary_bot_id=100,
                sender_bot_id=100,
                text=text,
                now=NOW,
            )
        db.execute.assert_not_awaited()

    def test_publisher_queue_claim_requires_a_durable_acknowledgement(self):
        source = Path(
            "core/services/telegram_delivery_queue_service.py"
        ).read_text(encoding="utf-8")

        self.assertIn("TelegramPublisherDispatchCommand", source)
        self.assertIn(
            'TelegramPublisherDispatchCommand.state == "acknowledged"',
            source,
        )


if __name__ == "__main__":
    unittest.main()
