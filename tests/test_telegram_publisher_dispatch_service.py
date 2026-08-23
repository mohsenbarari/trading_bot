from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.telegram_gateway import TelegramGatewayResult
from core.services.telegram_publisher_dispatch_service import (
    TelegramPublisherDispatchError,
    accept_telegram_publisher_acknowledgement,
    accept_telegram_publisher_dispatch,
    dispatch_claimed_telegram_publisher_command,
    record_telegram_publisher_dispatch_result,
    render_telegram_publisher_dispatch,
    run_telegram_publisher_dispatch_cycle,
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

    async def test_first_acknowledgement_wakes_the_assigned_publisher_lane(self):
        command = _command()
        db = _DB(command)
        text = render_telegram_publisher_dispatch(command)

        with patch(
            "core.telegram_delivery_queue_wakeup.emit_delivery_queue_wakeup",
            new=AsyncMock(),
        ) as emit:
            accepted = await accept_telegram_publisher_dispatch(
                db,
                current_server="foreign",
                publisher_bot_identity="publisher_3",
                expected_primary_bot_id=100,
                sender_bot_id=100,
                text=text,
                now=NOW,
            )
            duplicate = await accept_telegram_publisher_dispatch(
                db,
                current_server="foreign",
                publisher_bot_identity="publisher_3",
                expected_primary_bot_id=100,
                sender_bot_id=100,
                text=text,
                now=NOW,
            )

        self.assertFalse(accepted.duplicate)
        self.assertTrue(duplicate.duplicate)
        emit.assert_awaited_once_with(db, bot_identity="publisher_3")
        self.assertEqual(db.flush.await_count, 1)

    async def test_acknowledgement_wakeup_stays_on_the_same_transaction(self):
        command = _command()
        db = _DB(command)

        with patch(
            "core.telegram_delivery_queue_wakeup.emit_delivery_queue_wakeup",
            new=AsyncMock(),
        ) as emit:
            await accept_telegram_publisher_dispatch(
                db,
                current_server="foreign",
                publisher_bot_identity="publisher_3",
                expected_primary_bot_id=100,
                sender_bot_id=100,
                text=render_telegram_publisher_dispatch(command),
                now=NOW,
            )

        self.assertIs(emit.await_args.args[0], db)
        self.assertEqual(emit.await_args.kwargs["bot_identity"], "publisher_3")

    async def test_primary_acknowledgement_wakes_the_lane_once(self):
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
            "core.telegram_delivery_queue_wakeup.emit_delivery_queue_wakeup",
            new=AsyncMock(),
        ) as emit, patch(
            "core.services.telegram_publisher_dispatch_service.metrics_registry.observe"
        ):
            first = await accept_telegram_publisher_acknowledgement(
                db,
                current_server="foreign",
                sender_bot_id=303,
                publisher_bot_ids={"publisher_3": 303},
                text=text,
                now=NOW,
            )
            second = await accept_telegram_publisher_acknowledgement(
                db,
                current_server="foreign",
                sender_bot_id=303,
                publisher_bot_ids={"publisher_3": 303},
                text=text,
                now=NOW,
            )

        self.assertTrue(first)
        self.assertTrue(second)
        emit.assert_awaited_once_with(db, bot_identity="publisher_3")

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

    async def test_dispatch_cycle_sends_a_bounded_batch_and_continues_after_one_failure(self):
        commands = []
        for index in range(1, 4):
            command = _command()
            command.command_id = f"123e4567-e89b-12d3-a456-42661417400{index}"
            command.id = index
            commands.append(command)
        leases = [
            TelegramPublisherDispatchLease(command=command, lease_token=1)
            for command in commands
        ]
        remaining = list(leases)

        async def claim(*_args, **_kwargs):
            return remaining.pop(0) if remaining else None

        results = [
            TelegramGatewayResult(ok=True, method="sendMessage"),
            TelegramGatewayResult(ok=False, method="sendMessage", error="timeout"),
            TelegramGatewayResult(ok=True, method="sendMessage"),
        ]
        gateway = AsyncMock(side_effect=results)

        class _CycleSession:
            def __init__(self):
                self.commit = AsyncMock()
                self.rollback = AsyncMock()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        with patch(
            "core.services.telegram_publisher_dispatch_service.claim_next_telegram_publisher_dispatch_command",
            new=AsyncMock(side_effect=claim),
        ) as claim_mock, patch(
            "core.services.telegram_publisher_dispatch_service.record_telegram_publisher_dispatch_result",
            new=AsyncMock(return_value=True),
        ) as record:
            report = await run_telegram_publisher_dispatch_cycle(
                session_factory=_CycleSession,
                current_server="foreign",
                publisher_bot_ids={"publisher_3": 303},
                gateway_call=gateway,
                limit=8,
                lease_seconds=30,
                retry_after_seconds=1,
                acknowledgement_timeout_seconds=15,
                request_timeout_seconds=5,
                now_factory=lambda: NOW,
            )

        self.assertEqual(report.claimed_count, 3)
        self.assertEqual(report.sent_count, 2)
        self.assertEqual(report.retry_due_count, 1)
        self.assertEqual(gateway.await_count, 3)
        self.assertEqual(record.await_count, 3)
        self.assertEqual(claim_mock.await_count, 4)
        self.assertEqual(
            [call.kwargs["command_id"] for call in record.await_args_list],
            [command.command_id for command in commands],
        )

    async def test_dispatch_cycle_does_not_claim_past_the_batch_limit(self):
        remaining = [
            TelegramPublisherDispatchLease(command=_command(), lease_token=index)
            for index in range(1, 5)
        ]

        async def claim(*_args, **_kwargs):
            return remaining.pop(0)

        class _CycleSession:
            def __init__(self):
                self.commit = AsyncMock()
                self.rollback = AsyncMock()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        gateway = AsyncMock(
            return_value=TelegramGatewayResult(ok=True, method="sendMessage")
        )
        with patch(
            "core.services.telegram_publisher_dispatch_service.claim_next_telegram_publisher_dispatch_command",
            new=AsyncMock(side_effect=claim),
        ), patch(
            "core.services.telegram_publisher_dispatch_service.record_telegram_publisher_dispatch_result",
            new=AsyncMock(return_value=True),
        ):
            report = await run_telegram_publisher_dispatch_cycle(
                session_factory=_CycleSession,
                current_server="foreign",
                publisher_bot_ids={"publisher_3": 303},
                gateway_call=gateway,
                limit=2,
                lease_seconds=30,
                retry_after_seconds=1,
                acknowledgement_timeout_seconds=15,
                request_timeout_seconds=5,
                now_factory=lambda: NOW,
            )

        self.assertEqual(report.claimed_count, 2)
        self.assertEqual(gateway.await_count, 2)
        self.assertEqual(len(remaining), 2)

    async def test_idle_dispatch_cycle_claims_nothing(self):
        class _CycleSession:
            def __init__(self):
                self.commit = AsyncMock()
                self.rollback = AsyncMock()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        gateway = AsyncMock()
        with patch(
            "core.services.telegram_publisher_dispatch_service.claim_next_telegram_publisher_dispatch_command",
            new=AsyncMock(return_value=None),
        ):
            report = await run_telegram_publisher_dispatch_cycle(
                session_factory=_CycleSession,
                current_server="foreign",
                publisher_bot_ids={"publisher_3": 303},
                gateway_call=gateway,
                limit=8,
                lease_seconds=30,
                retry_after_seconds=1,
                acknowledgement_timeout_seconds=15,
                request_timeout_seconds=5,
                now_factory=lambda: NOW,
            )

        self.assertEqual(report.claimed_count, 0)
        self.assertEqual(report.sent_count, 0)
        gateway.assert_not_awaited()

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
