"""Unit tests for the combined staging matrix manifest and wave scaling."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import build_staging_combined_matrix_manifest as manifest_builder
from scripts import run_staging_combined_market_queue_overtime_estimate_matrix as runner
from scripts import staging_combined_matrix_heal as heal
from scripts import staging_combined_matrix_mutating_wave as mutating_wave
from scripts import staging_combined_matrix_queue_sampler as queue_sampler
from scripts import staging_combined_matrix_wave_driver as wave_driver


class CombinedMatrixManifestTests(unittest.TestCase):
    def test_manifest_fills_mandatory_cells(self) -> None:
        manifest = manifest_builder.build_manifest(seed=20260806)
        errors = manifest_builder.validate_combined_manifest(manifest)
        self.assertEqual(errors, [])
        self.assertEqual(manifest["wave"]["event_count"], 4800)
        self.assertEqual(manifest["wave"]["valid_target"], 4000)
        self.assertEqual(manifest["wave"]["invalid_target"], 800)
        self.assertEqual(manifest["wave"]["wave_seconds"], 1800)
        self.assertTrue(manifest["wave"]["schedule_sha256"])

    def test_schedule_is_deterministic(self) -> None:
        left = manifest_builder.build_manifest(seed=20260806)
        right = manifest_builder.build_manifest(seed=20260806)
        self.assertEqual(left["wave"]["schedule_sha256"], right["wave"]["schedule_sha256"])
        self.assertEqual(len(left["wave_events"]), len(right["wave_events"]))

    def test_wave_scale_preserves_relative_surface_mix(self) -> None:
        manifest = manifest_builder.build_manifest(seed=20260806)
        budget = wave_driver.WaveBudget(
            valid_target=4000,
            invalid_target=800,
            scale=0.01,
            reduction_reason="unit-test",
        )
        selected = wave_driver.scale_events(list(manifest["wave_events"]), budget)
        self.assertEqual(budget.valid_limit, 40)
        self.assertEqual(budget.invalid_limit, 8)
        self.assertEqual(len(selected), 48)
        valid_selected = [item for item in selected if item["kind"] == "valid"]
        webapp = sum(1 for item in valid_selected if item["surface"] == "webapp")
        bot = sum(1 for item in valid_selected if item["surface"] == "bot")
        self.assertEqual(webapp, 16)
        self.assertEqual(bot, 24)
        selected_trades = [
            item for item in valid_selected if int(item["seq"]) % 100 < 40
        ]
        self.assertEqual(
            sum(
                item.get("request_surface") == "webapp"
                for item in selected_trades
            ),
            sum(
                item.get("request_surface") == "telegram"
                for item in selected_trades
            ),
        )
        selected_invalid = [
            item for item in selected if item["kind"] == "invalid"
        ]
        self.assertEqual(
            sum(item["surface"] == "webapp" for item in selected_invalid),
            3,
        )
        self.assertEqual(
            sum(item["surface"] == "bot" for item in selected_invalid),
            5,
        )
        self.assertGreater(sum(1 for item in selected if item.get("overtime_creator")), 0)
        self.assertGreater(sum(1 for item in selected if item.get("estimate_probe")), 0)

    def test_offer_surface_is_telegram_heavy(self) -> None:
        manifest = manifest_builder.build_manifest(seed=20260806)
        valid = [item for item in manifest["wave_events"] if item["kind"] == "valid"]
        webapp = sum(1 for item in valid if item["surface"] == "webapp")
        bot = sum(1 for item in valid if item["surface"] == "bot")
        self.assertEqual(webapp, 1600)
        self.assertEqual(bot, 2400)
        self.assertGreaterEqual(bot / len(valid), 0.60)
        self.assertAlmostEqual(webapp / len(valid), 0.40, places=2)

    def test_request_surface_mix_is_balanced(self) -> None:
        manifest = manifest_builder.build_manifest(seed=20260806)
        valid = [item for item in manifest["wave_events"] if item["kind"] == "valid"]
        webapp = sum(1 for item in valid if item.get("request_surface") == "webapp")
        telegram = sum(1 for item in valid if item.get("request_surface") == "telegram")
        self.assertEqual(webapp, 2000)
        self.assertEqual(telegram, 2000)
        mix = manifest["wave"]["request_surface_mix"]
        self.assertEqual(mix["webapp_share"], 0.5)
        self.assertEqual(mix["telegram_share"], 0.5)

    def test_mandatory_cells_include_all_lanes(self) -> None:
        for prefix in ("market:", "queue:", "overtime:", "estimate:"):
            self.assertTrue(
                any(cell.startswith(prefix) for cell in manifest_builder.MANDATORY_CELLS),
                msg=f"missing lane prefix {prefix}",
            )

    def test_manifest_requires_every_comprehensive_market_family(self) -> None:
        manifest = manifest_builder.build_manifest(seed=20260806)
        self.assertEqual(
            manifest_builder.COMPREHENSIVE_MARKET_SCENARIO_COUNT,
            228,
        )
        for family, expected_count in (
            manifest_builder.COMPREHENSIVE_MARKET_FAMILY_COUNTS.items()
        ):
            cell = f"market:comprehensive:family:{family}"
            self.assertIn(cell, manifest_builder.MANDATORY_CELLS)
            self.assertTrue(manifest["coverage_index"][cell])
            slot = next(
                item for item in manifest["scenarios"] if item["cell"] == cell
            )
            self.assertEqual(
                slot["detail"]["required_scenario_count"],
                expected_count,
            )

    def test_scaled_realtime_schedule_spans_full_window(self) -> None:
        manifest = manifest_builder.build_manifest(seed=20260806)
        budget = wave_driver.WaveBudget(
            valid_target=4000,
            invalid_target=800,
            scale=0.01,
            reduction_reason="unit-test",
        )
        selected = wave_driver.scale_events(list(manifest["wave_events"]), budget)
        times = [float(item["t_seconds"]) for item in selected]
        self.assertLess(min(times), 180.0)
        self.assertGreater(max(times), 1620.0)

    def test_profile_is_required_and_realtime_cannot_be_compressed(self) -> None:
        with self.assertRaises(SystemExit):
            runner.parse_args(["--mode", "plan"])
        with self.assertRaises(SystemExit):
            runner.parse_args(
                [
                    "--mode",
                    "plan",
                    "--wave-profile",
                    "realtime-30m",
                    "--wave-speed",
                    "2",
                ]
            )
        args = runner.parse_args(
            [
                "--mode",
                "plan",
                "--wave-profile",
                "burst",
                "--artifact-dir",
                str(Path("/tmp/combined-matrix-unit-plan")),
            ]
        )
        self.assertEqual(args.wave_profile, "burst")

    def test_full_wave_limits_account_for_telegram_channel_ceiling(self) -> None:
        args = SimpleNamespace(
            wave_trade_percent=40,
            wave_manual_expire_percent=20,
            wave_profile="realtime-30m",
            queue_offer_expiry_minutes=2,
            allow_temporary_queue_expiry_override=False,
            wave_immediate_actions=False,
            wave_action_delay_seconds=45.0,
            wave_publish_wait_timeout_seconds=1800.0,
            wave_action_drain_timeout_seconds=2400.0,
            wave_timeout_seconds=5400.0,
        )
        limits = runner._effective_wave_limits(args, expected_valid=4000)
        self.assertEqual(limits["channel_base_interval_seconds"], 0.9)
        self.assertEqual(limits["channel_idle_burst_capacity"], 2)
        self.assertEqual(limits["effective_offer_expiry_minutes"], 2)
        self.assertFalse(limits["temporary_expiry_override_enabled"])
        self.assertFalse(limits["fits_configured_offer_lifetime"])
        self.assertEqual(limits["effective_publish_wait_timeout_seconds"], 52.0)
        self.assertLessEqual(limits["effective_wave_timeout_seconds"], 8_000)
        self.assertGreater(
            limits["required_offer_lifecycle_seconds"],
            limits["effective_offer_expiry_minutes"] * 60,
        )

    def test_expiry_override_is_never_implicit(self) -> None:
        args = SimpleNamespace(
            wave_trade_percent=40,
            wave_manual_expire_percent=20,
            wave_profile="burst",
            queue_offer_expiry_minutes=2,
            allow_temporary_queue_expiry_override=True,
            wave_immediate_actions=False,
            wave_action_delay_seconds=15.0,
            wave_publish_wait_timeout_seconds=1800.0,
            wave_action_drain_timeout_seconds=2400.0,
            wave_timeout_seconds=5400.0,
        )
        limits = runner._effective_wave_limits(args, expected_valid=800)
        self.assertTrue(limits["temporary_expiry_override_enabled"])
        self.assertGreater(limits["effective_offer_expiry_minutes"], 2)

    def test_lane_ok_without_cell_evidence_cannot_green_coverage(self) -> None:
        manifest = manifest_builder.build_manifest(seed=20260806)
        lanes = {
            "market": {"ok": True, "scenario_results": []},
            "actor_guards": {"ok": True, "payload": {"ok": True, "cells_covered": []}},
            "queue_wave": {"ok": True, "assertions": []},
            "estimate": {"ok": True, "payload": {"ok": True, "checks": []}},
            "overtime": {"ok": True, "scenario_results": []},
        }
        with patch.object(runner, "_driver_scenarios", return_value=[]):
            report = runner.build_live_coverage_report(
                manifest=manifest,
                lanes=lanes,
                artifact_dir=Path("/tmp/combined-matrix-unit-coverage"),
            )
        self.assertFalse(report["ok"])
        self.assertEqual(
            report["evidence"]["queue:wave:valid"]["status"], "failed"
        )
        self.assertEqual(
            report["evidence"]["overtime:queue_order"]["status"], "failed"
        )

    def test_heal_refuses_broad_or_short_prefixes(self) -> None:
        for prefix in (
            "",
            "CMB_",
            "OTACC_2026",
            "CMB_RUN*",
            "CMB_RUN?",
            "CMB_RUN[1]",
            "CMB_RUN:1",
        ):
            with self.assertRaises(heal.DriverRefusal):
                heal._validate_run_prefix(prefix)
        self.assertEqual(
            heal._validate_run_prefix("CMB_20260807_RUN1"),
            "CMB_20260807_RUN1",
        )

    def test_heal_change_log_plan_is_prefix_and_record_id_scoped(self) -> None:
        predicates, params = heal._change_log_delete_plan(
            "CMB_20260808_RUN1",
            {
                "offers": [19, 19, 21],
                "chat_members": [31],
                "notifications": [],
            },
        )

        self.assertIn("data::text", predicates[0])
        self.assertEqual(params["prefix"], "CMB_20260808_RUN1")
        table_values = {
            value
            for key, value in params.items()
            if key.startswith("change_table_")
        }
        self.assertEqual(table_values, {"offers", "chat_members"})
        id_sets = {
            tuple(value)
            for key, value in params.items()
            if key.startswith("change_ids_")
        }
        self.assertEqual(id_sets, {(19, 21), (31,)})
        self.assertEqual(len(predicates), 3)

    def test_provider_timing_separates_slow_channel_edits(self) -> None:
        started = datetime(2026, 8, 7, tzinfo=timezone.utc)
        rows = [
            (
                1,
                started,
                started + timedelta(seconds=0.4),
                "sent",
                "offer_post",
                1,
                1,
                None,
                "sent",
                started,
                "sendMessage",
            ),
            (
                2,
                started,
                started + timedelta(seconds=2.5),
                "sent",
                "traded_offer_edit",
                1,
                1,
                None,
                "sent",
                started,
                "editMessageText",
                "ofr_provider_timing",
                {"_provider_latency_ms": 2250.0},
            ),
        ]
        payload = queue_sampler._provider_timing_payload(
            rows,
            slow_edit_threshold_seconds=2.0,
        )
        self.assertEqual(payload["sample_count"], 2)
        self.assertEqual(payload["edit_sample_count"], 1)
        self.assertEqual(payload["slow_edit_count"], 1)
        self.assertEqual(payload["edit_latency_seconds"]["p95"], 2.25)

    def test_queue_partition_requires_retried_jobs_to_recover(self) -> None:
        now = datetime.now(timezone.utc)
        rows = [
            (
                1,
                None,
                now,
                "sent",
                "offer_publish",
                2,
                1,
                now,
                None,
                None,
                None,
                "ofr_public",
            ),
            (
                2,
                None,
                None,
                "failed_permanent",
                "trade_result",
                2,
                2,
                now,
                None,
                None,
                None,
                None,
            ),
        ]
        payload = queue_sampler._queue_partition_payload(
            rows,
            pending_values={"pending", "retry_wait"},
            failure_values={"failed_permanent"},
        )
        self.assertEqual(payload["retried_jobs"], 2)
        self.assertEqual(payload["retry_recovered_jobs"], 1)
        self.assertEqual(payload["rate_limited_jobs"], 2)
        self.assertEqual(payload["rate_limit_recovered_jobs"], 1)
        self.assertEqual(payload["sent_offer_public_ids"], ["ofr_public"])

    def test_synthetic_private_failures_are_classified_by_provider_reason(self) -> None:
        rows = [
            (
                1,
                None,
                None,
                "quarantined",
                "trade_result",
                1,
                1,
                None,
                "provider_rejected",
                None,
                "sendMessage",
                None,
                {"description": "Bad Request: chat not found"},
            ),
            (
                2,
                None,
                None,
                "quarantined",
                "offer_repeat_response",
                1,
                1,
                None,
                "provider_rejected",
                None,
                "sendMessage",
                None,
                {"description": "Bad Request: unsupported parse_mode"},
            ),
        ]
        payload = queue_sampler._queue_partition_payload(
            rows,
            pending_values={"pending", "retry_wait"},
            failure_values={"quarantined"},
            synthetic_private=True,
        )
        self.assertEqual(payload["expected_failed_jobs"], 1)
        self.assertEqual(payload["unexpected_failed_jobs"], 1)
        self.assertEqual(
            payload["failure_reason_counts"],
            {
                "telegram_chat_not_found": 1,
                "telegram_unsupported_parse_mode": 1,
            },
        )

    def test_load_runner_env_is_scoped_to_container_exec(self) -> None:
        args = SimpleNamespace(
            wave_timeout_seconds=30,
            foreign_app_container="foreign-app",
        )
        with patch.object(
            runner,
            "_run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout='{"ok": true}',
                stderr="",
            ),
        ) as execute:
            payload, code = runner._container_python(
                args,
                server="foreign",
                script="scripts/guard.py",
                script_args=["--case", "tier2"],
                container_env={"TRADING_BOT_SERVICE": "load_runner"},
            )

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            execute.call_args.args[0],
            [
                "docker",
                "exec",
                "-e",
                "TRADING_BOT_SERVICE=load_runner",
                "foreign-app",
                "python",
                "scripts/guard.py",
                "--case",
                "tier2",
            ],
        )


class CombinedMatrixWaveRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_request_uses_dispatcher_not_webapp_executor(self):
        requester = SimpleNamespace(id=7, telegram_id=9007)
        offer = SimpleNamespace(
            id=11,
            offer_public_id="ofr_route_test",
            lot_sizes=[5],
            original_lot_sizes=[5],
            remaining_quantity=5,
            quantity=5,
        )
        harness = object()
        with patch(
            "scripts.trading_core_probe_worker.execute_bot_trade_with_dispatcher",
            new=AsyncMock(return_value="success"),
        ) as bot_execute, patch(
            "scripts.trading_core_probe_worker.execute_webapp_trade_for_user",
            new=AsyncMock(return_value="success"),
        ) as web_execute:
            result = await mutating_wave._place_request(
                requester=requester,
                offer=offer,
                request_surface="telegram",
                seq=3,
                prefix="CMB_ROUTE_TEST",
                telegram_harness=harness,
            )

        bot_execute.assert_awaited_once()
        web_execute.assert_not_awaited()
        self.assertEqual(result["execution_surface"], "telegram")
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
