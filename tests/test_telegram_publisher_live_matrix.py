import asyncio
import unittest
from collections import Counter
from types import SimpleNamespace

from fastapi import HTTPException

from scripts.run_telegram_publisher_live_matrix import (
    MATRIX_BACKGROUND_TASKS_MAX_WAIT_SECONDS,
    MATRIX_INGRESS_MAX_INTERVAL_SECONDS,
    MATRIX_INGRESS_MIN_INTERVAL_SECONDS,
    MATRIX_OVERTIME_RECEIPT_SAFETY_SECONDS,
    _overtime_scheduled_at,
    MatrixRun,
    RetryableBotCallbackReceiptAbsent,
    OfferTimeline,
    LifecycleActionTimeline,
    PrivateMessageSimulationTimeline,
    _complete_lifecycle_action,
    _initial_publication_complete,
    _is_ignorable_historical_private_job,
    _retail_lot_sizes,
    _run_post_response_background_tasks,
    _run_direct_trade,
    _run_manual_expiry,
    _run_overtime_lifecycle,
    _run_overtime_schedule,
    _simulate_private_telegram_send,
    _report_payload,
    _assert_lifecycle_monitor_healthy,
    _raise_if_background_task_failed,
    _wait_for_worker_acknowledgement,
    _timeline_terminal_follows_initial_publication,
    _terminal_projection_verification_passed,
    build_live_matrix_workload,
)


