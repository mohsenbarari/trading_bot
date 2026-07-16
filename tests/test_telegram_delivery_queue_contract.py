import asyncio
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.telegram_delivery_queue_contract import (
    EDIT_CATCH_UP_FRESH_COUNT,
    InMemoryAdminBroadcastFeeder,
    InMemoryFeederCoordinator,
    InMemoryOfferEditFeeder,
    InMemoryTelegramDeliveryQueue,
    TelegramDeliveryAction,
    TelegramDeliveryDedupeConflictError,
    TelegramDeliveryJob,
    TelegramDeliveryOutcome,
    TelegramDeliveryPriority,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFeederRecord,
    TelegramFeederRecordState,
    TelegramFlowExit,
    TelegramFreshnessOutcome,
    TelegramFreshnessSnapshot,
    TelegramHandoffInterrupted,
    TelegramOfferBusinessState,
    apply_freshness_decision,
    authenticated_keyboard_policy,
    build_delivery_dedupe_key,
    build_offer_success_edit_call,
    build_terminal_offer_edit_call,
    feeder_internal_rank,
    reconcile_ambiguous_send,
    revalidate_delivery,
)


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


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
        feeder: TelegramFeederKind = TelegramFeederKind.DIRECT,
        action: TelegramDeliveryAction = TelegramDeliveryAction.GENERAL_IMMEDIATE,
        destination: str | None = None,
        destination_class: TelegramDestinationClass = TelegramDestinationClass.PRIVATE,
        method: str = "sendMessage",
        version: int = 1,
        delivery_deadline_at: datetime | None = None,
        freshness_deadline_at: datetime | None = None,
        campaign_id: str | None = None,
    ):
        job, created = await queue.enqueue(
            feeder=feeder,
            source_natural_id=key,
            source_version=version,
            action=action,
            destination_key=destination or f"private:{key}",
            destination_class=destination_class,
            method=method,
            payload={"text": key},
            delivery_deadline_at=delivery_deadline_at,
            freshness_deadline_at=freshness_deadline_at,
            campaign_id=campaign_id,
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
        return await queue.claim_next(
            now=now,
            worker_id=worker,
            request_timeout_seconds=10,
            lease_seconds=25,
        )

    async def resolve(
        self,
        queue: InMemoryTelegramDeliveryQueue,
        job,
        result,
        *,
        now: datetime = NOW,
        worker: str = "w1",
        token: int | None = None,
        **kwargs,
    ):
        return await queue.resolve(
            job.id,
            result,
            worker_id=worker,
            lease_token=job.lease_token if token is None else token,
            now=now,
            retry_after_safety_seconds=kwargs.pop("retry_after_safety_seconds", 0.1),
            **kwargs,
        )

    async def test_scheduler_selects_m0_through_m7_in_strict_order(self):
        queue = InMemoryTelegramDeliveryQueue()
        specs = [
            (TelegramFeederKind.TIMED_BOT, TelegramDeliveryAction.COSMETIC_CLEANUP),
            (TelegramFeederKind.ADMIN_SYSTEM, TelegramDeliveryAction.ADMIN_BROADCAST),
            (TelegramFeederKind.ADMIN_SYSTEM, TelegramDeliveryAction.ACCOUNT_STATUS),
            (TelegramFeederKind.MARKET_STATUS, TelegramDeliveryAction.MARKET_TRANSITION),
            (TelegramFeederKind.OFFER_EDIT, TelegramDeliveryAction.TRADED_OFFER_EDIT),
            (TelegramFeederKind.OFFER_EDIT, TelegramDeliveryAction.PARTIAL_OFFER_EDIT),
            (TelegramFeederKind.OFFER_CONTROL, TelegramDeliveryAction.OFFER_SUCCESS),
            (TelegramFeederKind.OFFER_CONTROL, TelegramDeliveryAction.OFFER_PUBLISH),
        ]
        for index, (feeder, action) in enumerate(specs):
            await self.enqueue(queue, f"job-{index}", feeder=feeder, action=action)

        claimed = [(await self.claim(queue, worker=f"w-{i}")).action for i in range(8)]
        self.assertEqual(claimed, [action for _feeder, action in reversed(specs)])

    async def test_m0_tie_order_is_callback_overdue_trade_then_offer_publish(self):
        queue = InMemoryTelegramDeliveryQueue()
        publish = await self.enqueue(
            queue,
            "publish",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
        )
        trade = await self.enqueue(
            queue,
            "trade",
            feeder=TelegramFeederKind.TRADE,
            action=TelegramDeliveryAction.TRADE_RESULT,
            delivery_deadline_at=NOW,
        )
        callback = await self.enqueue(
            queue,
            "callback",
            action=TelegramDeliveryAction.CALLBACK_DEADLINE,
            delivery_deadline_at=NOW + timedelta(seconds=2),
        )

        self.assertIs(await self.claim(queue, worker="a"), callback)
        self.assertIs(await self.claim(queue, worker="b"), trade)
        self.assertIs(await self.claim(queue, worker="c"), publish)

    async def test_trade_recipient_promotes_independently_after_five_seconds(self):
        queue = InMemoryTelegramDeliveryQueue()
        first = await self.enqueue(
            queue,
            "trade:buyer",
            feeder=TelegramFeederKind.TRADE,
            action=TelegramDeliveryAction.TRADE_RESULT,
            delivery_deadline_at=NOW + timedelta(seconds=5),
        )
        second = await self.enqueue(
            queue,
            "trade:seller",
            feeder=TelegramFeederKind.TRADE,
            action=TelegramDeliveryAction.TRADE_RESULT,
            delivery_deadline_at=NOW + timedelta(seconds=5),
        )
        self.assertEqual(first.effective_priority(now=NOW), TelegramDeliveryPriority.M1)
        first.state = TelegramDeliveryState.SENT
        self.assertEqual(
            second.effective_priority(now=NOW + timedelta(seconds=5)),
            TelegramDeliveryPriority.M0,
        )
        self.assertEqual(first.state, TelegramDeliveryState.SENT)

    def test_all_six_feeder_rank_matrices_match_roadmap(self):
        matrices = {
            TelegramFeederKind.OFFER_CONTROL: [
                TelegramDeliveryAction.OFFER_PUBLISH,
                TelegramDeliveryAction.OFFER_SUCCESS,
                TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
                TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK,
                TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
                TelegramDeliveryAction.OFFER_REPEAT_RESPONSE,
                TelegramDeliveryAction.RECONCILIATION_EDIT,
            ],
            TelegramFeederKind.OFFER_EDIT: [
                TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
                TelegramDeliveryAction.TRADED_OFFER_EDIT,
                TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
                TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
                TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT,
                TelegramDeliveryAction.RECONCILIATION_EDIT,
            ],
            TelegramFeederKind.TRADE: [
                TelegramDeliveryAction.TRADE_RESULT,
                TelegramDeliveryAction.TRADE_RESPONSE,
                TelegramDeliveryAction.TRADE_ALTERNATIVE,
                TelegramDeliveryAction.TRADE_UNAVAILABLE,
                TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
                TelegramDeliveryAction.TRADE_NONCRITICAL,
            ],
            TelegramFeederKind.ADMIN_SYSTEM: [
                TelegramDeliveryAction.NEW_USER_MEMBERSHIP,
                TelegramDeliveryAction.ACCOUNT_STATUS,
                TelegramDeliveryAction.TARGETED_ADMIN_MESSAGE,
                TelegramDeliveryAction.ADMIN_BROADCAST,
                TelegramDeliveryAction.GENERAL_ANNOUNCEMENT,
                TelegramDeliveryAction.COSMETIC_CLEANUP,
            ],
            TelegramFeederKind.MARKET_STATUS: [
                TelegramDeliveryAction.MARKET_TRANSITION,
                TelegramDeliveryAction.MARKET_STATUS_CORRECTION,
                TelegramDeliveryAction.NONCRITICAL_MARKET,
            ],
            TelegramFeederKind.TIMED_BOT: [
                TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
                TelegramDeliveryAction.TIMED_SECURITY,
                TelegramDeliveryAction.DELAYED_RESTRICTION,
                TelegramDeliveryAction.TEMPORARY_CLEANUP,
                TelegramDeliveryAction.COSMETIC_CLEANUP,
            ],
        }
        for feeder, actions in matrices.items():
            with self.subTest(feeder=feeder):
                self.assertEqual(
                    [feeder_internal_rank(feeder, action) for action in actions],
                    list(range(len(actions))),
                )

    async def test_same_priority_uses_action_rank_deadline_then_foreign_sequence(self):
        queue = InMemoryTelegramDeliveryQueue()
        later = await self.enqueue(
            queue,
            "later",
            action=TelegramDeliveryAction.CALLBACK_DEADLINE,
            delivery_deadline_at=NOW + timedelta(seconds=4),
        )
        earlier = await self.enqueue(
            queue,
            "earlier",
            action=TelegramDeliveryAction.CALLBACK_DEADLINE,
            delivery_deadline_at=NOW + timedelta(seconds=2),
        )
        self.assertIs(await self.claim(queue, worker="a"), earlier)
        self.assertIs(await self.claim(queue, worker="b"), later)

    async def test_concurrent_enqueue_is_idempotent_by_full_child_identity(self):
        queue = InMemoryTelegramDeliveryQueue()
        results = await asyncio.gather(
            *(
                queue.enqueue(
                    feeder=TelegramFeederKind.OFFER_CONTROL,
                    source_natural_id="offer:42",
                    source_version=3,
                    action=TelegramDeliveryAction.OFFER_PUBLISH,
                    destination_key="channel:offers",
                    destination_class=TelegramDestinationClass.CHANNEL,
                    method="sendMessage",
                    payload={"text": "offer"},
                )
                for _ in range(50)
            )
        )
        self.assertEqual(len({job.id for job, _created in results}), 1)
        self.assertEqual(sum(created for _job, created in results), 1)

    async def test_dedupe_collision_with_different_payload_is_rejected(self):
        queue = InMemoryTelegramDeliveryQueue()
        await self.enqueue(
            queue,
            "same",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
        )
        with self.assertRaises(TelegramDeliveryDedupeConflictError):
            await queue.enqueue(
                feeder=TelegramFeederKind.OFFER_CONTROL,
                source_natural_id="same",
                source_version=1,
                action=TelegramDeliveryAction.OFFER_PUBLISH,
                destination_key="private:same",
                destination_class=TelegramDestinationClass.PRIVATE,
                method="sendMessage",
                payload={"text": "changed"},
            )

    def test_dedupe_key_separates_source_version_action_and_destination(self):
        base = dict(
            feeder=TelegramFeederKind.OFFER_CONTROL,
            source_natural_id="offer:1",
            source_version=1,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination_identity="channel:a",
        )
        keys = {
            build_delivery_dedupe_key(**base),
            build_delivery_dedupe_key(**{**base, "source_version": 2}),
            build_delivery_dedupe_key(
                **{**base, "action": TelegramDeliveryAction.OFFER_SUCCESS}
            ),
            build_delivery_dedupe_key(**{**base, "destination_identity": "channel:b"}),
        }
        self.assertEqual(len(keys), 4)

    async def test_lease_requires_request_timeout_plus_fifteen_seconds(self):
        queue = InMemoryTelegramDeliveryQueue()
        await self.enqueue(queue, "lease")
        with self.assertRaisesRegex(ValueError, "lease_must_cover"):
            await queue.claim_next(
                now=NOW,
                worker_id="w",
                request_timeout_seconds=10,
                lease_seconds=24.9,
            )

    async def test_concurrent_claim_and_fencing_reject_old_worker_result(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(queue, "claim")
        claims = await asyncio.gather(
            *(self.claim(queue, worker=f"worker-{i}") for i in range(20))
        )
        self.assertEqual([item for item in claims if item is not None], [job])
        old_worker = job.worker_id
        old_token = job.lease_token
        await queue.recover_expired_leases(now=NOW + timedelta(seconds=25))
        await self.claim(queue, now=NOW + timedelta(seconds=25), worker="replacement")
        decision = await self.resolve(
            queue,
            job,
            gateway_result(ok=True, response_json={"result": {"message_id": 1}}),
            now=NOW + timedelta(seconds=26),
            worker=old_worker,
            token=old_token,
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.STALE_LEASE)
        self.assertEqual(job.worker_id, "replacement")

    async def test_heartbeat_extends_only_current_fenced_lease(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(queue, "heartbeat")
        await self.claim(queue)
        token = job.lease_token
        self.assertTrue(
            await queue.heartbeat(
                job.id,
                worker_id="w1",
                lease_token=token,
                now=NOW + timedelta(seconds=10),
                request_timeout_seconds=10,
                lease_seconds=25,
            )
        )
        self.assertEqual(job.lease_until, NOW + timedelta(seconds=35))
        self.assertFalse(
            await queue.heartbeat(
                job.id,
                worker_id="w1",
                lease_token=token - 1,
                now=NOW,
                request_timeout_seconds=10,
                lease_seconds=25,
            )
        )

    async def test_retry_after_is_not_capped_and_first_429_allows_delayed_probe(self):
        queue = InMemoryTelegramDeliveryQueue()
        first = await self.enqueue(
            queue,
            "channel-a",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination="channel:a",
            destination_class=TelegramDestinationClass.CHANNEL,
        )
        other = await self.enqueue(
            queue,
            "channel-b",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination="channel:b",
            destination_class=TelegramDestinationClass.CHANNEL,
        )
        await self.claim(queue)
        decision = await self.resolve(
            queue,
            first,
            gateway_result(
                status_code=429,
                response_json={"parameters": {"retry_after": 200_000}},
            ),
        )
        self.assertEqual(
            first.next_retry_at,
            NOW + timedelta(seconds=200_000.1),
        )
        self.assertEqual(first.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertIsNone(await self.claim(queue, now=NOW + timedelta(seconds=0.09)))
        self.assertIs(await self.claim(queue, now=NOW + timedelta(seconds=0.1)), other)
        self.assertEqual(decision.bot_probe_not_before, NOW + timedelta(seconds=0.1))

    async def test_second_destination_429_within_window_starts_bot_cooldown(self):
        queue = InMemoryTelegramDeliveryQueue()
        first = await self.enqueue(
            queue,
            "a",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination="channel:a",
            destination_class=TelegramDestinationClass.CHANNEL,
        )
        second = await self.enqueue(
            queue,
            "b",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination="channel:b",
            destination_class=TelegramDestinationClass.CHANNEL,
        )
        await self.claim(queue)
        await self.resolve(
            queue,
            first,
            gateway_result(status_code=429, response_json={"parameters": {"retry_after": 7}}),
        )
        await self.claim(queue, now=NOW + timedelta(seconds=0.1), worker="w2")
        decision = await self.resolve(
            queue,
            second,
            gateway_result(status_code=429, response_json={"parameters": {"retry_after": 3}}),
            now=NOW + timedelta(seconds=0.1),
            worker="w2",
        )
        self.assertEqual(decision.bot_cooldown_until, NOW + timedelta(seconds=7.1))
        self.assertIsNone(await self.claim(queue, now=NOW + timedelta(seconds=7.09)))

    async def test_429_without_retry_after_uses_bounded_fallback_but_never_terminalizes(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(queue, "fallback")
        job.attempt_count = 10_000
        await self.claim(queue)
        decision = await self.resolve(
            queue,
            job,
            gateway_result(status_code=429),
            retry_base_seconds=1,
            retry_max_seconds=5,
            retry_after_safety_seconds=0.25,
        )
        self.assertEqual(decision.next_retry_at, NOW + timedelta(seconds=5.25))
        self.assertEqual(job.state, TelegramDeliveryState.PENDING_RETRY)

    async def test_success_is_terminal_and_replayed_resolution_is_noop(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(queue, "success")
        await self.claim(queue)
        result = gateway_result(
            ok=True,
            response_json={"ok": True, "result": {"message_id": 901}},
        )
        first = await self.resolve(queue, job, result)
        second = await self.resolve(queue, job, result)
        self.assertEqual(first.outcome, TelegramDeliveryOutcome.SENT)
        self.assertEqual(second.outcome, TelegramDeliveryOutcome.ALREADY_RESOLVED)
        self.assertEqual(job.telegram_message_id, 901)

    async def test_gateway_response_matrix_is_method_and_destination_aware(self):
        cases = [
            (
                "edit-noop",
                TelegramFeederKind.OFFER_EDIT,
                TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
                TelegramDestinationClass.CHANNEL,
                "editMessageText",
                gateway_result(
                    method="editMessageText",
                    status_code=400,
                    response_text="Bad Request: message is not modified",
                ),
                TelegramDeliveryOutcome.SENT_NOOP,
                TelegramDeliveryState.SENT_NOOP,
            ),
            (
                "private-403",
                TelegramFeederKind.DIRECT,
                TelegramDeliveryAction.GENERAL_IMMEDIATE,
                TelegramDestinationClass.PRIVATE,
                "sendMessage",
                gateway_result(status_code=403),
                TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE,
                TelegramDeliveryState.PERMANENT_UNDELIVERABLE,
            ),
            (
                "channel-403",
                TelegramFeederKind.OFFER_CONTROL,
                TelegramDeliveryAction.OFFER_PUBLISH,
                TelegramDestinationClass.CHANNEL,
                "sendMessage",
                gateway_result(status_code=403),
                TelegramDeliveryOutcome.DESTINATION_PAUSED,
                TelegramDeliveryState.BLOCKED_DESTINATION,
            ),
            (
                "bad-payload",
                TelegramFeederKind.DIRECT,
                TelegramDeliveryAction.GENERAL_IMMEDIATE,
                TelegramDestinationClass.PRIVATE,
                "sendMessage",
                gateway_result(status_code=400, response_text="can't parse entities"),
                TelegramDeliveryOutcome.TERMINAL_FAILED,
                TelegramDeliveryState.TERMINAL_FAILED,
            ),
            (
                "uneditable",
                TelegramFeederKind.OFFER_EDIT,
                TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
                TelegramDestinationClass.CHANNEL,
                "editMessageText",
                gateway_result(
                    method="editMessageText",
                    status_code=400,
                    response_text="message to edit not found",
                ),
                TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE,
                TelegramDeliveryState.PERMANENT_UNDELIVERABLE,
            ),
            (
                "unknown-418",
                TelegramFeederKind.DIRECT,
                TelegramDeliveryAction.GENERAL_IMMEDIATE,
                TelegramDestinationClass.PRIVATE,
                "sendMessage",
                gateway_result(status_code=418),
                TelegramDeliveryOutcome.QUARANTINED,
                TelegramDeliveryState.QUARANTINED,
            ),
            (
                "edit-503",
                TelegramFeederKind.OFFER_EDIT,
                TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
                TelegramDestinationClass.CHANNEL,
                "editMessageText",
                gateway_result(method="editMessageText", status_code=503),
                TelegramDeliveryOutcome.RETRY_PENDING,
                TelegramDeliveryState.PENDING_RETRY,
            ),
            (
                "send-503",
                TelegramFeederKind.DIRECT,
                TelegramDeliveryAction.GENERAL_IMMEDIATE,
                TelegramDestinationClass.PRIVATE,
                "sendMessage",
                gateway_result(status_code=503),
                TelegramDeliveryOutcome.AMBIGUOUS,
                TelegramDeliveryState.AMBIGUOUS,
            ),
        ]
        for index, case in enumerate(cases):
            key, feeder, action, destination_class, method, result, outcome, state = case
            with self.subTest(key=key):
                queue = InMemoryTelegramDeliveryQueue()
                job = await self.enqueue(
                    queue,
                    key,
                    feeder=feeder,
                    action=action,
                    destination_class=destination_class,
                    method=method,
                )
                await self.claim(queue, worker=f"w{index}")
                decision = await self.resolve(
                    queue,
                    job,
                    result,
                    worker=f"w{index}",
                )
                self.assertEqual(decision.outcome, outcome)
                self.assertEqual(job.state, state)

    async def test_gateway_control_plane_and_transport_matrix(self):
        cases = [
            ("unauthorized", gateway_result(status_code=401), TelegramDeliveryOutcome.BOT_PAUSED),
            ("conflict", gateway_result(status_code=409), TelegramDeliveryOutcome.BOT_PAUSED),
            (
                "missing-method",
                gateway_result(status_code=404, response_text="method endpoint not found"),
                TelegramDeliveryOutcome.GATEWAY_PAUSED,
            ),
            (
                "missing-resource",
                gateway_result(status_code=404, response_text="chat not found"),
                TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE,
            ),
            (
                "migrated-chat",
                gateway_result(
                    status_code=400,
                    response_json={"parameters": {"migrate_to_chat_id": -1002}},
                ),
                TelegramDeliveryOutcome.DESTINATION_PAUSED,
            ),
            (
                "connect-before-send",
                gateway_result(error="ConnectError"),
                TelegramDeliveryOutcome.RETRY_PENDING,
            ),
            (
                "malformed-http-success",
                gateway_result(status_code=200, response_json=None),
                TelegramDeliveryOutcome.AMBIGUOUS,
            ),
        ]
        for index, (key, result, expected) in enumerate(cases):
            with self.subTest(key=key):
                queue = InMemoryTelegramDeliveryQueue()
                job = await self.enqueue(queue, key)
                await self.claim(queue, worker=f"w{index}")
                decision = await self.resolve(
                    queue,
                    job,
                    result,
                    worker=f"w{index}",
                )
                self.assertEqual(decision.outcome, expected)

    async def test_gateway_method_mismatch_is_quarantined(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(queue, "wrong-method")
        await self.claim(queue)
        decision = await self.resolve(
            queue,
            job,
            gateway_result(
                ok=True,
                method="editMessageText",
                response_json={"result": {"message_id": 1}},
            ),
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.QUARANTINED)

    async def test_operator_resume_requires_explicit_control_plane_action(self):
        queue = InMemoryTelegramDeliveryQueue()
        channel = await self.enqueue(
            queue,
            "blocked-channel",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination="channel:offers",
            destination_class=TelegramDestinationClass.CHANNEL,
        )
        await self.claim(queue)
        await self.resolve(queue, channel, gateway_result(status_code=403))
        self.assertIsNone(await self.claim(queue, now=NOW + timedelta(days=1)))
        self.assertEqual(
            await queue.operator_resume_destination("channel:offers", now=NOW),
            [channel.id],
        )
        self.assertIs(await self.claim(queue, now=NOW, worker="resumed"), channel)

    async def test_http_200_ok_false_uses_error_envelope(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(queue, "envelope")
        await self.claim(queue)
        decision = await self.resolve(
            queue,
            job,
            gateway_result(
                status_code=200,
                response_json={
                    "ok": False,
                    "error_code": 429,
                    "parameters": {"retry_after": 2},
                },
            ),
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)
        self.assertEqual(job.state, TelegramDeliveryState.PENDING_RETRY)

    async def test_inconsistent_gateway_ok_true_cannot_override_envelope_failure(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(queue, "defensive-envelope")
        await self.claim(queue)
        decision = await self.resolve(
            queue,
            job,
            gateway_result(
                ok=True,
                status_code=200,
                response_json={
                    "ok": False,
                    "error_code": 429,
                    "parameters": {"retry_after": 2},
                },
            ),
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)
        self.assertEqual(job.state, TelegramDeliveryState.PENDING_RETRY)

    async def test_send_success_without_message_id_is_ambiguous(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(queue, "missing-result")
        await self.claim(queue)
        decision = await self.resolve(
            queue,
            job,
            gateway_result(ok=True, response_json={"ok": True, "result": {}}),
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.AMBIGUOUS)

    async def test_ambiguous_send_never_retries_without_explicit_evidence(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(queue, "ambiguous")
        await self.claim(queue)
        await self.resolve(queue, job, gateway_result(error="ReadTimeout"))

        inconclusive = reconcile_ambiguous_send(
            job,
            delivered=None,
            now=NOW + timedelta(seconds=10),
            resolution_deadline_at=NOW + timedelta(seconds=20),
        )
        self.assertEqual(inconclusive.outcome, TelegramDeliveryOutcome.AMBIGUOUS)
        unresolved = reconcile_ambiguous_send(
            job,
            delivered=None,
            now=NOW + timedelta(seconds=20),
            resolution_deadline_at=NOW + timedelta(seconds=20),
        )
        self.assertEqual(unresolved.outcome, TelegramDeliveryOutcome.AMBIGUOUS_UNRESOLVED)
        self.assertIsNone(await self.claim(queue, now=NOW + timedelta(days=1)))

        confirmed = reconcile_ambiguous_send(
            job,
            delivered=True,
            now=NOW + timedelta(seconds=30),
            telegram_message_id=777,
        )
        self.assertEqual(confirmed.outcome, TelegramDeliveryOutcome.SENT)
        self.assertEqual(job.telegram_message_id, 777)

    async def test_explicit_absence_is_the_only_path_back_to_retry(self):
        queue = InMemoryTelegramDeliveryQueue()
        job = await self.enqueue(queue, "absent")
        await self.claim(queue)
        await self.resolve(queue, job, gateway_result(error="ReadTimeout"))
        decision = reconcile_ambiguous_send(
            job,
            delivered=False,
            now=NOW,
            confirmed_absent_retry_delay_seconds=5,
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)
        self.assertEqual(job.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertIsNone(await self.claim(queue, now=NOW + timedelta(seconds=4.9)))
        self.assertIs(await self.claim(queue, now=NOW + timedelta(seconds=5)), job)


class TelegramFreshnessContractTests(unittest.TestCase):
    def make_job(
        self,
        action: TelegramDeliveryAction,
        *,
        deadline: datetime | None = None,
        freshness_deadline: datetime | None = None,
    ):
        feeder = {
            TelegramDeliveryAction.OFFER_PUBLISH: TelegramFeederKind.OFFER_CONTROL,
            TelegramDeliveryAction.PARTIAL_OFFER_EDIT: TelegramFeederKind.OFFER_EDIT,
            TelegramDeliveryAction.EXPIRED_OFFER_EDIT: TelegramFeederKind.OFFER_EDIT,
            TelegramDeliveryAction.TRADE_RESULT: TelegramFeederKind.TRADE,
            TelegramDeliveryAction.ADMIN_BROADCAST: TelegramFeederKind.ADMIN_SYSTEM,
            TelegramDeliveryAction.TEMPORARY_CLEANUP: TelegramFeederKind.TIMED_BOT,
        }.get(action, TelegramFeederKind.DIRECT)
        return TelegramDeliveryJob(
            id=1,
            dedupe_key="k",
            feeder=feeder,
            feeder_rank=feeder_internal_rank(feeder, action),
            source_natural_id="s",
            source_version=1,
            destination_key="d",
            destination_class=TelegramDestinationClass.PRIVATE,
            method="sendMessage",
            payload={},
            action=action,
            created_sequence=1,
            delivery_deadline_at=deadline,
            freshness_deadline_at=freshness_deadline,
        )

    def test_callback_after_deadline_expires_without_send(self):
        job = self.make_job(
            TelegramDeliveryAction.CALLBACK_DEADLINE,
            deadline=NOW,
        )
        decision = revalidate_delivery(job, TelegramFreshnessSnapshot(), now=NOW)
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.EXPIRED_INTERACTION)
        apply_freshness_decision(job, decision)
        self.assertEqual(job.state, TelegramDeliveryState.EXPIRED_INTERACTION)

    def test_offer_publication_revalidates_business_state_and_deadline(self):
        job = self.make_job(
            TelegramDeliveryAction.OFFER_PUBLISH,
            freshness_deadline=NOW,
        )
        for snapshot in (
            TelegramFreshnessSnapshot(),
            TelegramFreshnessSnapshot(offer_state=TelegramOfferBusinessState.EXPIRED),
        ):
            with self.subTest(snapshot=snapshot):
                self.assertEqual(
                    revalidate_delivery(job, snapshot, now=NOW).outcome,
                    TelegramFreshnessOutcome.SUPERSEDED,
                )

    def test_partial_edit_waits_for_message_then_reclassifies_terminal_state(self):
        job = self.make_job(TelegramDeliveryAction.PARTIAL_OFFER_EDIT)
        waiting = revalidate_delivery(
            job,
            TelegramFreshnessSnapshot(channel_message_exists=False),
            now=NOW,
        )
        self.assertEqual(waiting.outcome, TelegramFreshnessOutcome.WAIT_DEPENDENCY)
        terminal = revalidate_delivery(
            job,
            TelegramFreshnessSnapshot(offer_state=TelegramOfferBusinessState.TRADED),
            now=NOW,
        )
        self.assertEqual(terminal.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(terminal.replacement_action, TelegramDeliveryAction.TRADED_OFFER_EDIT)
        apply_freshness_decision(job, terminal)
        self.assertEqual(job.action, TelegramDeliveryAction.TRADED_OFFER_EDIT)

    def test_terminal_edit_without_published_message_is_successful_noop(self):
        job = self.make_job(TelegramDeliveryAction.EXPIRED_OFFER_EDIT)
        decision = revalidate_delivery(
            job,
            TelegramFreshnessSnapshot(channel_message_exists=False),
            now=NOW,
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SENT_NOOP)

    def test_immutable_trade_result_remains_sendable(self):
        job = self.make_job(TelegramDeliveryAction.TRADE_RESULT)
        decision = revalidate_delivery(
            job,
            TelegramFreshnessSnapshot(
                offer_state=TelegramOfferBusinessState.EXPIRED,
                interaction_valid=False,
                ttl_valid=False,
            ),
            now=NOW,
        )
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)

    def test_admin_ttl_and_cleanup_allowlist_prevent_stale_side_effect(self):
        admin = self.make_job(TelegramDeliveryAction.ADMIN_BROADCAST)
        cleanup = self.make_job(TelegramDeliveryAction.TEMPORARY_CLEANUP)
        self.assertEqual(
            revalidate_delivery(
                admin,
                TelegramFreshnessSnapshot(ttl_valid=False),
                now=NOW,
            ).outcome,
            TelegramFreshnessOutcome.SUPERSEDED,
        )
        self.assertEqual(
            revalidate_delivery(
                cleanup,
                TelegramFreshnessSnapshot(run_id_allowed=False),
                now=NOW,
            ).outcome,
            TelegramFreshnessOutcome.SUPERSEDED,
        )


class TelegramOfferEditFeederContractTests(unittest.TestCase):
    def enqueue(
        self,
        feeder: InMemoryOfferEditFeeder,
        key: str,
        *,
        action: TelegramDeliveryAction,
        offer_created_at: datetime,
        enqueued_at: datetime = NOW,
        version: int = 1,
        message_id: int | None = 1,
    ):
        return feeder.enqueue(
            offer_id=key,
            source_version=version,
            action=action,
            offer_created_at=offer_created_at,
            enqueued_at=enqueued_at,
            channel_message_id=message_id,
        )[0]

    def test_internal_rank_precedes_newest_first(self):
        feeder = InMemoryOfferEditFeeder()
        expired = self.enqueue(
            feeder,
            "expired-new",
            action=TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
            offer_created_at=NOW,
        )
        older_partial = self.enqueue(
            feeder,
            "partial-old",
            action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            offer_created_at=NOW - timedelta(seconds=10),
        )
        newest_partial = self.enqueue(
            feeder,
            "partial-new",
            action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            offer_created_at=NOW - timedelta(seconds=1),
        )
        self.assertIs(feeder.pop_next(now=NOW), newest_partial)
        self.assertIs(feeder.pop_next(now=NOW), older_partial)
        self.assertIs(feeder.pop_next(now=NOW), expired)

    def test_coalescing_preserves_first_age_and_reclassifies_partial_to_terminal(self):
        feeder = InMemoryOfferEditFeeder()
        first = self.enqueue(
            feeder,
            "offer",
            action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            offer_created_at=NOW,
            enqueued_at=NOW - timedelta(minutes=6),
        )
        updated, changed = feeder.enqueue(
            offer_id="offer",
            source_version=2,
            action=TelegramDeliveryAction.TRADED_OFFER_EDIT,
            offer_created_at=NOW,
            enqueued_at=NOW,
            channel_message_id=1,
        )
        self.assertTrue(changed)
        self.assertIs(updated, first)
        self.assertEqual(updated.first_enqueued_at, NOW - timedelta(minutes=6))
        self.assertEqual(updated.action, TelegramDeliveryAction.TRADED_OFFER_EDIT)
        with self.assertRaisesRegex(ValueError, "cannot_revert"):
            feeder.enqueue(
                offer_id="offer",
                source_version=3,
                action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
                offer_created_at=NOW,
                enqueued_at=NOW,
                channel_message_id=1,
            )

    def test_edit_without_channel_message_is_not_released(self):
        feeder = InMemoryOfferEditFeeder()
        self.enqueue(
            feeder,
            "waiting",
            action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            offer_created_at=NOW,
            message_id=None,
        )
        self.assertIsNone(feeder.pop_next(now=NOW))

    def test_stale_edit_gets_one_catch_up_slot_after_twenty_fresh_same_rank(self):
        feeder = InMemoryOfferEditFeeder()
        stale = self.enqueue(
            feeder,
            "stale",
            action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            offer_created_at=NOW - timedelta(minutes=10),
            enqueued_at=NOW - timedelta(minutes=6),
        )
        for index in range(EDIT_CATCH_UP_FRESH_COUNT + 1):
            self.enqueue(
                feeder,
                f"fresh-{index}",
                action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
                offer_created_at=NOW + timedelta(seconds=index),
            )
        first_twenty = [feeder.pop_next(now=NOW) for _ in range(EDIT_CATCH_UP_FRESH_COUNT)]
        self.assertTrue(all(item is not stale for item in first_twenty))
        self.assertIs(feeder.pop_next(now=NOW), stale)


class TelegramFeederHandoffContractTests(unittest.IsolatedAsyncioTestCase):
    def make_record(self):
        return TelegramFeederRecord(
            id="child:1",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            source_natural_id="offer:1",
            source_version=1,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination_key="channel:offers",
            destination_class=TelegramDestinationClass.CHANNEL,
            method="sendMessage",
            payload={"text": "offer"},
        )

    async def test_crash_after_enqueue_is_recovered_without_duplicate_main_job(self):
        queue = InMemoryTelegramDeliveryQueue()
        coordinator = InMemoryFeederCoordinator()
        record = self.make_record()
        coordinator.add(record)
        with self.assertRaises(TelegramHandoffInterrupted):
            await coordinator.handoff(record.id, queue, fail_after_enqueue=True)
        self.assertEqual(len(queue.jobs), 1)
        self.assertIsNone(record.main_job_id)

        job = await coordinator.handoff(record.id, queue)
        self.assertEqual(len(queue.jobs), 1)
        self.assertEqual(record.main_job_id, job.id)
        self.assertEqual(record.state, TelegramFeederRecordState.HANDED_OFF)

    async def test_concurrent_handoff_and_feedback_have_one_owner(self):
        queue = InMemoryTelegramDeliveryQueue()
        coordinator = InMemoryFeederCoordinator()
        record = self.make_record()
        coordinator.add(record)
        jobs = await asyncio.gather(*(coordinator.handoff(record.id, queue) for _ in range(20)))
        self.assertEqual({job.id for job in jobs}, {1})
        self.assertEqual(len(queue.jobs), 1)
        self.assertEqual(
            coordinator.reflect_main_result(record.id, jobs[0]),
            TelegramFeederRecordState.HANDED_OFF,
        )
        jobs[0].state = TelegramDeliveryState.SENT
        self.assertEqual(
            coordinator.reflect_main_result(record.id, jobs[0]),
            TelegramFeederRecordState.TERMINAL,
        )

    async def test_each_of_six_feeders_hands_off_exactly_one_main_job(self):
        queue = InMemoryTelegramDeliveryQueue()
        coordinator = InMemoryFeederCoordinator()
        representatives = [
            (TelegramFeederKind.OFFER_CONTROL, TelegramDeliveryAction.OFFER_PUBLISH),
            (TelegramFeederKind.OFFER_EDIT, TelegramDeliveryAction.PARTIAL_OFFER_EDIT),
            (TelegramFeederKind.TRADE, TelegramDeliveryAction.TRADE_RESULT),
            (TelegramFeederKind.ADMIN_SYSTEM, TelegramDeliveryAction.ADMIN_BROADCAST),
            (TelegramFeederKind.MARKET_STATUS, TelegramDeliveryAction.MARKET_TRANSITION),
            (TelegramFeederKind.TIMED_BOT, TelegramDeliveryAction.TEMPORARY_CLEANUP),
        ]
        for index, (feeder, action) in enumerate(representatives):
            record = TelegramFeederRecord(
                id=f"child:{index}",
                feeder=feeder,
                source_natural_id=f"source:{index}",
                source_version=1,
                action=action,
                destination_key=f"destination:{index}",
                destination_class=TelegramDestinationClass.CHANNEL,
                method="sendMessage",
                payload={"index": index},
            )
            coordinator.add(record)
            await coordinator.handoff(record.id, queue)
        self.assertEqual(len(queue.jobs), 6)
        self.assertEqual(
            {job.feeder for job in queue.jobs.values()},
            {feeder for feeder, _action in representatives},
        )


class TelegramAdminBroadcastFeederContractTests(unittest.TestCase):
    def test_one_in_flight_per_campaign_global_two_and_round_robin(self):
        feeder = InMemoryAdminBroadcastFeeder()
        for campaign_id in ("a", "b", "c"):
            feeder.add_campaign(campaign_id, [f"{campaign_id}-1", f"{campaign_id}-2"])
        first = feeder.release_next(now=NOW)
        second = feeder.release_next(now=NOW)
        self.assertEqual({first.campaign_id, second.campaign_id}, {"a", "b"})
        self.assertIsNone(feeder.release_next(now=NOW))

        feeder.apply_result("a", TelegramDeliveryOutcome.SENT)
        third = feeder.release_next(now=NOW + timedelta(seconds=1))
        self.assertEqual(third.campaign_id, "c")

    def test_retryable_pauses_only_its_campaign_and_systemic_pauses_all(self):
        feeder = InMemoryAdminBroadcastFeeder()
        feeder.add_campaign("a", ["a1", "a2"])
        feeder.add_campaign("b", ["b1", "b2"])
        feeder.release_next(now=NOW)
        feeder.release_next(now=NOW)
        feeder.apply_result("a", TelegramDeliveryOutcome.RETRY_PENDING)
        self.assertTrue(feeder.campaigns["a"].paused)
        self.assertFalse(feeder.campaigns["b"].paused)
        feeder.apply_result("b", TelegramDeliveryOutcome.BOT_PAUSED)
        self.assertTrue(feeder.globally_paused)
        self.assertIsNone(feeder.release_next(now=NOW + timedelta(seconds=1)))

    def test_terminal_private_failure_advances_to_next_recipient(self):
        feeder = InMemoryAdminBroadcastFeeder()
        feeder.add_campaign("a", ["blocked", "reachable"])
        first = feeder.release_next(now=NOW)
        self.assertEqual(first.recipient, "blocked")
        feeder.apply_result("a", TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE)
        second = feeder.release_next(now=NOW + timedelta(seconds=1))
        self.assertEqual(second.recipient, "reachable")


class TelegramMessageShapeContractTests(unittest.TestCase):
    def test_terminal_marker_and_button_removal_share_one_method_call(self):
        call = build_terminal_offer_edit_call(
            chat_id=-100123,
            message_id=44,
            text="آفر منقضی شد ❌",
        )
        self.assertEqual(call.method, "editMessageText")
        self.assertEqual(call.payload["reply_markup"], {"inline_keyboard": []})

    def test_offer_success_reuses_preview_in_one_edit_with_expiry_button(self):
        call = build_offer_success_edit_call(
            chat_id=123,
            message_id=44,
            text="✅ لفظ شما با موفقیت در کانال ارسال شد!\n\nلفظ شما:\n...",
            expire_callback_data="expire:offer:1",
        )
        self.assertEqual(call.method, "editMessageText")
        self.assertEqual(call.payload["message_id"], 44)
        self.assertEqual(
            call.payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
            "expire:offer:1",
        )

    def test_authenticated_keyboard_returns_on_every_temporary_flow_exit(self):
        for flow_exit in TelegramFlowExit:
            with self.subTest(flow_exit=flow_exit):
                decision = authenticated_keyboard_policy(
                    authenticated=True,
                    temporary_context_keyboard=True,
                    flow_exit=flow_exit,
                    business_inline_keyboard_stale=False,
                )
                self.assertFalse(decision.delete_active_anchor)
                self.assertFalse(decision.remove_reply_keyboard)
                self.assertTrue(decision.restore_persistent_main_menu)

    def test_stale_business_inline_keyboard_is_removed_without_reply_keyboard(self):
        decision = authenticated_keyboard_policy(
            authenticated=True,
            temporary_context_keyboard=False,
            flow_exit=TelegramFlowExit.SUCCESS,
            business_inline_keyboard_stale=True,
        )
        self.assertTrue(decision.remove_stale_business_inline_keyboard)
        self.assertFalse(decision.remove_reply_keyboard)


if __name__ == "__main__":
    unittest.main()
