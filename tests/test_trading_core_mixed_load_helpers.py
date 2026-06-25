import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.methods import AnswerCallbackQuery

from scripts import trading_core_probe_worker as worker


class TradingCoreMixedLoadHelperTests(unittest.TestCase):
    def test_warm_load_runner_dependencies_initializes_redis_singleton(self):
        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, *_args, **_kwargs):
                return None

        redis_client = AsyncMock()
        redis_client.ping = AsyncMock()

        async def run_probe():
            with patch.object(worker, "AsyncSessionLocal", return_value=FakeSession()), patch.object(
                worker, "init_redis", new=AsyncMock(return_value=redis_client)
            ) as init_redis_mock:
                report = await worker.warm_load_runner_dependencies(db_connections=1)
            return report, init_redis_mock

        report, init_redis_mock = asyncio.run(run_probe())

        init_redis_mock.assert_awaited_once()
        redis_client.ping.assert_awaited_once()
        self.assertTrue(report["redis_singleton_initialized"])
        self.assertEqual(report["db_connections"], 1)

    def test_build_mixed_surface_plan_preserves_ratio_and_skips_owner(self):
        users = [worker.LoadUserRef(user_id=index, telegram_id=9000 + index) for index in range(1, 12)]

        plan = worker.build_mixed_surface_plan(
            users=users,
            owner_user_id=1,
            total_requests=20,
            telegram_ratio=0.6,
        )

        self.assertEqual(len(plan), 20)
        self.assertEqual(sum(1 for item in plan if item.surface == "telegram"), 12)
        self.assertEqual(sum(1 for item in plan if item.surface == "webapp"), 8)
        self.assertNotIn(1, {item.user_id for item in plan})

    def test_cleanup_redis_for_user_ids_removes_exact_expire_rate_key(self):
        class FakeRedis:
            def __init__(self):
                self.deleted_keys = None
                self.scan_matches = []

            async def scan_iter(self, *, match):
                self.scan_matches.append(match)
                for key in {
                    "expire_rate:*": [b"expire_rate:7:legacy-window", b"expire_rate:8:other"],
                    "confirm:*": [b"confirm:7:offer", b"confirm:8:offer"],
                    "daily_expire:*": [b"daily_expire:7:1999-01-01", b"daily_expire:8:1999-01-01"],
                    "user:*": [b"user:7:profile-cache", b"user:8:profile-cache"],
                }.get(match, []):
                    yield key

            async def delete(self, *keys):
                self.deleted_keys = keys
                return len(keys)

            async def aclose(self):
                return None

        fake_client = FakeRedis()

        async def run_probe():
            with patch.object(worker.redis, "Redis", return_value=fake_client):
                dry_count = await worker.cleanup_redis_for_user_ids([7], dry_run=True)
                deleted_count = await worker.cleanup_redis_for_user_ids([7], dry_run=False)
            return dry_count, deleted_count

        dry_count, deleted_count = asyncio.run(run_probe())

        self.assertEqual(dry_count, 8)
        self.assertEqual(deleted_count, 8)
        self.assertEqual(
            fake_client.scan_matches,
            ["expire_rate:*", "confirm:*", "daily_expire:*", "user:*"] * 2,
        )
        self.assertIn("expire_rate:7", fake_client.deleted_keys)
        self.assertIn("expire_rate:7:legacy-window", fake_client.deleted_keys)
        self.assertIn("confirm:7:offer", fake_client.deleted_keys)
        self.assertIn("daily_expire:7:1999-01-01", fake_client.deleted_keys)
        self.assertIn("user:7:profile-cache", fake_client.deleted_keys)
        self.assertNotIn("confirm:8:offer", fake_client.deleted_keys)

    def test_load_surface_server_mapping_matches_deployment_policy(self):
        self.assertEqual(worker.server_for_load_surface("telegram"), worker.SERVER_FOREIGN)
        self.assertEqual(worker.server_for_load_surface("webapp"), worker.SERVER_IRAN)
        with self.assertRaises(worker.TradingProbeError):
            worker.server_for_load_surface("messenger")

    def test_load_fixture_identity_uses_stable_large_namespace(self):
        first_mobile, first_telegram = worker.load_fixture_identity_for_index(
            prefix="PFM_probe_A_",
            index=0,
            user_count=1000,
        )
        repeated_mobile, repeated_telegram = worker.load_fixture_identity_for_index(
            prefix="PFM_probe_A_",
            index=0,
            user_count=1000,
        )
        other_mobile, other_telegram = worker.load_fixture_identity_for_index(
            prefix="PFM_probe_B_",
            index=0,
            user_count=1000,
        )
        generated = [
            worker.load_fixture_identity_for_index(
                prefix="PFM_probe_A_",
                index=index,
                user_count=1000,
            )
            for index in range(1000)
        ]

        self.assertEqual((first_mobile, first_telegram), (repeated_mobile, repeated_telegram))
        self.assertNotEqual(first_mobile[:8], other_mobile[:8])
        self.assertNotEqual(first_telegram // 1000, other_telegram // 1000)
        self.assertEqual(len(first_mobile), 11)
        self.assertTrue(first_mobile.startswith("09"))
        self.assertEqual(len({mobile for mobile, _telegram in generated}), 1000)
        self.assertEqual(len({telegram for _mobile, telegram in generated}), 1000)

    def test_seed_offer_runtime_metadata_retries_stale_update(self):
        class FakeDb:
            def __init__(self):
                self.offer = SimpleNamespace(
                    channel_message_id=None,
                    created_at=None,
                    updated_at=None,
                )
                self.commit_calls = 0
                self.rollback_calls = 0
                self.get_calls = 0

            async def get(self, *args, **kwargs):
                self.get_calls += 1
                return self.offer

            async def commit(self):
                self.commit_calls += 1
                if self.commit_calls == 1:
                    raise worker.StaleDataError("stale offer version")

            async def rollback(self):
                self.rollback_calls += 1

        fake_db = FakeDb()

        async def run_probe():
            with patch.object(worker.asyncio, "sleep", AsyncMock()):
                await worker.seed_offer_runtime_metadata_with_retry(
                    fake_db,
                    offer_id=99,
                    channel_message_id=123456,
                    time_limit_buffer_minutes=60,
                )

        asyncio.run(run_probe())

        self.assertEqual(fake_db.commit_calls, 2)
        self.assertEqual(fake_db.rollback_calls, 1)
        self.assertEqual(fake_db.get_calls, 2)
        self.assertEqual(fake_db.offer.channel_message_id, 123456)
        self.assertIsNotNone(fake_db.offer.created_at)
        self.assertIsNotNone(fake_db.offer.updated_at)

    def test_hot_offer_contention_runs_surfaces_with_architecture_server_roles(self):
        users = [
            worker.LoadUserRef(user_id=1, telegram_id=9001),
            worker.LoadUserRef(user_id=2, telegram_id=9002),
            worker.LoadUserRef(user_id=3, telegram_id=9003),
        ]
        plan = [
            worker.MixedLoadAttemptSpec(index=0, surface="telegram", user_id=2, telegram_id=9002),
            worker.MixedLoadAttemptSpec(index=1, surface="webapp", user_id=3, telegram_id=9003),
        ]
        observed_servers = []

        class DummyHarness:
            async def close(self):
                return None

        async def fake_load_offer_snapshot(_offer_id):
            return SimpleNamespace(id=42, offer_public_id="ofr_42")

        async def fake_preconfirm(**_kwargs):
            observed_servers.append(("preconfirm", worker.current_server()))
            return {"attempt_count": 1}

        async def fake_bot_trade(**_kwargs):
            observed_servers.append(("telegram", worker.current_server()))
            return "rejected"

        async def fake_webapp_trade(**_kwargs):
            observed_servers.append(("webapp", worker.current_server()))
            return "rejected"

        async def fake_persistence(_offer_id):
            return worker.HotOfferPersistenceSnapshot(
                offer_id=42,
                original_quantity=5,
                remaining_quantity=5,
                offer_status="active",
                persisted_trade_count=0,
                completed_trade_quantity=0,
                completed_ledger_count=0,
                trades_without_completed_ledger_count=0,
                failed_internal_ledger_count=0,
                duplicate_replay_ledger_count=0,
            )

        async def run_probe():
            with patch.object(worker, "AiogramDispatcherHarness", DummyHarness), patch.object(
                worker, "build_mixed_surface_plan", return_value=plan
            ), patch.object(worker, "load_offer_snapshot", new=fake_load_offer_snapshot), patch.object(
                worker, "preconfirm_bot_trade_callbacks", new=fake_preconfirm
            ), patch.object(
                worker, "execute_bot_trade_with_dispatcher", new=fake_bot_trade
            ), patch.object(
                worker, "execute_webapp_trade_for_user", new=fake_webapp_trade
            ), patch.object(
                worker, "inspect_hot_offer_persistence", new=fake_persistence
            ):
                await worker.run_hot_offer_contention(
                    prefix="probe-",
                    offer_id=42,
                    owner_user_id=1,
                    users=users,
                    total_requests=2,
                    telegram_ratio=0.5,
                    target_rps=1000.0,
                    amount=5,
                    expected_winner_count=1,
                    check=False,
                )

        asyncio.run(run_probe())

        self.assertIn(("preconfirm", worker.SERVER_FOREIGN), observed_servers)
        self.assertIn(("telegram", worker.SERVER_FOREIGN), observed_servers)
        self.assertIn(("webapp", worker.SERVER_IRAN), observed_servers)

    def test_build_mixed_surface_plan_rejects_invalid_inputs(self):
        users = [worker.LoadUserRef(user_id=1, telegram_id=9001)]

        with self.assertRaises(worker.TradingProbeError):
            worker.build_mixed_surface_plan(
                users=users,
                owner_user_id=1,
                total_requests=10,
                telegram_ratio=0.6,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.build_mixed_surface_plan(
                users=[worker.LoadUserRef(user_id=1, telegram_id=9001), worker.LoadUserRef(user_id=2, telegram_id=9002)],
                owner_user_id=1,
                total_requests=0,
                telegram_ratio=0.6,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.build_mixed_surface_plan(
                users=[worker.LoadUserRef(user_id=1, telegram_id=9001), worker.LoadUserRef(user_id=2, telegram_id=9002)],
                owner_user_id=1,
                total_requests=10,
                telegram_ratio=1.0,
            )

    def test_summarize_attempt_results_reports_business_and_telegram_rps(self):
        results = [
            worker.MixedLoadAttemptResult(
                index=0,
                surface="telegram",
                status="success",
                duration_ms=10,
                telegram_update_count=1,
            ),
            worker.MixedLoadAttemptResult(
                index=1,
                surface="telegram",
                status="rejected",
                duration_ms=20,
                telegram_update_count=2,
            ),
            worker.MixedLoadAttemptResult(index=2, surface="webapp", status="rejected", duration_ms=30),
            worker.MixedLoadAttemptResult(index=3, surface="webapp", status="error", duration_ms=40),
        ]

        summary = worker.summarize_attempt_results(results, elapsed_seconds=2.0)

        self.assertEqual(summary["business_request_rps"], 2.0)
        self.assertEqual(summary["telegram_update_count"], 3)
        self.assertEqual(summary["telegram_update_rps"], 1.5)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["rejected"], 2)
        self.assertEqual(summary["error"], 1)
        self.assertEqual(summary["surfaces"]["telegram"]["total"], 2)
        self.assertEqual(summary["surfaces"]["webapp"]["total"], 2)

    def test_summarize_attempt_results_preserves_error_details(self):
        results = [
            worker.MixedLoadAttemptResult(
                index=0,
                surface="telegram",
                status="error",
                duration_ms=10,
                detail="RuntimeError: remote forward failed",
            ),
            worker.MixedLoadAttemptResult(
                index=1,
                surface="webapp",
                status="error",
                duration_ms=20,
                detail="RuntimeError: remote forward failed",
            ),
            worker.MixedLoadAttemptResult(
                index=2,
                surface="webapp",
                status="error",
                duration_ms=20,
                detail="TimeoutError: db pool exhausted",
            ),
        ]

        summary = worker.summarize_attempt_results(results, elapsed_seconds=1.0)

        self.assertEqual(
            summary["error_details"],
            [
                "RuntimeError: remote forward failed",
                "TimeoutError: db pool exhausted",
            ],
        )

    def test_patched_trading_boundaries_disables_web_push_schedulers(self):
        async def run_probe():
            import core.web_push as web_push

            original_market = web_push.schedule_market_offer_web_push
            original_notification = web_push.schedule_notification_web_push
            async with worker.patched_trading_boundaries():
                self.assertIs(web_push.schedule_market_offer_web_push, worker.noop_schedule_web_push)
                self.assertIs(web_push.schedule_notification_web_push, worker.noop_schedule_web_push)
            self.assertIs(web_push.schedule_market_offer_web_push, original_market)
            self.assertIs(web_push.schedule_notification_web_push, original_notification)

        asyncio.run(run_probe())

    def test_dual_role_worker_plans_split_distribution_and_share_barrier(self):
        users = [worker.LoadUserRef(user_id=index, telegram_id=9000 + index) for index in range(1, 12)]

        plans = worker.build_dual_role_worker_plans(
            run_id="run-1",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id="offer-public-42",
            total_requests=20,
            telegram_ratio=0.6,
            target_rps=600.0,
            amount=5,
            barrier_epoch=1234.5,
        )

        self.assertEqual(set(plans), {"telegram_foreign", "webapp_iran"})
        telegram_plan = worker.validate_role_plan_artifact(plans["telegram_foreign"])
        webapp_plan = worker.validate_role_plan_artifact(plans["webapp_iran"])
        self.assertEqual(telegram_plan["surface"], "telegram")
        self.assertEqual(webapp_plan["surface"], "webapp")
        self.assertEqual(len(telegram_plan["attempts"]), 12)
        self.assertEqual(len(webapp_plan["attempts"]), 8)
        self.assertEqual(telegram_plan["barrier_epoch"], webapp_plan["barrier_epoch"])
        self.assertEqual(worker.assert_role_plan_barrier_skew(plans, max_skew_seconds=0.001)["observed_skew_seconds"], 0.0)

    def test_dual_role_worker_plan_can_force_webapp_duplicate_replay_attempts(self):
        users = [worker.LoadUserRef(user_id=index, telegram_id=9000 + index) for index in range(1, 6)]

        plans = worker.build_dual_role_worker_plans(
            run_id="run-1b",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id="offer-public-42",
            total_requests=2,
            telegram_ratio=0.6,
            target_rps=600.0,
            amount=10,
            barrier_epoch=1234.5,
            request_surface="webapp",
            idempotency_mode="duplicate_replay",
        )

        telegram_plan = worker.validate_role_plan_artifact(plans["telegram_foreign"])
        webapp_plan = worker.validate_role_plan_artifact(plans["webapp_iran"])
        self.assertEqual(telegram_plan["attempts"], [])
        self.assertEqual(len(webapp_plan["attempts"]), 2)
        self.assertEqual(webapp_plan["attempts"][0]["user_id"], webapp_plan["attempts"][1]["user_id"])
        self.assertEqual(webapp_plan["attempts"][0]["idempotency_key"], webapp_plan["attempts"][1]["idempotency_key"])
        self.assertTrue(str(webapp_plan["attempts"][0]["idempotency_key"]).startswith("load:duplicate-re"))

    def test_dual_role_worker_plan_can_force_telegram_duplicate_replay_attempts(self):
        users = [worker.LoadUserRef(user_id=index, telegram_id=9000 + index) for index in range(1, 6)]

        plans = worker.build_dual_role_worker_plans(
            run_id="run-1c",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id="offer-public-42",
            total_requests=2,
            telegram_ratio=0.6,
            target_rps=600.0,
            amount=5,
            barrier_epoch=1234.5,
            request_surface="telegram",
            idempotency_mode="duplicate_replay",
        )

        telegram_plan = worker.validate_role_plan_artifact(plans["telegram_foreign"])
        webapp_plan = worker.validate_role_plan_artifact(plans["webapp_iran"])
        self.assertEqual(webapp_plan["attempts"], [])
        self.assertEqual(len(telegram_plan["attempts"]), 2)
        self.assertEqual(telegram_plan["attempts"][0]["user_id"], telegram_plan["attempts"][1]["user_id"])
        self.assertEqual(telegram_plan["attempts"][0]["telegram_id"], telegram_plan["attempts"][1]["telegram_id"])
        self.assertEqual(telegram_plan["attempts"][0]["idempotency_key"], telegram_plan["attempts"][1]["idempotency_key"])

    def test_role_plan_artifact_validation_fails_closed(self):
        users = [worker.LoadUserRef(user_id=1, telegram_id=9001), worker.LoadUserRef(user_id=2, telegram_id=9002)]
        plans = worker.build_dual_role_worker_plans(
            run_id="run-2",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id=None,
            total_requests=2,
            telegram_ratio=0.5,
            target_rps=10.0,
            amount=1,
            barrier_epoch=1234.5,
        )
        broken = dict(plans["telegram_foreign"])
        broken["surface"] = "webapp"

        with self.assertRaises(worker.TradingProbeError):
            worker.validate_role_plan_artifact(broken)

    def test_dry_role_results_merge_into_required_artifact_schema(self):
        users = [worker.LoadUserRef(user_id=index, telegram_id=9000 + index) for index in range(1, 12)]
        plans = worker.build_dual_role_worker_plans(
            run_id="run-3",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id="offer-public-42",
            total_requests=10,
            telegram_ratio=0.6,
            target_rps=100.0,
            amount=1,
            barrier_epoch=1234.5,
        )
        telegram_result = worker.build_dry_role_result_artifact(plans["telegram_foreign"], started_epoch=2000.0)
        webapp_result = worker.build_dry_role_result_artifact(plans["webapp_iran"], started_epoch=2000.001)

        merged = worker.merge_role_result_artifacts([telegram_result, webapp_result])

        self.assertEqual(merged["schema_version"], worker.DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION)
        self.assertEqual(merged["summary"]["total"], 10)
        self.assertEqual(merged["summary"]["surfaces"]["telegram"]["total"], 6)
        self.assertEqual(merged["summary"]["surfaces"]["webapp"]["total"], 4)
        self.assertIn("role_start_skew", merged)
        first_attempt = merged["attempts"][0]
        for key in {
            "monotonic_timestamp",
            "source_role",
            "source_surface",
            "user_id",
            "offer_public_id",
            "idempotency_key",
            "outcome",
            "latency_ms",
        }:
            self.assertIn(key, first_attempt)

    def test_role_result_merge_uses_attempt_window_not_worker_start_skew(self):
        users = [worker.LoadUserRef(user_id=index, telegram_id=9000 + index) for index in range(1, 12)]
        plans = worker.build_dual_role_worker_plans(
            run_id="run-3b",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id="offer-public-42",
            total_requests=10,
            telegram_ratio=0.6,
            target_rps=100.0,
            amount=1,
            barrier_epoch=1234.5,
        )
        telegram_result = worker.build_dry_role_result_artifact(plans["telegram_foreign"], started_epoch=2000.0)
        webapp_result = worker.build_dry_role_result_artifact(plans["webapp_iran"], started_epoch=2100.0)

        for offset, attempt in enumerate(telegram_result["attempts"]):
            attempt["monotonic_timestamp"] = 5000.0 + offset * 0.01
            attempt["latency_ms"] = 10.0
        for offset, attempt in enumerate(webapp_result["attempts"]):
            attempt["monotonic_timestamp"] = 5000.005 + offset * 0.01
            attempt["latency_ms"] = 10.0

        merged = worker.merge_role_result_artifacts([telegram_result, webapp_result])

        self.assertLess(merged["summary"]["elapsed_seconds"], 0.1)
        self.assertGreater(merged["summary"]["business_request_rps"], 100.0)
        self.assertGreater(merged["summary"]["attempt_start_rps"], 100.0)
        self.assertEqual(merged["role_start_skew"]["observed_skew_seconds"], 100.0)

    def test_role_result_merge_reports_attempt_start_rps_separately_from_ack_window(self):
        users = [worker.LoadUserRef(user_id=index, telegram_id=9000 + index) for index in range(1, 12)]
        plans = worker.build_dual_role_worker_plans(
            run_id="run-3c",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id="offer-public-42",
            total_requests=10,
            telegram_ratio=0.6,
            target_rps=100.0,
            amount=1,
            barrier_epoch=1234.5,
        )
        telegram_result = worker.build_dry_role_result_artifact(plans["telegram_foreign"], started_epoch=2000.0)
        webapp_result = worker.build_dry_role_result_artifact(plans["webapp_iran"], started_epoch=2000.0)

        for offset, attempt in enumerate(telegram_result["attempts"]):
            attempt["monotonic_timestamp"] = 5000.0 + offset * 0.01
            attempt["latency_ms"] = 1000.0
        for offset, attempt in enumerate(webapp_result["attempts"]):
            attempt["monotonic_timestamp"] = 5000.005 + offset * 0.01
            attempt["latency_ms"] = 1000.0

        merged = worker.merge_role_result_artifacts([telegram_result, webapp_result])

        self.assertLess(merged["summary"]["business_request_rps"], 20.0)
        self.assertGreater(merged["summary"]["attempt_start_rps"], 100.0)
        self.assertEqual(merged["summary"]["attempt_start_elapsed_seconds"], 0.05)

    def test_role_result_merge_rejects_missing_required_attempt_fields(self):
        users = [worker.LoadUserRef(user_id=1, telegram_id=9001), worker.LoadUserRef(user_id=2, telegram_id=9002)]
        plans = worker.build_dual_role_worker_plans(
            run_id="run-4",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id="offer-public-42",
            total_requests=2,
            telegram_ratio=0.5,
            target_rps=10.0,
            amount=1,
            barrier_epoch=1234.5,
        )
        result = worker.build_dry_role_result_artifact(plans["telegram_foreign"], started_epoch=2000.0)
        del result["attempts"][0]["idempotency_key"]

        with self.assertRaises(worker.TradingProbeError):
            worker.merge_role_result_artifacts([result])

    def test_role_attempt_idempotency_key_stays_within_trade_column_limit(self):
        first = worker.build_role_attempt_idempotency_key(
            prefix="P7_STAGE_TRADE_NONCONC_DIAG_20260621_091252_CLM-004_" * 2,
            role="telegram_foreign",
            offer_id=123456789,
            attempt_index=9999,
        )
        second = worker.build_role_attempt_idempotency_key(
            prefix="P7_STAGE_TRADE_NONCONC_DIAG_20260621_091252_CLM-004_" * 2,
            role="telegram_foreign",
            offer_id=123456789,
            attempt_index=10000,
        )

        self.assertLessEqual(len(first), 64)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("load:telegram-for:123456789:9999:"))

    def test_hot_offer_scenario_specs_cover_step_11b3_matrix(self):
        scenarios = worker.build_hot_offer_scenario_specs(
            total_requests=1000,
            telegram_ratio=0.6,
            target_rps=600.0,
            price=100000,
            offer_type="sell",
        )

        names = {scenario.name for scenario in scenarios}
        self.assertEqual(
            names,
            {
                "webapp_full_fill",
                "bot_full_fill",
                "webapp_partial_fill",
                "bot_partial_fill",
                "webapp_retail_lot",
                "bot_retail_lot",
            },
        )
        self.assertEqual({scenario.origin for scenario in scenarios}, {"webapp", "bot"})
        self.assertTrue(any(scenario.expected_winner_count == 1 for scenario in scenarios))
        self.assertTrue(any(scenario.expected_winner_count > 1 for scenario in scenarios))
        self.assertTrue(any(not scenario.is_wholesale and scenario.lot_sizes for scenario in scenarios))
        for scenario in scenarios:
            self.assertEqual(scenario.expected_completed_quantity, scenario.quantity)
            self.assertGreaterEqual(scenario.start_burst_request_count, 36)
            if scenario.expected_winner_count > 1:
                self.assertFalse(scenario.is_wholesale)
                self.assertEqual(sum(scenario.lot_sizes), scenario.quantity)

    def test_hot_offer_scenario_specs_reject_weak_contention(self):
        with self.assertRaises(worker.TradingProbeError):
            worker.build_hot_offer_scenario_specs(
                total_requests=20,
                telegram_ratio=0.6,
                target_rps=600.0,
                price=100000,
                offer_type="sell",
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.build_hot_offer_scenario_specs(
                total_requests=1000,
                telegram_ratio=0.6,
                target_rps=100.0,
                price=100000,
                offer_type="sell",
            )

    def test_hot_offer_acceptance_fails_closed_on_data_corruption(self):
        worker.assert_hot_offer_contention_acceptance(
            persisted_trade_count=1,
            response_success_count=1,
            error_count=0,
            remaining_quantity=0,
            status="completed",
            expected_winner_count=1,
        )

        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=2,
                response_success_count=2,
                error_count=0,
                remaining_quantity=-1,
                status="completed",
                expected_winner_count=1,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=1,
                response_success_count=1,
                error_count=1,
                remaining_quantity=0,
                status="completed",
                expected_winner_count=1,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=1,
                response_success_count=1,
                error_count=0,
                remaining_quantity=0,
                status="active",
                expected_winner_count=1,
            )

    def test_hot_offer_acceptance_validates_quantities_and_ledger(self):
        worker.assert_hot_offer_contention_acceptance(
            persisted_trade_count=4,
            response_success_count=4,
            error_count=0,
            remaining_quantity=0,
            status="completed",
            expected_winner_count=4,
            original_quantity=20,
            completed_trade_quantity=20,
            completed_ledger_count=4,
        )

        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=4,
                response_success_count=4,
                error_count=0,
                remaining_quantity=0,
                status="completed",
                expected_winner_count=4,
                original_quantity=20,
                completed_trade_quantity=25,
                completed_ledger_count=4,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=4,
                response_success_count=4,
                error_count=0,
                remaining_quantity=0,
                status="completed",
                expected_winner_count=4,
                original_quantity=20,
                completed_trade_quantity=20,
                completed_ledger_count=3,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=4,
                response_success_count=4,
                error_count=0,
                remaining_quantity=0,
                status="completed",
                expected_winner_count=4,
                original_quantity=20,
                completed_trade_quantity=20,
                completed_ledger_count=4,
                failed_internal_ledger_count=1,
            )

    def test_duplicate_replay_acceptance_allows_successful_replay_without_duplicate_trade(self):
        snapshot = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=5,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=1,
            completed_trade_quantity=5,
            completed_ledger_count=1,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        worker.assert_duplicate_replay_acceptance(statuses=["success", "success"], persistence=snapshot)
        worker.assert_duplicate_replay_acceptance(statuses=["success", "rejected"], persistence=snapshot)

        corrupted = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=5,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=2,
            completed_trade_quantity=10,
            completed_ledger_count=2,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_duplicate_replay_acceptance(statuses=["success", "success"], persistence=corrupted)

    def test_cleanup_prefix_guard_rejects_empty_broad_and_wildcard_prefixes(self):
        self.assertEqual(worker.validate_cleanup_prefix("P7_TRADING_1405_"), "P7_TRADING_1405_")

        for value in ("", "abc", "test", "prod", "stage", "bad%prefix", "bad*prefix", "bad?prefix"):
            with self.subTest(value=value):
                with self.assertRaises(worker.TradingProbeError):
                    worker.validate_cleanup_prefix(value)

    def test_cleanup_prefix_patterns_escape_sql_like_wildcards(self):
        prefix_pattern, contains_pattern = worker.cleanup_prefix_patterns("P7_TRADING_1405_")

        self.assertEqual(prefix_pattern, r"P7\_TRADING\_1405\_%")
        self.assertEqual(contains_pattern, r"%P7\_TRADING\_1405\_%")

    def test_cleanup_in_batches_splits_large_id_lists(self):
        batches = list(worker.cleanup_in_batches(list(range(12001)), batch_size=5000))

        self.assertEqual([len(batch) for batch in batches], [5000, 5000, 2001])
        self.assertEqual(batches[0][0], 0)
        self.assertEqual(batches[-1][-1], 12000)

    def test_stable_unique_preserves_cleanup_id_order(self):
        self.assertEqual(worker.stable_unique([3, 1, 3, 2, 1]), [3, 1, 2])

    def test_cleanup_dry_run_report_lists_request_and_publication_scope(self):
        plan = worker.CleanupPlan(
            prefix="P7_TRADING_1405_",
            user_ids=[1, 2],
            invitation_ids=[],
            accountant_relation_ids=[],
            customer_relation_ids=[],
            user_session_ids=[],
            session_login_request_ids=[],
            recovery_request_ids=[],
            recovery_admin_target_ids=[],
            telegram_link_token_ids=[],
            push_subscription_ids=[],
            offer_ids=[10],
            offer_public_ids=["ofr_10"],
            trade_ids=[20],
            trade_delivery_receipt_ids=[],
            offer_request_ids=[30, 31],
            publication_state_ids=[40, 41],
            notification_ids=[50],
            chat_member_ids=[60],
        )

        report = worker.cleanup_report_payload(plan=plan, dry_run=True, deleted_redis_keys=3)

        self.assertTrue(report["dry_run"])
        self.assertEqual(report["planned_counts"]["offer_requests"], 2)
        self.assertEqual(report["planned_counts"]["offer_publication_states"], 2)
        self.assertEqual(report["planned_ids"]["offer_public_ids"], ["ofr_10"])
        self.assertEqual(report["deleted_offer_requests"], 0)
        self.assertEqual(report["deleted_publication_states"], 0)
        self.assertEqual(report["deleted_redis_keys"], 3)

    def test_cleanup_mutating_statement_marks_sync_cleanup_and_blocks_production(self):
        with patch.object(worker.settings, "environment", "staging"):
            statement = worker.cleanup_mutating_statement(worker.delete(worker.Offer).where(worker.Offer.id == 1))

        self.assertTrue(statement.get_execution_options()["is_sync"])
        with patch.object(worker.settings, "environment", "production"):
            with self.assertRaises(worker.TradingProbeError):
                worker.cleanup_mutating_statement(worker.delete(worker.Offer).where(worker.Offer.id == 1))

    def test_cleanup_deletes_late_chat_members_by_user_id_before_users(self):
        source = inspect.getsource(worker.delete_cleanup_plan)

        user_lock = "await lock_cleanup_users(db, plan.user_ids)"
        late_chat_member_delete = "delete_in_batches(db, ChatMember, ChatMember.user_id, plan.user_ids)"
        user_delete = "delete_in_batches(db, User, User.id, plan.user_ids)"
        self.assertIn(user_lock, source)
        self.assertIn(late_chat_member_delete, source)
        self.assertLess(source.index(user_lock), source.index(late_chat_member_delete))
        self.assertLess(source.index(late_chat_member_delete), source.index(user_delete))

    def test_cleanup_retries_late_chat_member_fk_violation(self):
        exc = worker.DBAPIError(
            "DELETE FROM users",
            {},
            Exception("ForeignKeyViolationError fk_chat_members_user"),
        )

        self.assertTrue(worker.is_retryable_cleanup_database_error(exc))

    def test_load_runner_runtime_surface_guard_accepts_expected_roles(self):
        with patch.object(worker.settings, "environment", "staging"), patch.object(
            worker.settings, "trading_bot_service", "load_runner"
        ), patch.object(worker.settings, "server_mode", "foreign"), patch.object(worker.settings, "bot_token", ""):
            payload = worker.assert_load_runner_runtime_surface("telegram_foreign")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["surface"], "telegram")
        self.assertEqual(payload["server_mode"], "foreign")

        with patch.object(worker.settings, "environment", "staging"), patch.object(
            worker.settings, "trading_bot_service", "load_runner"
        ), patch.object(worker.settings, "server_mode", "iran"), patch.object(worker.settings, "bot_token", None):
            payload = worker.assert_load_runner_runtime_surface("webapp_iran")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["surface"], "webapp")
        self.assertEqual(payload["server_mode"], "iran")

    def test_load_runner_runtime_surface_guard_fails_closed(self):
        with patch.object(worker.settings, "environment", "production"), patch.object(
            worker.settings, "trading_bot_service", "app"
        ), patch.object(worker.settings, "server_mode", "iran"), patch.object(worker.settings, "bot_token", "token"):
            with self.assertRaises(worker.TradingProbeError) as exc_info:
                worker.assert_load_runner_runtime_surface("telegram_foreign")

        message = str(exc_info.exception)
        self.assertIn("ENVIRONMENT must be staging", message)
        self.assertIn("TRADING_BOT_SERVICE must be load_runner", message)
        self.assertIn("SERVER_MODE must be foreign", message)
        self.assertIn("BOT_TOKEN must be empty", message)

    def test_load_runner_runtime_surface_guard_allows_confirmed_production_runner(self):
        with patch.dict(
            "os.environ",
            {worker.PRODUCTION_ROLE_WORKER_CONFIRM_ENV: worker.PRODUCTION_ROLE_WORKER_CONFIRM_VALUE},
            clear=True,
        ), patch.object(worker.settings, "environment", "production"), patch.object(
            worker.settings, "trading_bot_service", "load_runner"
        ), patch.object(worker.settings, "server_mode", "foreign"), patch.object(worker.settings, "bot_token", ""):
            payload = worker.assert_load_runner_runtime_surface(
                "telegram_foreign",
                allow_production=True,
                prefix="PFM_20260624_180000_",
            )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["environment"], "production")
        self.assertTrue(payload["production_execution_allowed"])

    def test_dual_role_prepare_runtime_requires_offer_home_server(self):
        with patch.object(worker.settings, "environment", "staging"), patch.object(
            worker.settings, "server_mode", "foreign"
        ):
            payload = worker.assert_dual_role_prepare_runtime(
                "bot",
                allow_production=False,
                prefix="PFM_20260624_180000_",
            )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["server_mode"], "foreign")

        with patch.object(worker.settings, "environment", "staging"), patch.object(
            worker.settings, "server_mode", "foreign"
        ):
            with self.assertRaises(worker.TradingProbeError) as exc_info:
                worker.assert_dual_role_prepare_runtime(
                    "webapp",
                    allow_production=False,
                    prefix="PFM_20260624_180000_",
                )

        self.assertIn("SERVER_MODE must be iran", str(exc_info.exception))

    def test_role_plan_parser_accepts_external_side_effect_patch(self):
        args = worker.build_parser().parse_args(
            [
                "run-role-plan",
                "--plan",
                "/tmp/role.plan.json",
                "--patch-external-side-effects",
            ]
        )

        self.assertTrue(args.patch_external_side_effects)
        self.assertFalse(args.patch_boundaries)
        self.assertFalse(args.allow_production_execution)

    def test_role_worker_patch_options_reject_ambiguous_combination(self):
        async def run_probe():
            async with worker.optional_role_worker_patches(
                patch_boundaries=True,
                patch_external_side_effects=True,
            ):
                return None

        with self.assertRaises(worker.TradingProbeError) as exc_info:
            asyncio.run(run_probe())

        self.assertIn("cannot be used together", str(exc_info.exception))

    def test_external_side_effect_patch_preserves_cross_server_forwarders(self):
        original_trade_forward = worker.trades_router.forward_trade_to_home_server
        original_bot_trade_forward = worker.bot_trade_execute.forward_trade_to_home_server
        original_offer_expiry_forward = worker.offers_router.forward_offer_expiry_to_home_server
        original_bot_expiry_forward = worker.bot_trade_manage.forward_offer_expiry_to_home_server

        async def run_probe():
            async with worker.patched_external_side_effects():
                self.assertIs(worker.trades_router.forward_trade_to_home_server, original_trade_forward)
                self.assertIs(worker.bot_trade_execute.forward_trade_to_home_server, original_bot_trade_forward)
                self.assertIs(worker.offers_router.forward_offer_expiry_to_home_server, original_offer_expiry_forward)
                self.assertIs(worker.bot_trade_manage.forward_offer_expiry_to_home_server, original_bot_expiry_forward)

        asyncio.run(run_probe())
        self.assertIs(worker.trades_router.forward_trade_to_home_server, original_trade_forward)
        self.assertIs(worker.bot_trade_execute.forward_trade_to_home_server, original_bot_trade_forward)
        self.assertIs(worker.offers_router.forward_offer_expiry_to_home_server, original_offer_expiry_forward)
        self.assertIs(worker.bot_trade_manage.forward_offer_expiry_to_home_server, original_bot_expiry_forward)

    def test_aiogram_dispatcher_harness_can_be_recreated(self):
        async def _build_twice():
            first = worker.AiogramDispatcherHarness()
            await first.close()
            second = worker.AiogramDispatcherHarness()
            await second.close()

        asyncio.run(_build_twice())

    def test_dual_role_final_report_accepts_consistent_hot_offer(self):
        prepare = {
            "run_id": "run-5",
            "prefix": "probe-",
            "topology": "single-db staging role-worker smoke",
            "telegram_gateway_boundary": "mock",
            "scenario": {
                "name": "webapp_hot_offer",
                "expected_winner_count": 1,
            },
            "offer": {
                "id": 42,
                "owner_user_id": 7,
            },
        }
        merged_result = {
            "schema_version": worker.DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION,
            "summary": {
                "total": 10,
                "success": 1,
                "rejected": 9,
                "error": 0,
                "business_request_rps": 20.0,
                "telegram_update_rps": 24.0,
                "latency": {},
                "surfaces": {},
            },
            "roles": {},
            "role_start_skew": {},
            "attempts": [],
        }
        persistence = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=5,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=1,
            completed_trade_quantity=5,
            completed_ledger_count=1,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        report = worker.build_dual_role_final_report(
            prepare=prepare,
            merged_result=merged_result,
            persistence=persistence,
        )

        self.assertEqual(report["schema_version"], worker.DUAL_ROLE_FINAL_SCHEMA_VERSION)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["correctness_failures"], [])
        self.assertEqual(report["reports"]["webapp_hot_offer"]["persisted_trade_count"], 1)

    def test_dual_role_final_report_accepts_partial_duplicate_replay_persistence(self):
        prepare = {
            "run_id": "run-5b",
            "prefix": "probe-",
            "topology": "single-db staging role-worker smoke",
            "telegram_gateway_boundary": "mock",
            "scenario": {
                "name": "duplicate_idempotency_replay",
                "expected_winner_count": 1,
                "expected_remaining_quantity": 10,
                "require_terminal_completed": False,
                "idempotency_mode": "duplicate_replay",
            },
            "offer": {
                "id": 42,
                "owner_user_id": 7,
            },
        }
        merged_result = {
            "schema_version": worker.DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION,
            "summary": {
                "total": 2,
                "success": 2,
                "rejected": 0,
                "error": 0,
                "business_request_rps": 20.0,
                "telegram_update_rps": 0.0,
                "latency": {},
                "surfaces": {},
            },
            "roles": {},
            "role_start_skew": {},
            "attempts": [
                {"outcome": "success"},
                {"outcome": "success"},
            ],
        }
        persistence = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=20,
            remaining_quantity=10,
            offer_status="active",
            persisted_trade_count=1,
            completed_trade_quantity=10,
            completed_ledger_count=1,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        report = worker.build_dual_role_final_report(
            prepare=prepare,
            merged_result=merged_result,
            persistence=persistence,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["correctness_failures"], [])
        self.assertEqual(report["reports"]["duplicate_idempotency_replay"]["offer_remaining_quantity"], 10)

    def test_dual_role_final_report_accepts_manual_expiry_race_when_expiry_wins(self):
        prepare = {
            "run_id": "run-5c",
            "prefix": "probe-",
            "topology": "single-db staging role-worker smoke",
            "telegram_gateway_boundary": "mock",
            "scenario": {
                "name": "manual_expire_trade_race",
                "expected_winner_count": 2,
                "idempotency_mode": "unique",
            },
            "offer": {
                "id": 42,
                "owner_user_id": 7,
            },
        }
        merged_result = {
            "schema_version": worker.DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION,
            "summary": {
                "total": 8,
                "success": 0,
                "rejected": 8,
                "error": 0,
                "business_request_rps": 20.0,
                "telegram_update_rps": 0.0,
                "latency": {},
                "surfaces": {},
            },
            "roles": {},
            "role_start_skew": {},
            "attempts": [],
        }
        persistence = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=20,
            remaining_quantity=20,
            offer_status="expired",
            persisted_trade_count=0,
            completed_trade_quantity=0,
            completed_ledger_count=0,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        report = worker.build_dual_role_final_report(
            prepare=prepare,
            merged_result=merged_result,
            persistence=persistence,
            manual_expiry_result={"schema_version": worker.MANUAL_EXPIRY_RACE_RESULT_SCHEMA_VERSION, "status": "success"},
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["correctness_failures"], [])
        self.assertEqual(
            report["reports"]["manual_expire_trade_race"]["manual_expiry_result"]["status"],
            "success",
        )

    def test_manual_expiry_race_acceptance_fails_closed_on_active_offer(self):
        snapshot = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=20,
            remaining_quantity=10,
            offer_status="active",
            persisted_trade_count=1,
            completed_trade_quantity=10,
            completed_ledger_count=1,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        with self.assertRaises(worker.TradingProbeError):
            worker.assert_manual_expiry_trade_race_acceptance(
                response_success_count=1,
                error_count=0,
                persistence=snapshot,
                expected_winner_count=2,
                manual_expiry_result={"status": "rejected"},
            )

    def test_dual_role_final_report_accepts_time_expiry_race_with_in_flight_completion(self):
        prepare = {
            "run_id": "run-5d",
            "prefix": "probe-",
            "topology": "single-db staging role-worker smoke",
            "telegram_gateway_boundary": "mock",
            "scenario": {
                "name": "time_expire_trade_race",
                "expected_winner_count": 1,
                "idempotency_mode": "unique",
            },
            "offer": {
                "id": 42,
                "owner_user_id": 7,
            },
        }
        merged_result = {
            "schema_version": worker.DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION,
            "summary": {
                "total": 8,
                "success": 1,
                "rejected": 7,
                "error": 0,
                "business_request_rps": 20.0,
                "telegram_update_rps": 0.0,
                "latency": {},
                "surfaces": {},
            },
            "roles": {},
            "role_start_skew": {},
            "attempts": [],
        }
        persistence = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=20,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=1,
            completed_trade_quantity=20,
            completed_ledger_count=1,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        report = worker.build_dual_role_final_report(
            prepare=prepare,
            merged_result=merged_result,
            persistence=persistence,
            time_expiry_result={"schema_version": worker.TIME_EXPIRY_RACE_RESULT_SCHEMA_VERSION, "status": "success"},
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["correctness_failures"], [])
        self.assertEqual(
            report["reports"]["time_expire_trade_race"]["time_expiry_result"]["status"],
            "success",
        )

    def test_time_expiry_race_acceptance_fails_closed_on_overtrade(self):
        snapshot = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=20,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=2,
            completed_trade_quantity=25,
            completed_ledger_count=2,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        with self.assertRaises(worker.TradingProbeError):
            worker.assert_time_expiry_trade_race_acceptance(
                response_success_count=2,
                error_count=0,
                persistence=snapshot,
                expected_winner_count=2,
                time_expiry_result={"status": "success"},
            )

    def test_dual_role_final_report_accepts_read_during_write_results(self):
        prepare = {
            "run_id": "run-5e",
            "prefix": "probe-",
            "topology": "single-db staging role-worker smoke",
            "telegram_gateway_boundary": "mock",
            "scenario": {
                "name": "read_during_write",
                "expected_winner_count": 1,
                "hot_offer_requests": 12,
                "idempotency_mode": "unique",
            },
            "offer": {
                "id": 42,
                "owner_user_id": 7,
            },
        }
        merged_result = {
            "schema_version": worker.DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION,
            "summary": {
                "total": 12,
                "success": 1,
                "rejected": 11,
                "error": 0,
                "business_request_rps": 20.0,
                "telegram_update_rps": 0.0,
                "latency": {},
                "surfaces": {},
            },
            "roles": {},
            "role_start_skew": {},
            "attempts": [],
        }
        persistence = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=20,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=1,
            completed_trade_quantity=20,
            completed_ledger_count=1,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )
        read_results = [
            {
                "schema_version": worker.READ_DURING_WRITE_RESULT_SCHEMA_VERSION,
                "status": "ok",
                "read_surface": "telegram",
                "summary": {
                    "total": 12,
                    "success": 12,
                    "error": 0,
                    "operation_counts": {"telegram_market_view": 12},
                },
            },
            {
                "schema_version": worker.READ_DURING_WRITE_RESULT_SCHEMA_VERSION,
                "status": "ok",
                "read_surface": "webapp",
                "summary": {
                    "total": 12,
                    "success": 12,
                    "error": 0,
                    "operation_counts": {
                        "active_offers": 4,
                        "public_detail": 4,
                        "market_history": 4,
                    },
                },
            },
        ]

        report = worker.build_dual_role_final_report(
            prepare=prepare,
            merged_result=merged_result,
            persistence=persistence,
            read_during_write_results=read_results,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["correctness_failures"], [])
        self.assertEqual(
            len(report["reports"]["read_during_write"]["read_during_write_results"]),
            2,
        )

    def test_read_during_write_acceptance_fails_closed_on_missing_read_surface(self):
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_read_during_write_acceptance(
                read_results=[
                    {
                        "status": "ok",
                        "read_surface": "webapp",
                        "summary": {
                            "total": 12,
                            "error": 0,
                            "operation_counts": {
                                "active_offers": 4,
                                "public_detail": 4,
                                "market_history": 4,
                            },
                        },
                    }
                ],
                expected_read_count=12,
            )

    def test_dual_role_final_report_fails_closed_on_request_errors(self):
        prepare = {
            "run_id": "run-6",
            "prefix": "probe-",
            "topology": "single-db staging role-worker smoke",
            "telegram_gateway_boundary": "mock",
            "scenario": {
                "name": "bot_hot_offer",
                "expected_winner_count": 1,
            },
            "offer": {
                "id": 42,
                "owner_user_id": 7,
            },
        }
        merged_result = {
            "schema_version": worker.DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION,
            "summary": {
                "total": 10,
                "success": 1,
                "rejected": 8,
                "error": 1,
                "business_request_rps": 20.0,
                "telegram_update_rps": 24.0,
                "latency": {},
                "surfaces": {},
            },
            "roles": {},
            "role_start_skew": {},
            "attempts": [
                {
                    "outcome": "error",
                    "detail": "Pool timeout",
                }
            ],
        }
        persistence = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=5,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=1,
            completed_trade_quantity=5,
            completed_ledger_count=1,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        report = worker.build_dual_role_final_report(
            prepare=prepare,
            merged_result=merged_result,
            persistence=persistence,
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn("expected zero internal errors", report["correctness_failures"][0])
        self.assertEqual(report["reports"]["bot_hot_offer"]["attempt_error_details"], {"Pool timeout": 1})

    def test_recording_bot_handles_bound_callback_answer_without_telegram_network(self):
        async def run_probe():
            recorder = worker.RecordingTelegramBot()
            try:
                result = await recorder.bot(
                    AnswerCallbackQuery(
                        callback_query_id="callback-1",
                        text="ok",
                        show_alert=False,
                    )
                )
            finally:
                await recorder.close()
            return result, recorder.callback_answers

        result, callback_answers = asyncio.run(run_probe())

        self.assertTrue(result)
        self.assertEqual(callback_answers["callback-1"]["text"], "ok")

    def test_negative_guard_evidence_acceptance_for_own_offer_reject(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 1,
            "offer_request_status_counts": {
                worker.OfferRequestStatus.REJECTED_BUSINESS_RULE.value: 1,
            },
            "offer_request_public_failure_code_counts": {"own_offer": 1},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="own_offer_request",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_negative_guard_evidence_acceptance_for_pre_ledger_reject(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="watch_role_market_action",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_negative_guard_evidence_acceptance_for_market_closed_reject(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="market_closed",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_negative_guard_evidence_acceptance_for_inactive_offer_owner_reject(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 1,
            "offer_request_status_counts": {
                worker.OfferRequestStatus.REJECTED_BUSINESS_RULE.value: 1,
            },
            "offer_request_public_failure_code_counts": {"offer_owner_inactive": 1},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="inactive_offer_owner",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_negative_guard_evidence_acceptance_for_tier2_offer_creation_reject(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
            "offer_creation": {
                "before_offer_count": 0,
                "after_offer_count": 0,
                "created_offer_delta": 0,
            },
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="tier2_offer_creation",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_negative_guard_evidence_detects_tier2_offer_creation_mutation(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
            "offer_creation": {
                "before_offer_count": 0,
                "after_offer_count": 1,
                "created_offer_delta": 1,
            },
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="tier2_offer_creation",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertTrue(any("expected created_offer_delta=0" in failure for failure in failures))

    def test_negative_guard_evidence_acceptance_for_tier2_telegram_request_reject(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
            "bot_callback": {
                "second_answer_text": "⚠️ دسترسی این حساب به ربات تلگرام فعال نیست.",
                "second_answer_alert": True,
                "telegram_update_count": 2,
            },
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="tier2_telegram_request",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_negative_guard_evidence_detects_missing_tier2_telegram_denial_answer(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
            "bot_callback": {},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="tier2_telegram_request",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertTrue(any("expected a bot callback denial answer" in failure for failure in failures))

    def test_negative_guard_evidence_acceptance_for_limit_pre_ledger_rejects(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="daily_trade_limit_exceeded",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_negative_guard_evidence_acceptance_for_offer_creation_limit_rejects(self):
        for case_id in ("daily_request_limit_exceeded", "active_commodity_limit_exceeded"):
            with self.subTest(case_id=case_id):
                evidence = {
                    "offer": {"remaining_quantity": 5},
                    "trade_count": 0,
                    "offer_request_count": 0,
                    "offer_request_status_counts": {},
                    "offer_request_public_failure_code_counts": {},
                    "offer_creation": {
                        "before_offer_count": 0,
                        "after_offer_count": 0,
                        "created_offer_delta": 0,
                    },
                }

                failures = worker.assert_negative_guard_evidence(
                    case_id=case_id,
                    status_sequence=["rejected"],
                    evidence=evidence,
                )

                self.assertEqual(failures, [])

    def test_negative_guard_evidence_acceptance_for_remote_authority_unavailable_reject(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
            "remote_forward": {"status_code": 503, "detail": "سرور مرجع معامله در دسترس نیست."},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="remote_authority_unavailable",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_negative_guard_evidence_detects_remote_authority_unavailable_without_forward_failure(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
            "remote_forward": {"status_code": 200},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="remote_authority_unavailable",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertTrue(any("expected remote forward status >=500" in failure for failure in failures))

    def test_negative_guard_evidence_acceptance_for_internal_endpoint_rejects(self):
        for case_id, status_code in (
            ("bad_internal_signature", 401),
            ("wrong_authoritative_server", 409),
            ("missing_public_offer_id", 404),
        ):
            with self.subTest(case_id=case_id):
                evidence = {
                    "offer": {"remaining_quantity": 5},
                    "trade_count": 0,
                    "offer_request_count": 0,
                    "offer_request_status_counts": {},
                    "offer_request_public_failure_code_counts": {},
                    "internal_execute": {"status_code": status_code},
                }

                failures = worker.assert_negative_guard_evidence(
                    case_id=case_id,
                    status_sequence=["rejected"],
                    evidence=evidence,
                )

                self.assertEqual(failures, [])

    def test_negative_guard_evidence_detects_wrong_internal_endpoint_status(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
            "internal_execute": {"status_code": 200},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="bad_internal_signature",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertTrue(any("expected internal execute status 401" in failure for failure in failures))

    def test_negative_guard_evidence_acceptance_for_stale_telegram_button_reject(self):
        evidence = {
            "offer": {"remaining_quantity": 0},
            "trade_count": 1,
            "offer_request_count": 1,
            "offer_request_status_counts": {
                worker.OfferRequestStatus.COMPLETED_TRADE.value: 1,
            },
            "offer_request_public_failure_code_counts": {"none": 1},
            "bot_callback": {
                "second_answer_text": "این لفظ دیگر فعال نیست.",
                "second_answer_alert": True,
                "telegram_update_count": 2,
            },
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="stale_telegram_button",
            status_sequence=["success", "rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_negative_guard_evidence_acceptance_for_cleanup_scope_violation_reject(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 0,
            "offer_request_status_counts": {},
            "offer_request_public_failure_code_counts": {},
            "cleanup_scope_guard": {
                "rejected_values": [{"check": "cleanup_prefix", "value": "prod", "reason": "too broad"}],
                "accepted_values": [],
            },
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="cleanup_scope_violation",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_unsupported_policy_evidence_acceptance_for_tier2_offer_creation_reject(self):
        failures = worker.assert_unsupported_policy_evidence(
            expected_reasons={"tier2_cannot_create_offer"},
            probe_results=[
                {
                    "reason": "tier2_cannot_create_offer",
                    "status": "rejected",
                    "created_offer_delta": 0,
                }
            ],
        )

        self.assertEqual(failures, [])

    def test_unsupported_policy_evidence_acceptance_for_tier2_telegram_request_reject(self):
        failures = worker.assert_unsupported_policy_evidence(
            expected_reasons={"tier2_cannot_use_telegram_request"},
            probe_results=[
                {
                    "reason": "tier2_cannot_use_telegram_request",
                    "status": "rejected",
                    "second_answer_text": "⚠️ دسترسی این حساب به ربات تلگرام فعال نیست.",
                    "expected_remaining_quantity": 5,
                    "offer_evidence": {
                        "offer": {"remaining_quantity": 5},
                        "trade_count": 0,
                        "offer_request_count": 0,
                    },
                }
            ],
        )

        self.assertEqual(failures, [])

    def test_unsupported_policy_evidence_detects_partial_trade_mutation(self):
        failures = worker.assert_unsupported_policy_evidence(
            expected_reasons={"tier2_cannot_use_telegram_request"},
            probe_results=[
                {
                    "reason": "tier2_cannot_use_telegram_request",
                    "status": "rejected",
                    "second_answer_text": "denied",
                    "expected_remaining_quantity": 5,
                    "offer_evidence": {
                        "offer": {"remaining_quantity": 0},
                        "trade_count": 1,
                        "offer_request_count": 1,
                    },
                }
            ],
        )

        self.assertTrue(any("expected trade_count=0" in failure for failure in failures))
        self.assertTrue(any("expected offer_request_count=0" in failure for failure in failures))

    def test_negative_guard_evidence_acceptance_for_expired_offer_reject(self):
        evidence = {
            "offer": {"remaining_quantity": 5},
            "trade_count": 0,
            "offer_request_count": 1,
            "offer_request_status_counts": {
                worker.OfferRequestStatus.REJECTED_OFFER_EXPIRED.value: 1,
            },
            "offer_request_public_failure_code_counts": {"offer_not_active": 1},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="manually_expired_offer",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertEqual(failures, [])

    def test_negative_guard_evidence_detects_partial_mutation(self):
        evidence = {
            "offer": {"remaining_quantity": 0},
            "trade_count": 1,
            "offer_request_count": 2,
            "offer_request_status_counts": {
                worker.OfferRequestStatus.REJECTED_BUSINESS_RULE.value: 1,
                worker.OfferRequestStatus.COMPLETED_TRADE.value: 1,
            },
            "offer_request_public_failure_code_counts": {"invalid_quantity": 1},
        }

        failures = worker.assert_negative_guard_evidence(
            case_id="invalid_request_amount",
            status_sequence=["rejected"],
            evidence=evidence,
        )

        self.assertTrue(any("expected trade_count=0" in failure for failure in failures))
        self.assertTrue(any("unexpectedly has completed offer_request" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
