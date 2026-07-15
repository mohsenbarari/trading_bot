import asyncio
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.telegram_delivery_queue_contract import (
    InMemoryTelegramDeliveryQueue,
    TelegramDeliveryDedupeConflictError,
    TelegramDeliveryOutcome,
    TelegramDeliveryPriority,
    TelegramDeliveryState,
    TelegramOfferChannelOrder,
    build_terminal_offer_edit_call,
    reconcile_ambiguous_send,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class FakeGatewayResult:
    ok: bool
    method: str
    status_code: int | None
    response_json: dict[str, Any] | None
    response_text: str
    error: str | None

    @property
    def message_id(self) -> int | None:
        result = self.response_json.get("result") if self.response_json else None
        if not isinstance(result, dict):
            return None
        try:
            return int(result.get("message_id"))
        except (TypeError, ValueError):
            return None


def gateway_result(
    *,
    ok: bool = False,
    method: str = "sendMessage",
    status_code: int | None = None,
    response_json: dict | None = None,
    response_text: str = "",
    error: str | None = None,
) -> FakeGatewayResult:
    return FakeGatewayResult(
        ok=ok,
        method=method,
        status_code=status_code,
        response_json=response_json,
        response_text=response_text,
        error=error,
    )


class TelegramDeliveryQueueContractTests(unittest.IsolatedAsyncioTestCase):
    async def enqueue(
        self,
        queue: InMemoryTelegramDeliveryQueue,
        key: str,
        *,
        priority: TelegramDeliveryPriority,
        destination: str | None = None,
        channel_order: TelegramOfferChannelOrder | None = None,
        method: str = "sendMessage",
    ):
        job, created = await queue.enqueue(
            dedupe_key=key,
            destination_key=destination or f"private:{key}",
            method=method,
            payload={"text": key},
            priority=priority,
            channel_order=channel_order,
        )
        self.assertTrue(created)
        return job

    async def claim(
        self,
        queue: InMemoryTelegramDeliveryQueue,
        *,
        now: datetime = NOW,
        worker: str = "w1",
    ):
        return await queue.claim_next(now=now, worker_id=worker, lease_seconds=30)

    async def test_scheduler_selects_p0_through_p3_in_strict_order(self):
        queue = InMemoryTelegramDeliveryQueue()
        await self.enqueue(queue, "p3", priority=TelegramDeliveryPriority.BULK)
        await self.enqueue(queue, "p2", priority=TelegramDeliveryPriority.PRIVATE_TRANSACTIONAL)
        await self.enqueue(
            queue,
            "p1",
            priority=TelegramDeliveryPriority.OFFER_CHANNEL,
            channel_order=TelegramOfferChannelOrder.NEW_OFFER,
        )
        await self.enqueue(queue, "p0", priority=TelegramDeliveryPriority.INTERACTIVE)

        claimed = []
        for index in range(4):
            job = await self.claim(queue, worker=f"w{index}")
            claimed.append(job.dedupe_key)

        self.assertEqual(claimed, ["p0", "p1", "p2", "p3"])

    async def test_p1_internal_order_is_terminal_partial_market_then_new(self):
        queue = InMemoryTelegramDeliveryQueue()
        for order in reversed(list(TelegramOfferChannelOrder)):
            await self.enqueue(
                queue,
                order.name,
                priority=TelegramDeliveryPriority.OFFER_CHANNEL,
                channel_order=order,
            )

        claimed = []
        for index in range(4):
            job = await self.claim(queue, worker=f"w{index}")
            claimed.append(job.channel_order)

        self.assertEqual(claimed, list(TelegramOfferChannelOrder))

    async def test_fifo_is_preserved_within_same_priority_and_subpriority(self):
        queue = InMemoryTelegramDeliveryQueue()
        for key in ("first", "second", "third"):
            await self.enqueue(
                queue,
                key,
                priority=TelegramDeliveryPriority.OFFER_CHANNEL,
                channel_order=TelegramOfferChannelOrder.NEW_OFFER,
            )

        claimed = [
            (await self.claim(queue, worker=f"w{index}")).dedupe_key
            for index in range(3)
        ]
        self.assertEqual(claimed, ["first", "second", "third"])

    async def test_429_keeps_job_pending_and_blocks_only_that_destination(self):
        queue = InMemoryTelegramDeliveryQueue()
        first = await self.enqueue(
            queue,
            "channel-first",
            priority=TelegramDeliveryPriority.OFFER_CHANNEL,
            destination="channel:offers",
            channel_order=TelegramOfferChannelOrder.NEW_OFFER,
        )
        await self.enqueue(
            queue,
            "channel-second",
            priority=TelegramDeliveryPriority.OFFER_CHANNEL,
            destination="channel:offers",
            channel_order=TelegramOfferChannelOrder.NEW_OFFER,
        )
        other = await self.enqueue(
            queue,
            "other-channel",
            priority=TelegramDeliveryPriority.OFFER_CHANNEL,
            destination="channel:other",
            channel_order=TelegramOfferChannelOrder.NEW_OFFER,
        )

        self.assertIs(await self.claim(queue), first)
        decision = await queue.resolve(
            first.id,
            gateway_result(
                status_code=429,
                response_json={"error_code": 429, "parameters": {"retry_after": 7}},
            ),
            worker_id="w1",
            now=NOW,
            retry_after_safety_seconds=0.1,
        )

        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)
        self.assertEqual(first.state, TelegramDeliveryState.PENDING)
        self.assertEqual(first.next_retry_at, NOW + timedelta(seconds=7.1))
        self.assertEqual(decision.destination_cooldown_until, first.next_retry_at)
        self.assertIs(await self.claim(queue, now=NOW + timedelta(seconds=1)), other)
        self.assertIsNone(await self.claim(queue, now=NOW + timedelta(seconds=7.09)))
        self.assertIs(await self.claim(queue, now=NOW + timedelta(seconds=7.1)), first)

    async def test_429_never_terminalizes_even_after_many_attempts(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(
            queue,
            "rate-limit",
            priority=TelegramDeliveryPriority.INTERACTIVE,
        )
        job.attempt_count = 10_000
        await self.claim(queue)

        decision = await queue.resolve(
            job.id,
            gateway_result(status_code=429, response_json={"parameters": {"retry_after": 2}}),
            worker_id="w1",
            now=NOW,
            retry_after_safety_seconds=0.2,
        )

        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)
        self.assertEqual(job.state, TelegramDeliveryState.PENDING)

    async def test_429_without_retry_after_uses_bounded_backoff(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(
            queue,
            "fallback",
            priority=TelegramDeliveryPriority.INTERACTIVE,
        )
        job.attempt_count = 4
        await self.claim(queue)

        decision = await queue.resolve(
            job.id,
            gateway_result(status_code=429),
            worker_id="w1",
            now=NOW,
            retry_after_safety_seconds=0.25,
            retry_base_seconds=1,
            retry_max_seconds=5,
        )

        self.assertEqual(decision.next_retry_at, NOW + timedelta(seconds=5.25))
        self.assertEqual(job.state, TelegramDeliveryState.PENDING)

    async def test_temporary_edit_failure_retries_without_offer_mutation(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(
            queue,
            "terminal-edit",
            priority=TelegramDeliveryPriority.OFFER_CHANNEL,
            channel_order=TelegramOfferChannelOrder.TERMINAL,
            method="editMessageText",
        )
        job.attempt_count = 2
        await self.claim(queue)

        decision = await queue.resolve(
            job.id,
            gateway_result(method="editMessageText", status_code=503),
            worker_id="w1",
            now=NOW,
            retry_after_safety_seconds=0.1,
            retry_base_seconds=1,
        )

        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)
        self.assertIsNone(decision.offer_mutation)
        self.assertEqual(job.state, TelegramDeliveryState.PENDING)
        self.assertEqual(job.next_retry_at, NOW + timedelta(seconds=4))

    async def test_send_read_timeout_is_quarantined_instead_of_blind_retry(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(
            queue,
            "ambiguous-send",
            priority=TelegramDeliveryPriority.OFFER_CHANNEL,
            method="sendMessage",
        )
        await self.claim(queue)

        decision = await queue.resolve(
            job.id,
            gateway_result(error="ReadTimeout"),
            worker_id="w1",
            now=NOW,
            retry_after_safety_seconds=0.1,
        )

        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.AMBIGUOUS)
        self.assertEqual(job.state, TelegramDeliveryState.AMBIGUOUS)
        self.assertIsNone(job.next_retry_at)
        self.assertIsNone(await self.claim(queue, now=NOW + timedelta(days=1)))

    async def test_malformed_400_is_terminal_and_actionable(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(
            queue,
            "bad-payload",
            priority=TelegramDeliveryPriority.INTERACTIVE,
        )
        await self.claim(queue)

        decision = await queue.resolve(
            job.id,
            gateway_result(
                status_code=400,
                response_text="Bad Request: can't parse entities",
            ),
            worker_id="w1",
            now=NOW,
            retry_after_safety_seconds=0.1,
        )

        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.TERMINAL_FAILED)
        self.assertEqual(job.state, TelegramDeliveryState.TERMINAL_FAILED)

    async def test_403_pauses_destination_without_discarding_job(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(
            queue,
            "forbidden",
            priority=TelegramDeliveryPriority.OFFER_CHANNEL,
            destination="channel:offers",
        )
        await self.claim(queue)

        decision = await queue.resolve(
            job.id,
            gateway_result(status_code=403, response_text="Forbidden: bot is not an administrator"),
            worker_id="w1",
            now=NOW,
            retry_after_safety_seconds=0.1,
            destination_pause_seconds=120,
        )

        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.DESTINATION_PAUSED)
        self.assertEqual(job.state, TelegramDeliveryState.PENDING)
        self.assertEqual(job.next_retry_at, NOW + timedelta(seconds=120))

    async def test_concurrent_enqueue_is_idempotent_by_dedupe_key(self):
        queue = InMemoryTelegramDeliveryQueue()

        results = await asyncio.gather(
            *(
                queue.enqueue(
                    dedupe_key="offer:42:publish",
                    destination_key="channel:offers",
                    method="sendMessage",
                    payload={"text": "offer"},
                    priority=TelegramDeliveryPriority.OFFER_CHANNEL,
                    channel_order=TelegramOfferChannelOrder.NEW_OFFER,
                )
                for _ in range(50)
            )
        )

        self.assertEqual(len({job.id for job, _created in results}), 1)
        self.assertEqual(sum(1 for _job, created in results if created), 1)
        self.assertEqual(len(queue.jobs), 1)

    async def test_dedupe_key_collision_with_different_payload_is_rejected(self):
        queue = InMemoryTelegramDeliveryQueue()
        await self.enqueue(queue, "same-key", priority=TelegramDeliveryPriority.INTERACTIVE)

        with self.assertRaises(TelegramDeliveryDedupeConflictError):
            await queue.enqueue(
                dedupe_key="same-key",
                destination_key="private:same-key",
                method="sendMessage",
                payload={"text": "different"},
                priority=TelegramDeliveryPriority.INTERACTIVE,
            )

    async def test_concurrent_claim_leases_a_job_only_once(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(
            queue,
            "claim-once",
            priority=TelegramDeliveryPriority.INTERACTIVE,
        )

        claims = await asyncio.gather(
            *(
                queue.claim_next(now=NOW, worker_id=f"worker-{index}", lease_seconds=30)
                for index in range(20)
            )
        )

        claimed = [item for item in claims if item is not None]
        self.assertEqual(claimed, [job])
        self.assertEqual(job.attempt_count, 1)

    async def test_restart_recovers_expired_lease_without_creating_duplicate(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(
            queue,
            "restart",
            priority=TelegramDeliveryPriority.INTERACTIVE,
        )
        self.assertIs(await self.claim(queue, worker="dead-worker"), job)

        self.assertEqual(
            await queue.recover_expired_leases(now=NOW + timedelta(seconds=29)),
            [],
        )
        self.assertEqual(
            await queue.recover_expired_leases(now=NOW + timedelta(seconds=30)),
            [job.id],
        )
        reclaimed = await self.claim(queue, now=NOW + timedelta(seconds=30), worker="replacement")

        self.assertIs(reclaimed, job)
        self.assertEqual(len(queue.jobs), 1)
        self.assertEqual(job.attempt_count, 2)

        stale_result = await queue.resolve(
            job.id,
            gateway_result(ok=True, response_json={"result": {"message_id": 1}}),
            worker_id="dead-worker",
            now=NOW + timedelta(seconds=31),
            retry_after_safety_seconds=0.1,
        )
        self.assertEqual(stale_result.outcome, TelegramDeliveryOutcome.STALE_LEASE)
        self.assertEqual(job.state, TelegramDeliveryState.LEASED)
        self.assertEqual(job.worker_id, "replacement")

    async def test_success_is_terminal_and_replayed_resolution_is_noop(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(
            queue,
            "success",
            priority=TelegramDeliveryPriority.INTERACTIVE,
        )
        await self.claim(queue)
        success = gateway_result(
            ok=True,
            response_json={"ok": True, "result": {"message_id": 901}},
        )

        first = await queue.resolve(
            job.id,
            success,
            worker_id="w1",
            now=NOW,
            retry_after_safety_seconds=0.1,
        )
        second = await queue.resolve(
            job.id,
            success,
            worker_id="w1",
            now=NOW,
            retry_after_safety_seconds=0.1,
        )

        self.assertEqual(first.outcome, TelegramDeliveryOutcome.SENT)
        self.assertEqual(second.outcome, TelegramDeliveryOutcome.ALREADY_RESOLVED)
        self.assertEqual(job.telegram_message_id, 901)
        self.assertEqual(job.state, TelegramDeliveryState.SENT)

    async def test_failure_injection_eventually_delivers_each_logical_job_once(self):
        queue = InMemoryTelegramDeliveryQueue()
        jobs = [
            await self.enqueue(
                queue,
                f"offer-{index}",
                priority=TelegramDeliveryPriority.OFFER_CHANNEL,
                destination=f"channel:{index}",
                channel_order=TelegramOfferChannelOrder.NEW_OFFER,
            )
            for index in range(3)
        ]

        for job, failure in zip(
            jobs,
            (
                gateway_result(
                    status_code=429,
                    response_json={"parameters": {"retry_after": 1}},
                ),
                gateway_result(status_code=502),
                gateway_result(ok=True, response_json={"result": {"message_id": 30}}),
            ),
        ):
            claimed = await self.claim(queue, worker=f"first-{job.id}")
            self.assertIs(claimed, job)
            await queue.resolve(
                job.id,
                failure,
                worker_id=f"first-{job.id}",
                now=NOW,
                retry_after_safety_seconds=0.1,
            )

        retry_time = NOW + timedelta(seconds=2)
        for expected_message_id in (10, 20):
            retry_job = await self.claim(
                queue,
                now=retry_time,
                worker=f"retry-{expected_message_id}",
            )
            await queue.resolve(
                retry_job.id,
                gateway_result(ok=True, response_json={"result": {"message_id": expected_message_id}}),
                worker_id=f"retry-{expected_message_id}",
                now=retry_time,
                retry_after_safety_seconds=0.1,
            )

        self.assertEqual(len(queue.jobs), 3)
        self.assertTrue(all(job.state == TelegramDeliveryState.SENT for job in jobs))
        self.assertEqual({job.telegram_message_id for job in jobs}, {10, 20, 30})

    async def test_ambiguous_send_requires_explicit_reconciliation_evidence(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(
            queue,
            "ambiguous-reconcile",
            priority=TelegramDeliveryPriority.OFFER_CHANNEL,
            method="sendMessage",
        )
        await self.claim(queue)
        await queue.resolve(
            job.id,
            gateway_result(error="ReadTimeout"),
            worker_id="w1",
            now=NOW,
            retry_after_safety_seconds=0.1,
        )

        inconclusive = reconcile_ambiguous_send(job, delivered=None, now=NOW)
        self.assertEqual(inconclusive.outcome, TelegramDeliveryOutcome.AMBIGUOUS)
        self.assertEqual(job.state, TelegramDeliveryState.AMBIGUOUS)

        confirmed = reconcile_ambiguous_send(
            job,
            delivered=True,
            now=NOW,
            telegram_message_id=777,
        )
        self.assertEqual(confirmed.outcome, TelegramDeliveryOutcome.SENT)
        self.assertEqual(job.state, TelegramDeliveryState.SENT)
        self.assertEqual(job.telegram_message_id, 777)

        absent_job = await self.enqueue(
            queue,
            "ambiguous-confirmed-absent",
            priority=TelegramDeliveryPriority.OFFER_CHANNEL,
            method="sendMessage",
        )
        await self.claim(queue, worker="absence-probe")
        await queue.resolve(
            absent_job.id,
            gateway_result(error="ReadTimeout"),
            worker_id="absence-probe",
            now=NOW,
            retry_after_safety_seconds=0.1,
        )
        absent = reconcile_ambiguous_send(
            absent_job,
            delivered=False,
            now=NOW,
            confirmed_absent_retry_delay_seconds=5,
        )
        self.assertEqual(absent.outcome, TelegramDeliveryOutcome.RETRY_PENDING)
        self.assertIsNone(await self.claim(queue, now=NOW + timedelta(seconds=4.9)))
        self.assertIs(
            await self.claim(queue, now=NOW + timedelta(seconds=5)),
            absent_job,
        )


class TelegramTerminalEditContractTests(unittest.TestCase):
    def test_terminal_marker_and_button_removal_share_one_method_call(self):
        call = build_terminal_offer_edit_call(
            chat_id=-100123,
            message_id=44,
            text="آفر منقضی شد ❌",
        )

        self.assertEqual(call.method, "editMessageText")
        self.assertEqual(
            call.payload,
            {
                "chat_id": -100123,
                "message_id": 44,
                "text": "آفر منقضی شد ❌",
                "reply_markup": {"inline_keyboard": []},
            },
        )


if __name__ == "__main__":
    unittest.main()
