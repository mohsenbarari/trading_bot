from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.telegram_gateway import TelegramGatewayResult
from core.services.telegram_publisher_dispatch_service import (
    acknowledge_claimed_telegram_publisher_dispatch_locally,
    TelegramPublisherDispatchError,
    accept_telegram_publisher_acknowledgement,
    accept_telegram_publisher_dispatch,
    dispatch_claimed_telegram_publisher_command,
    record_telegram_publisher_dispatch_result,
    render_telegram_publisher_dispatch,
    run_co_located_telegram_publisher_dispatch_cycle,
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
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


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
        lease_token=4,
        attempt_count=0,
        sent_at=None,
        last_error_class=None,
        last_error_message=None,
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

    async def test_dispatch_result_records_send_without_using_ack_metric(self):
        command = _command()
        db = _DB(command)
        result = TelegramGatewayResult(
            ok=True,
            method="sendMessage",
            status_code=200,
            response_json={"ok": True, "result": {"message_id": 9}},
        )

        with patch(
            "core.services.telegram_publisher_dispatch_service.metrics_registry.observe"
        ) as observe:
            recorded = await record_telegram_publisher_dispatch_result(
                db,
                current_server="foreign",
                command_id=command.command_id,
                lease_token=4,
                result=result,
                retry_after_seconds=1,
                acknowledgement_timeout_seconds=15,
                now=NOW,
            )

        self.assertTrue(recorded)
        self.assertEqual(command.state, "sent")
        self.assertEqual(command.sent_at, NOW)
        observe.assert_not_called()
        db.flush.assert_awaited_once()

    async def test_co_located_dispatch_acknowledges_without_telegram_io(self):
        command = _command(state="retry_due")
        command.next_retry_at = NOW
        command.last_error_class = "telegram_b2b_send_failed"
        db = _DB(command)

        with patch(
            "core.services.telegram_publisher_dispatch_service.metrics_registry.observe"
        ) as observe:
            acknowledged = (
                await acknowledge_claimed_telegram_publisher_dispatch_locally(
                    db,
                    current_server="foreign",
                    command_id=command.command_id,
                    lease_token=4,
                    now=NOW,
                )
            )

        self.assertTrue(acknowledged)
        self.assertEqual(command.state, "acknowledged")
        self.assertEqual(command.receipt_sequence, 37)
        self.assertEqual(command.receipt_received_at, NOW)
        self.assertIsNone(command.next_retry_at)
        self.assertIsNone(command.last_error_class)
        observe.assert_called_once()
        db.flush.assert_awaited_once()

    async def test_co_located_cycle_claims_then_acknowledges_in_separate_transactions(self):
        command = _command(state="retry_due")
        lease = TelegramPublisherDispatchLease(command=command, lease_token=4)
        claim_db = _DB(command)
        acknowledgement_db = _DB(command)
        sessions = iter((claim_db, acknowledgement_db))

        with (
            patch(
                "core.services.telegram_publisher_dispatch_service."
                "claim_next_telegram_publisher_dispatch_command",
                new=AsyncMock(return_value=lease),
            ) as claim,
            patch(
                "core.services.telegram_publisher_dispatch_service."
                "acknowledge_claimed_telegram_publisher_dispatch_locally",
                new=AsyncMock(return_value=True),
            ) as acknowledge,
        ):
            report = await run_co_located_telegram_publisher_dispatch_cycle(
                session_factory=lambda: next(sessions),
                current_server="foreign",
                limit=1,
                lease_seconds=30,
                now_factory=lambda: NOW,
            )

        self.assertEqual(report.claimed_count, 1)
        self.assertEqual(report.sent_count, 1)
        self.assertEqual(report.retry_due_count, 0)
        claim.assert_awaited_once()
        acknowledge.assert_awaited_once_with(
            acknowledgement_db,
            current_server="foreign",
            command_id=command.command_id,
            lease_token=4,
            now=NOW,
        )
        claim_db.commit.assert_awaited_once()
        acknowledgement_db.commit.assert_awaited_once()

    async def test_primary_acknowledgement_records_ack_lag_once(self):
        command = _command(state="sent")
        db = _DB(command)
        text = render_telegram_publisher_b2b_envelope(
            TelegramPublisherB2BEnvelope(
                message_type=TelegramPublisherB2BMessageType.ACK,
                command_id=command.command_id,
                sequence=command.dispatch_sequence,
                enqueued_at=NOW,
                ack_sent_at=NOW,
            )
        )

        with patch(
            "core.services.telegram_publisher_dispatch_service.metrics_registry.observe"
        ) as observe:
            accepted = await accept_telegram_publisher_acknowledgement(
                db,
                current_server="foreign",
                sender_bot_id=303,
                publisher_bot_ids={"publisher_3": 303},
                text=text,
                now=NOW,
            )

        self.assertTrue(accepted)
        self.assertEqual(command.state, "acknowledged")
        observe.assert_called_once()
        self.assertEqual(observe.call_args.kwargs["lane"], "publisher_3")

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
