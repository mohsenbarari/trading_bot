import asyncio
import unittest
from collections import Counter
from types import SimpleNamespace

from scripts.run_telegram_publisher_live_matrix import (
    MATRIX_BACKGROUND_TASKS_MAX_WAIT_SECONDS,
    MATRIX_INGRESS_INTERVAL_SECONDS,
    MatrixRun,
    OfferTimeline,
    LifecycleActionTimeline,
    _complete_lifecycle_action,
    _initial_publication_complete,
    _is_ignorable_historical_private_job,
    _retail_lot_sizes,
    _run_post_response_background_tasks,
    _run_direct_trade,
    _run_overtime_lifecycle,
    _assert_lifecycle_monitor_healthy,
    _wait_for_worker_acknowledgement,
    _timeline_terminal_follows_initial_publication,
    build_live_matrix_workload,
)


class TelegramPublisherLiveMatrixTests(unittest.TestCase):
    def test_builds_exact_source_ratio_and_interaction_mix(self):
        workload = build_live_matrix_workload(
            total_offers=1000,
            bot_offers=600,
            webapp_offers=400,
            interaction_count=10,
            ingress_interval_seconds=MATRIX_INGRESS_INTERVAL_SECONDS,
        )

        self.assertEqual(len(workload.origins), 1000)
        self.assertEqual(workload.origins.count("bot"), 600)
        self.assertEqual(workload.origins.count("webapp"), 400)
        self.assertEqual(workload.origins[:10], ("bot",) * 6 + ("webapp",) * 4)
        self.assertEqual(
            Counter(workload.scenarios),
            {
                "direct_wholesale_trade": 100,
                "direct_retail_lot_trade": 100,
                "overtime_approved_trade": 30,
                "overtime_owner_rejected": 30,
                "overtime_decision_timeout": 240,
                "manual_expiry": 100,
                "natural_expiry": 400,
            },
        )
        for start, stop in (
            (0, 100),
            (100, 200),
            (200, 230),
            (230, 260),
            (260, 500),
            (500, 600),
            (600, 1000),
        ):
            self.assertEqual(workload.origins[start:stop].count("bot"), (stop - start) * 3 // 5)
            self.assertEqual(workload.origins[start:stop].count("webapp"), (stop - start) * 2 // 5)
        self.assertEqual(workload.interaction_origins, ("bot",) * 6 + ("webapp",) * 4)
        self.assertEqual(len(workload.interaction_offsets_seconds), 10)
        self.assertEqual(
            tuple(sorted(workload.interaction_offsets_seconds)),
            workload.interaction_offsets_seconds,
        )

    def test_rejects_any_non_two_per_second_rate(self):
        with self.assertRaisesRegex(RuntimeError, "two_per_second"):
            build_live_matrix_workload(
                total_offers=1000,
                bot_offers=600,
                webapp_offers=400,
                interaction_count=10,
                ingress_interval_seconds=0.51,
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

    def test_initial_publication_requires_all_posts_before_any_unpublished_expiry(self):
        self.assertFalse(
            _initial_publication_complete(
                posted_count=999,
                expired_before_initial_publication_count=0,
            )
        )
        self.assertTrue(
            _initial_publication_complete(
                posted_count=1000,
                expired_before_initial_publication_count=0,
            )
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "expired_before_initial_publication",
        ):
            _initial_publication_complete(
                posted_count=999,
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
                posted_count=694,
                expired_before_initial_publication_count=0,
                expected_count=1000,
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


if __name__ == "__main__":
    unittest.main()
