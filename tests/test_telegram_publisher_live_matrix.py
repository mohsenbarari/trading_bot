import asyncio
import unittest
from collections import Counter
from types import SimpleNamespace

from scripts.run_telegram_publisher_live_matrix import (
    MATRIX_INGRESS_INTERVAL_SECONDS,
    LifecycleActionTimeline,
    _complete_lifecycle_action,
    _initial_publication_complete,
    _is_ignorable_historical_private_job,
    _retail_lot_sizes,
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

    def test_initial_publication_requires_all_posts_before_any_expiry(self):
        self.assertFalse(
            _initial_publication_complete(posted_count=999, expired_count=0)
        )
        self.assertTrue(
            _initial_publication_complete(posted_count=1000, expired_count=0)
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "expired_before_initial_publication",
        ):
            _initial_publication_complete(posted_count=999, expired_count=1)

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


if __name__ == "__main__":
    unittest.main()