class TelegramPublisherLiveMatrixTests(unittest.TestCase):
    def test_terminal_projection_verification_requires_every_durable_stage(self):
        import scripts.run_telegram_publisher_live_matrix as matrix

        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )
        for index in range(1, 501):
            status = "completed" if index <= 115 else "expired"
            run.timelines.append(
                OfferTimeline(
                    index=index,
                    origin="bot",
                    scenario="reconstructed",
                    expected_terminal_status=status,
                    scheduled_at="2026-08-12T00:00:00+00:00",
                    offer_status=status,
                    central_queue_entered_at="2026-08-12T00:00:00+00:00",
                    worker_acknowledged_at="2026-08-12T00:00:00+00:00",
                    channel_post_state="sent",
                    terminal_edit_state="sent",
                    webapp_terminal_status=status,
                    publisher_lane=matrix.TELEGRAM_PUBLISHER_IDENTITIES[
                        (index - 1) % len(matrix.TELEGRAM_PUBLISHER_IDENTITIES)
                    ],
                )
            )

        self.assertTrue(_terminal_projection_verification_passed(run))
        run.timelines[-1].worker_acknowledged_at = None
        self.assertFalse(_terminal_projection_verification_passed(run))

    def test_terminal_webapp_observation_is_bounded(self):
        import scripts.run_telegram_publisher_live_matrix as matrix

        active = 0
        maximum_active = 0
        observed = 0

        async def fake_observe(timeline, *, terminal=False):
            nonlocal active, maximum_active, observed
            self.assertTrue(terminal)
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0)
            timeline.webapp_terminal_visible_at = "2026-08-12T00:00:00+00:00"
            timeline.webapp_terminal_status = timeline.expected_terminal_status
            observed += 1
            active -= 1

        timelines = [
            OfferTimeline(
                index=index,
                origin="bot",
                scenario="natural_expiry",
                expected_terminal_status="expired",
                scheduled_at="2026-08-12T00:00:00+00:00",
            )
            for index in range(1, 51)
        ]
        original_limit = matrix.MATRIX_WEBAPP_TERMINAL_OBSERVATION_MAX_CONCURRENT
        original_observer = matrix._observe_webapp_visibility
        self.assertEqual(original_limit, 2)
        matrix.MATRIX_WEBAPP_TERMINAL_OBSERVATION_MAX_CONCURRENT = 3
        matrix._observe_webapp_visibility = fake_observe
        try:
            asyncio.run(matrix._observe_webapp_terminal_projections(timelines))
        finally:
            matrix.MATRIX_WEBAPP_TERMINAL_OBSERVATION_MAX_CONCURRENT = original_limit
            matrix._observe_webapp_visibility = original_observer

        self.assertEqual(observed, 50)
        self.assertLessEqual(maximum_active, 3)

    def test_builds_exact_randomized_source_ratio_and_interaction_mix(self):
        workload = build_live_matrix_workload(
            total_offers=500,
            bot_offers=300,
            webapp_offers=200,
            interaction_count=10,
            ingress_min_interval_seconds=MATRIX_INGRESS_MIN_INTERVAL_SECONDS,
            ingress_max_interval_seconds=MATRIX_INGRESS_MAX_INTERVAL_SECONDS,
            random_seed=43,
        )

        self.assertEqual(len(workload.origins), 500)
        self.assertEqual(workload.origins.count("bot"), 300)
        self.assertEqual(workload.origins.count("webapp"), 200)
        self.assertEqual(
            Counter(workload.scenarios),
            {
                "direct_wholesale_trade": 50,
                "direct_retail_lot_trade": 50,
                "overtime_approved_trade": 15,
                "overtime_owner_rejected": 15,
                "overtime_decision_timeout": 120,
                "manual_expiry": 50,
                "natural_expiry": 200,
            },
        )
        for scenario, count in Counter(workload.scenarios).items():
            origins = [
                origin
                for origin, candidate in zip(workload.origins, workload.scenarios, strict=True)
                if candidate == scenario
            ]
            self.assertEqual(len(origins), count)
            self.assertEqual(origins.count("bot"), count * 3 // 5)
            self.assertEqual(origins.count("webapp"), count * 2 // 5)
        self.assertEqual(workload.interaction_origins.count("bot"), 6)
        self.assertEqual(workload.interaction_origins.count("webapp"), 4)
        self.assertEqual(len(workload.interaction_offsets_seconds), 10)
        self.assertEqual(
            tuple(sorted(workload.interaction_offsets_seconds)),
            workload.interaction_offsets_seconds,
        )
        self.assertEqual(len(workload.management_message_offsets_seconds), 5)
        self.assertEqual(
            tuple(sorted(workload.management_message_offsets_seconds)),
            workload.management_message_offsets_seconds,
        )
        ingress_gaps = [
            current - previous
            for previous, current in zip(
                workload.ingress_offsets_seconds,
                workload.ingress_offsets_seconds[1:],
            )
        ]
        self.assertEqual(len(ingress_gaps), 499)
        self.assertTrue(
            all(
                MATRIX_INGRESS_MIN_INTERVAL_SECONDS <= gap <= MATRIX_INGRESS_MAX_INTERVAL_SECONDS
                for gap in ingress_gaps
            )
        )
        self.assertEqual(workload.random_seed, 43)
        self.assertEqual(
            Counter(event.scenario for event in workload.active_lifecycle_events),
            {
                "direct_wholesale_trade": 50,
                "direct_retail_lot_trade": 50,
                "manual_expiry": 50,
            },
        )
        self.assertEqual(
            tuple(event.scheduled_offset_seconds for event in workload.active_lifecycle_events),
            tuple(sorted(event.scheduled_offset_seconds for event in workload.active_lifecycle_events)),
        )

    def test_rejects_any_non_approved_random_ingress_range(self):
        with self.assertRaisesRegex(RuntimeError, "random_0_8_to_4_seconds"):
            build_live_matrix_workload(
                total_offers=500,
                bot_offers=300,
                webapp_offers=200,
                interaction_count=10,
                ingress_min_interval_seconds=0.8,
                ingress_max_interval_seconds=3.99,
                random_seed=43,
            )

    def test_ignores_only_unclaimable_legacy_private_repeat_jobs(self):
        allowed = SimpleNamespace(
            state="ambiguous_unresolved",
            action_kind="offer_repeat_response",
            destination_class="private",
            destination_key="private:user:123",
        )
        self.assertTrue(_is_ignorable_historical_private_job(allowed))

        for changed in (
            {"state": "pending_retry"},
            {"action_kind": "trade_response"},
            {"destination_class": "channel"},
            {"destination_key": "admin:123"},
        ):
            payload = {
                "state": "ambiguous_unresolved",
                "action_kind": "offer_repeat_response",
                "destination_class": "private",
                "destination_key": "private:user:123",
            }
            payload.update(changed)
            self.assertFalse(
                _is_ignorable_historical_private_job(SimpleNamespace(**payload))
            )

    def test_retail_lots_always_respect_the_active_minimum(self):
        self.assertEqual(_retail_lot_sizes(5), (5, 5, 5))
        self.assertEqual(_retail_lot_sizes("7"), (7, 7, 7))
        self.assertEqual(_retail_lot_sizes(None), (1, 1, 1))

    def test_private_message_simulation_rejects_invalid_payloads_without_network(self):
        asyncio.run(
            _simulate_private_telegram_send(
                telegram_id=1,
                text="پیام آزمایشی",
            )
        )
        with self.assertRaisesRegex(RuntimeError, "private_message_payload_invalid"):
            asyncio.run(
                _simulate_private_telegram_send(
                    telegram_id=0,
                    text="",
                )
            )

    def test_private_message_audit_is_aggregate_and_redacted(self):
        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )
        run.private_message_simulations.append(
            PrivateMessageSimulationTimeline(
                kind="management",
                campaign_index=1,
                scheduled_at="2026-08-12T00:00:00+00:00",
                recipient_count=10,
                message_count=10,
                status="success",
            )
        )

        payload = _report_payload(run)

        simulation = payload["summary"]["private_message_simulation"]
        self.assertEqual(simulation["transport"], "in_process_fake_private_transport")
        self.assertEqual(simulation["message_count"], 10)
        self.assertNotIn("telegram_id", str(payload))

    def test_initial_publication_requires_all_posts_before_any_unpublished_expiry(self):
        self.assertFalse(
            _initial_publication_complete(
                posted_count=499,
                expired_before_initial_publication_count=0,
            )
        )
        self.assertTrue(
            _initial_publication_complete(
                posted_count=500,
                expired_before_initial_publication_count=0,
            )
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "expired_before_initial_publication",
        ):
            _initial_publication_complete(
                posted_count=499,
                expired_before_initial_publication_count=1,
            )

    def test_initial_publication_gate_accepts_a_complete_lifecycle_cohort(self):
        self.assertTrue(
            _initial_publication_complete(
                posted_count=200,
                expired_before_initial_publication_count=0,
                expected_count=200,
            )
        )
        self.assertTrue(
            _initial_publication_complete(
                posted_count=200,
                expired_before_initial_publication_count=1,
                expected_count=200,
            )
        )

    def test_initial_publication_gate_allows_published_manual_expiry_while_others_wait(self):
        self.assertFalse(
            _initial_publication_complete(
                posted_count=349,
                expired_before_initial_publication_count=0,
                expected_count=500,
            )
        )

    def test_initial_publication_gate_rejects_terminal_state_without_post_evidence(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "expired_before_initial_publication",
        ):
            _initial_publication_complete(
                posted_count=199,
                expired_before_initial_publication_count=1,
                expected_count=200,
            )

    def test_terminal_audit_requires_initial_post_to_precede_terminal_state(self):
        timeline = OfferTimeline(
            index=1,
            origin="bot",
            scenario="manual_expiry",
            expected_terminal_status="expired",
            scheduled_at="2026-08-12T00:00:00+00:00",
            channel_posted_at="2026-08-12T00:01:00+00:00",
            terminal_at="2026-08-12T00:02:00+00:00",
        )
        self.assertTrue(_timeline_terminal_follows_initial_publication(timeline))
        timeline.terminal_at = "2026-08-12T00:00:59+00:00"
        self.assertFalse(_timeline_terminal_follows_initial_publication(timeline))

    def test_worker_acknowledgement_requires_the_full_matrix(self):
        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )
        with self.assertRaisesRegex(RuntimeError, "worker_ack_offer_count_invalid"):
            asyncio.run(_wait_for_worker_acknowledgement(run))

    def test_lifecycle_action_records_only_the_failure_class(self):
        entry = LifecycleActionTimeline(
            offer_index=1,
            action="direct_wholesale_trade",
            origin="bot",
            scheduled_at="2026-08-12T00:00:00+00:00",
        )

        async def fail() -> None:
            raise ValueError("internal diagnostic must not be persisted")

        with self.assertRaisesRegex(RuntimeError, "lifecycle_action_failed"):
            asyncio.run(_complete_lifecycle_action(entry, fail))

        self.assertEqual(entry.status, "ValueError")
        self.assertEqual(entry.failure_class, "ValueError")
        self.assertIsNone(entry.failure_status_code)
        self.assertEqual(entry.attempt_count, 1)

    def test_lifecycle_action_records_only_http_status_code_not_detail(self):
        entry = LifecycleActionTimeline(
            offer_index=1,
            action="manual_expiry",
            origin="webapp",
            scheduled_at="2026-08-12T00:00:00+00:00",
        )

        async def fail() -> None:
            raise HTTPException(status_code=403, detail="private diagnostic")

        with self.assertRaisesRegex(RuntimeError, "lifecycle_action_failed"):
            asyncio.run(_complete_lifecycle_action(entry, fail))

        self.assertEqual(entry.status, "HTTPException")
        self.assertEqual(entry.failure_class, "HTTPException")
        self.assertEqual(entry.failure_status_code, 403)
        self.assertEqual(entry.attempt_count, 1)

    def test_lifecycle_action_retries_a_bounded_webapp_timeout(self):
        entry = LifecycleActionTimeline(
            offer_index=1,
            action="overtime_request",
            origin="webapp",
            scheduled_at="2026-08-12T00:00:00+00:00",
        )
        calls = 0

        async def eventually_succeeds() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.Future()
            return "success"

        asyncio.run(
            _complete_lifecycle_action(
                entry,
                eventually_succeeds,
                timeout_seconds=0.001,
                retry_attempts=1,
                retry_delay_seconds=0,
            )
        )

        self.assertEqual(calls, 2)
        self.assertEqual(entry.status, "success")
        self.assertEqual(entry.attempt_count, 2)

    def test_lifecycle_action_retries_only_an_allowed_http_status(self):
        entry = LifecycleActionTimeline(
            offer_index=1,
            action="manual_expiry",
            origin="webapp",
            scheduled_at="2026-08-12T00:00:00+00:00",
        )
        calls = 0

        async def eventually_succeeds() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise HTTPException(status_code=409, detail="must not persist")
            return "success"

        asyncio.run(
            _complete_lifecycle_action(
                entry,
                eventually_succeeds,
                retry_attempts=1,
                retry_delay_seconds=0,
                retryable_status_codes=frozenset({409}),
            )
        )

        self.assertEqual(calls, 2)
        self.assertEqual(entry.status, "success")
        self.assertIsNone(entry.failure_status_code)
        self.assertEqual(entry.attempt_count, 2)

    def test_lifecycle_action_retries_a_missing_bot_callback_receipt(self):
        entry = LifecycleActionTimeline(
            offer_index=1,
            action="manual_expiry",
            origin="bot",
            scheduled_at="2026-08-12T00:00:00+00:00",
        )
        calls = 0

        async def eventually_succeeds() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RetryableBotCallbackReceiptAbsent
            return "success"

        asyncio.run(
            _complete_lifecycle_action(
                entry,
                eventually_succeeds,
                retry_attempts=1,
                retry_delay_seconds=0,
                retryable_exception_types=(RetryableBotCallbackReceiptAbsent,),
            )
        )

        self.assertEqual(calls, 2)
        self.assertEqual(entry.status, "success")
        self.assertEqual(entry.attempt_count, 2)

    def test_bot_manual_expiry_retry_uses_a_fresh_callback_identity(self):
        class FakeWorker:
            def __init__(self) -> None:
                self.prefixes: list[str] = []

            async def expire_bot_offer_with_dispatcher(self, **kwargs):
                self.prefixes.append(str(kwargs["prefix"]))
                return "rejected" if len(self.prefixes) == 1 else "success"

        worker = FakeWorker()
        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )
        timeline = OfferTimeline(
            index=1,
            origin="bot",
            scenario="manual_expiry",
            expected_terminal_status="expired",
            scheduled_at="2026-08-12T00:00:00+00:00",
            offer_id=1,
        )
        import scripts.run_telegram_publisher_live_matrix as matrix

        original_delay = matrix.MATRIX_BOT_CALLBACK_RETRY_DELAY_SECONDS
        matrix.MATRIX_BOT_CALLBACK_RETRY_DELAY_SECONDS = 0
        try:
            asyncio.run(
                _run_manual_expiry(
                    worker=worker,
                    harness=object(),
                    users=[SimpleNamespace(user_id=1)],
                    run=run,
                    timeline=timeline,
                )
            )
        finally:
            matrix.MATRIX_BOT_CALLBACK_RETRY_DELAY_SECONDS = original_delay

        self.assertEqual(len(worker.prefixes), 2)
        self.assertNotEqual(*worker.prefixes)
        self.assertEqual(run.lifecycle_actions[0].attempt_count, 2)

    def test_background_task_failure_stops_ingress(self):
        async def fail() -> None:
            raise ValueError("private diagnostic")

        async def check() -> None:
            task = asyncio.create_task(fail())
            await asyncio.gather(task, return_exceptions=True)
            with self.assertRaisesRegex(RuntimeError, "active_lifecycle_failed"):
                _raise_if_background_task_failed(
                    task,
                    task_name="active_lifecycle",
                )

        asyncio.run(check())

    def test_lifecycle_monitor_must_not_stop_silently(self):
        async def stopped_monitor() -> None:
            return None

        async def check() -> None:
            task = asyncio.create_task(stopped_monitor())
            await task
            with self.assertRaisesRegex(RuntimeError, "monitor_stopped"):
                _assert_lifecycle_monitor_healthy(task)

        asyncio.run(check())

    def test_post_response_background_tasks_are_bounded(self):
        async def never_finishes() -> None:
            await asyncio.Future()

        import scripts.run_telegram_publisher_live_matrix as matrix

        original_timeout = matrix.MATRIX_BACKGROUND_TASKS_MAX_WAIT_SECONDS
        matrix.MATRIX_BACKGROUND_TASKS_MAX_WAIT_SECONDS = 0.001
        try:
            completed = asyncio.run(
                _run_post_response_background_tasks(never_finishes)
            )
        finally:
            matrix.MATRIX_BACKGROUND_TASKS_MAX_WAIT_SECONDS = original_timeout

        self.assertFalse(completed)
        self.assertGreater(MATRIX_BACKGROUND_TASKS_MAX_WAIT_SECONDS, 0)

    def test_foreign_overtime_decision_uses_official_presented_path(self):
        presented = []

        class FakeWorker:
            async def execute_webapp_trade_for_user(self, **_kwargs):
                return "success"

        import scripts.run_telegram_publisher_live_matrix as matrix

        timeline = OfferTimeline(
            index=4,
            origin="bot",
            scenario="overtime_approved_trade",
            expected_terminal_status="completed",
            scheduled_at="2026-08-12T00:00:00+00:00",
            offer_id=44,
            offer_public_id="ofr_44",
            offer_home_server="foreign",
            normal_deadline_at="2026-08-12T00:00:00+00:00",
        )
        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )
        users = [SimpleNamespace(user_id=index) for index in range(1, 1_001)]

        async def fake_load(**_kwargs):
            return "req_public_test"

        async def fake_present(**kwargs):
            presented.append(kwargs)

        async def fake_decide(**_kwargs):
            return "success"

        original_load = matrix._load_overtime_request_public_id
        original_present = matrix._ensure_foreign_overtime_presented
        original_decide = matrix._decide_overtime_request_via_webapp
        matrix._load_overtime_request_public_id = fake_load
        matrix._ensure_foreign_overtime_presented = fake_present
        matrix._decide_overtime_request_via_webapp = fake_decide
        try:
            asyncio.run(
                _run_overtime_lifecycle(
                    worker=FakeWorker(),
                    users=users,
                    run=run,
                    timeline=timeline,
                    wait_for_schedule=False,
                )
            )
        finally:
            matrix._load_overtime_request_public_id = original_load
            matrix._ensure_foreign_overtime_presented = original_present
            matrix._decide_overtime_request_via_webapp = original_decide

        self.assertEqual(presented, [
            {
                "offer_id": 44,
                "idempotency_key": "telegram-live-matrix-unit-overtime-0004",
                "offer_index": 4,
            }
        ])
        self.assertEqual(
            [entry.action for entry in run.lifecycle_actions],
            ["overtime_request", "overtime_owner_approve"],
        )

    def test_overtime_is_scheduled_after_the_normal_deadline(self):
        from datetime import datetime

        timeline = OfferTimeline(
            index=1,
            origin="bot",
            scenario="overtime_decision_timeout",
            expected_terminal_status="expired",
            scheduled_at="2026-08-12T00:00:00+00:00",
            normal_deadline_at="2026-08-12T00:25:00+00:00",
        )
        scheduled = _overtime_scheduled_at(timeline)
        deadline = datetime.fromisoformat(timeline.normal_deadline_at)
        self.assertGreater(scheduled, deadline)
        self.assertEqual(
            (scheduled - deadline).total_seconds(),
            MATRIX_OVERTIME_RECEIPT_SAFETY_SECONDS,
        )

    def test_overtime_requests_are_limited_before_the_direct_webapp_operation(self):
        active_operations = 0
        maximum_active_operations = 0

        class FakeWorker:
            async def execute_webapp_trade_for_user(self, **_kwargs):
                nonlocal active_operations, maximum_active_operations
                active_operations += 1
                maximum_active_operations = max(
                    maximum_active_operations,
                    active_operations,
                )
                try:
                    await asyncio.sleep(0.001)
                    return "success"
                finally:
                    active_operations -= 1

        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )
        users = [SimpleNamespace(user_id=index) for index in range(1, 1_001)]
        base = {
            "origin": "webapp",
            "scenario": "overtime_decision_timeout",
            "expected_terminal_status": "expired",
            "scheduled_at": "2026-08-12T00:00:00+00:00",
            "offer_home_server": "iran",
            "normal_deadline_at": "2026-08-12T00:00:00+00:00",
        }
        first = OfferTimeline(index=1, offer_id=1, offer_public_id="first", **base)
        second = OfferTimeline(index=2, offer_id=2, offer_public_id="second", **base)

        async def run_bounded_requests() -> None:
            semaphore = asyncio.Semaphore(1)
            await asyncio.gather(
                _run_overtime_lifecycle(
                    worker=FakeWorker(),
                    users=users,
                    run=run,
                    timeline=first,
                    operation_semaphore=semaphore,
                ),
                _run_overtime_lifecycle(
                    worker=FakeWorker(),
                    users=users,
                    run=run,
                    timeline=second,
                    operation_semaphore=semaphore,
                ),
            )

        asyncio.run(run_bounded_requests())

        self.assertEqual(maximum_active_operations, 1)
        self.assertEqual(
            [entry.status for entry in run.lifecycle_actions],
            ["success", "success"],
        )

    def test_overtime_scheduler_launches_each_deadline_without_timer_fanout(self):
        observed_schedules = []

        class FakeWorker:
            async def execute_webapp_trade_for_user(self, **_kwargs):
                return "success"

        import scripts.run_telegram_publisher_live_matrix as matrix

        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )
        users = [SimpleNamespace(user_id=index) for index in range(1, 1_001)]
        base = {
            "origin": "webapp",
            "scenario": "overtime_decision_timeout",
            "expected_terminal_status": "expired",
            "scheduled_at": "2026-08-12T00:00:00+00:00",
            "offer_home_server": "iran",
        }
        first = OfferTimeline(
            index=2,
            offer_id=2,
            offer_public_id="second",
            normal_deadline_at="2026-08-12T00:00:02+00:00",
            **base,
        )
        second = OfferTimeline(
            index=1,
            offer_id=1,
            offer_public_id="first",
            normal_deadline_at="2026-08-12T00:00:01+00:00",
            **base,
        )

        async def no_wait(target):
            observed_schedules.append(target)

        original_wait = matrix._wait_until
        matrix._wait_until = no_wait
        try:
            asyncio.run(
                _run_overtime_schedule(
                    worker=FakeWorker(),
                    users=users,
                    run=run,
                    timelines=(first, second),
                    operation_semaphore=asyncio.Semaphore(1),
                )
            )
        finally:
            matrix._wait_until = original_wait

        self.assertEqual(observed_schedules, sorted(observed_schedules))
        self.assertEqual(
            [entry.status for entry in run.lifecycle_actions],
            ["success", "success"],
        )

    def test_bot_lifecycle_uses_the_publishing_lane_for_the_callback(self):
        observed = []

        class FakeWorker:
            MixedLoadAttemptSpec = SimpleNamespace

            async def load_offer_snapshot(self, _offer_id):
                return SimpleNamespace(lot_sizes=None)

            async def execute_bot_trade_with_dispatcher(self, **kwargs):
                observed.append(kwargs["callback_bot_identity"])
                return "success"

        users = [
            SimpleNamespace(user_id=index, telegram_id=100_000 + index)
            for index in range(1, 1001)
        ]
        timeline = OfferTimeline(
            index=1,
            origin="bot",
            scenario="direct_wholesale_trade",
            expected_terminal_status="completed",
            scheduled_at="2026-08-12T00:00:00+00:00",
            offer_id=42,
            publisher_lane="publisher_3",
        )
        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )

        asyncio.run(
            _run_direct_trade(
                worker=FakeWorker(),
                harness=object(),
                users=users,
                run=run,
                timeline=timeline,
            )
        )

        self.assertEqual(observed, ["publisher_3"])
        self.assertEqual(run.lifecycle_actions[0].status, "success")

    def test_webapp_direct_trade_omits_inline_background_tasks(self):
        observed = []

        class FakeWorker:
            async def load_offer_snapshot(self, _offer_id):
                return SimpleNamespace(id=42, offer_public_id="ofr_42")

            async def execute_webapp_trade_for_user(self, **kwargs):
                observed.append(kwargs)
                return "success"

        timeline = OfferTimeline(
            index=1,
            origin="webapp",
            scenario="direct_wholesale_trade",
            expected_terminal_status="completed",
            scheduled_at="2026-08-12T00:00:00+00:00",
            offer_id=42,
            offer_public_id="ofr_42",
            offer_home_server="iran",
        )
        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )
        users = [
            SimpleNamespace(user_id=index, telegram_id=100_000 + index)
            for index in range(1, 1_001)
        ]

        asyncio.run(
            _run_direct_trade(
                worker=FakeWorker(),
                harness=object(),
                users=users,
                run=run,
                timeline=timeline,
            )
        )

        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0]["run_background_tasks"])

    def test_overtime_request_omits_inline_background_tasks(self):
        observed = []

        class FakeWorker:
            async def execute_webapp_trade_for_user(self, **kwargs):
                observed.append(kwargs)
                return "success"

        import scripts.run_telegram_publisher_live_matrix as matrix

        timeline = OfferTimeline(
            index=1,
            origin="bot",
            scenario="overtime_decision_timeout",
            expected_terminal_status="expired",
            scheduled_at="2026-08-12T00:00:00+00:00",
            offer_id=42,
            offer_public_id="ofr_42",
            offer_home_server="iran",
            normal_deadline_at="2026-08-12T00:00:00+00:00",
        )
        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )
        users = [
            SimpleNamespace(user_id=index, telegram_id=100_000 + index)
            for index in range(1, 1_001)
        ]

        async def no_wait(_target):
            return None

        original_wait = matrix._wait_until
        matrix._wait_until = no_wait
        try:
            asyncio.run(
                _run_overtime_lifecycle(
                    worker=FakeWorker(),
                    users=users,
                    run=run,
                    timeline=timeline,
                )
            )
        finally:
            matrix._wait_until = original_wait

        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0]["run_background_tasks"])


class TelegramPublisherLiveMatrixExpiryDriveTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_expiry_cycles_run_iran_then_foreign(self):
        from unittest.mock import patch

        import scripts.run_telegram_publisher_live_matrix as matrix

        called = []

        async def fake_cycle(server):
            called.append(server)
            return 0

        with patch(
            "scripts.trading_core_probe_worker.run_offer_expiry_cycle_for_server",
            fake_cycle,
        ):
            await matrix._drive_official_home_expiry_cycles()
        self.assertEqual(called, ["iran", "foreign"])

    async def test_terminal_wait_drives_official_expiry_before_snapshot(self):
        from unittest.mock import AsyncMock, patch

        import scripts.run_telegram_publisher_live_matrix as matrix

        order = []

        async def fake_drive():
            order.append("drive")

        async def fake_snapshot(_run):
            order.append("snapshot")
            return (matrix.MATRIX_TOTAL_OFFERS,) * 4

        run = MatrixRun(
            run_id="telegram-live-matrix-unit",
            started_at="2026-08-12T00:00:00+00:00",
            expected_expiry_minutes=25,
        )
        run.timelines.append(
            OfferTimeline(
                index=1,
                origin="webapp",
                scenario="natural_expiry",
                expected_terminal_status="expired",
                scheduled_at="2026-08-12T00:00:00+00:00",
                webapp_terminal_status="expired",
            )
        )
        with patch.object(matrix, "_drive_official_home_expiry_cycles", fake_drive), patch.object(
            matrix, "_terminal_progress_snapshot", fake_snapshot
        ), patch.object(
            matrix, "_hydrate_timelines", AsyncMock()
        ), patch.object(
            matrix, "_observe_webapp_terminal_projections", AsyncMock()
        ), patch.object(
            matrix, "_timeline_terminal_follows_initial_publication", return_value=True
        ), patch.object(
            matrix, "_write_audit"
        ):
            await matrix._wait_for_terminal_lifecycle(run)
        self.assertEqual(order[:3], ["snapshot", "drive", "snapshot"])


if __name__ == "__main__":
    unittest.main()
