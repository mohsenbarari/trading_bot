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


def _queue_evidence_row(
    *,
    job_id: int,
    created_at: datetime,
    dispatch_at: datetime,
    action: str = "offer_publish",
    priority: int = 0,
    priority_rank: int = 2,
    enqueued_seq: int | None = None,
    destination: str = "channel:matrix-test",
    destination_class: str = "channel",
    eligible_at: datetime | None = None,
    delivery_deadline_at: datetime | None = None,
    attempt_count: int = 1,
    provider_attempt_count: int = 1,
    last_rate_limited_at: datetime | None = None,
    last_rate_limit_until: datetime | None = None,
    sent_at: datetime | None = None,
    provider_status_code: int | None = 200,
    provider_error_code: int | None = None,
) -> tuple[object, ...]:
    return (
        job_id,
        created_at,
        sent_at or dispatch_at + timedelta(milliseconds=20),
        "sent",
        action,
        attempt_count,
        provider_attempt_count,
        last_rate_limited_at,
        "sent",
        dispatch_at,
        "sendMessage",
        f"ofr_matrix_{job_id}",
        {"ok": True},
        priority,
        priority_rank,
        enqueued_seq if enqueued_seq is not None else job_id,
        eligible_at,
        delivery_deadline_at,
        destination,
        "primary",
        "offer_control",
        None,
        None,
        destination_class,
        last_rate_limit_until,
        provider_status_code,
        provider_error_code,
    )


class CombinedMatrixManifestTests(unittest.TestCase):
    def test_runtime_driver_bundle_includes_shared_probe_worker(self) -> None:
        self.assertIn(
            "scripts/trading_core_probe_worker.py",
            runner.DRIVER_SCRIPTS,
        )

    def test_iran_mutating_wave_registers_outbox_before_prefix_validation(self) -> None:
        args = SimpleNamespace(run_prefix="NOT_A_MATRIX_PREFIX")

        async def run_probe() -> None:
            with patch.object(mutating_wave, "_guard"), patch.object(
                mutating_wave,
                "setup_event_listeners",
            ) as setup_events, patch.object(
                mutating_wave,
                "current_server",
                return_value=mutating_wave.SERVER_IRAN,
            ):
                with self.assertRaises(mutating_wave.DriverRefusal):
                    await mutating_wave._run(args)
                setup_events.assert_called_once_with()

        import asyncio

        asyncio.run(run_probe())

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

    def test_wave_runtime_topology_keeps_telegram_actions_on_foreign(self) -> None:
        manifest = manifest_builder.build_manifest(seed=20260806)
        budget = wave_driver.WaveBudget(
            valid_target=4000,
            invalid_target=800,
            scale=0.1,
            reduction_reason="unit-test",
        )
        selected = [
            dict(item)
            for item in wave_driver.scale_events(list(manifest["wave_events"]), budget)
        ]

        report = runner._route_wave_requests_for_runtime_topology(
            selected,
            trade_percent=40,
        )

        actionable = [
            item
            for item in selected
            if item.get("kind") == "valid" and int(item["seq"]) % 100 < 40
        ]
        self.assertEqual(report["manifest_request_mix"], report["effective_request_mix"])
        self.assertEqual(report["iran_telegram_actions"], 0)
        self.assertTrue(
            all(
                item.get("surface") == "bot"
                for item in actionable
                if item.get("effective_request_surface") == "telegram"
            )
        )

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
        self.assertEqual(limits["channel_drain_safety_factor"], 2.25)
        self.assertEqual(limits["estimated_channel_operations"], 10400)
        self.assertGreater(
            limits["protected_channel_drain_seconds"],
            limits["estimated_channel_drain_seconds"],
        )
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

    def test_small_live_wave_fits_real_two_minute_offer_lifetime(self) -> None:
        manifest = manifest_builder.build_manifest(seed=20260806)
        args = SimpleNamespace(
            wave_scale=0.004,
            wave_reduction_reason="real two-minute lifecycle",
            wave_trade_percent=40,
            wave_manual_expire_percent=20,
            wave_profile="burst",
            queue_offer_expiry_minutes=2,
            allow_temporary_queue_expiry_override=False,
            wave_immediate_actions=False,
            wave_action_delay_seconds=15.0,
            wave_publish_wait_timeout_seconds=1800.0,
            wave_action_drain_timeout_seconds=2400.0,
            wave_timeout_seconds=5400.0,
        )

        capacity = runner._wave_capacity_preflight(args, manifest)

        self.assertTrue(capacity["ok"])
        self.assertTrue(capacity["fits_real_offer_lifetime"])
        self.assertEqual(capacity["selected_valid_count"], 16)
        self.assertEqual(capacity["selected_invalid_count"], 3)

    def test_full_live_wave_fails_preflight_before_mutation(self) -> None:
        manifest = manifest_builder.build_manifest(seed=20260806)
        args = SimpleNamespace(
            wave_scale=1.0,
            wave_reduction_reason=None,
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

        capacity = runner._wave_capacity_preflight(args, manifest)

        self.assertFalse(capacity["ok"])
        self.assertFalse(capacity["fits_real_offer_lifetime"])
        self.assertIn("lifecycle-independent", capacity["guidance"])

    def test_wave_prefix_catchup_replays_synced_dependency_parents(self) -> None:
        args = SimpleNamespace(run_prefix="CMB_DEPENDENCY_BARRIER")
        with patch.object(
            runner,
            "_container_python",
            return_value=({"status": "ok"}, 0),
        ) as container_python:
            payload = runner._wave_prefix_sync_catchup(args, include_synced=True)

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["include_synced"])
        self.assertEqual(container_python.call_count, 1)
        self.assertEqual(payload["results"]["foreign"]["status"], "skipped")
        for call in container_python.call_args_list:
            self.assertIn("--include-synced", call.kwargs["script_args"])

    def test_later_wave_prefix_catchups_do_not_replay_synced_rows(self) -> None:
        args = SimpleNamespace(run_prefix="CMB_INCREMENTAL_CATCHUP")
        with patch.object(
            runner,
            "_container_python",
            return_value=({"status": "ok"}, 0),
        ) as container_python:
            payload = runner._wave_prefix_sync_catchup(args, include_synced=False)

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["include_synced"])
        self.assertEqual(container_python.call_count, 1)
        for call in container_python.call_args_list:
            self.assertNotIn("--include-synced", call.kwargs["script_args"])

    def test_queue_drain_wait_exits_as_soon_as_scope_reaches_zero(self) -> None:
        args = SimpleNamespace(
            drain_wait_seconds=180.0,
            queue_sample_interval_seconds=5.0,
        )
        samples = [
            {"ok": True, "scoped": {"pending_jobs": 1}},
            {"ok": True, "scoped": {"pending_jobs": 0}},
        ]
        ticks = iter(range(100))
        with patch.object(runner, "_queue_sample", side_effect=samples), patch.object(
            runner.time,
            "sleep",
            return_value=None,
        ), patch.object(
            runner.time,
            "perf_counter",
            side_effect=lambda: float(next(ticks)),
        ):
            final_sample, report = runner._wait_for_scoped_queue_drain(
                args,
                since_utc="2026-08-08T00:00:00Z",
                initial_sample={"ok": True, "scoped": {"pending_jobs": 2}},
                effective_limits={"protected_channel_drain_seconds": 60.0},
            )

        self.assertEqual(final_sample["scoped"]["pending_jobs"], 0)
        self.assertTrue(report["ok"])
        self.assertEqual(report["outcome"], "drained")
        self.assertEqual(report["initial_pending_jobs"], 2)
        self.assertEqual(report["final_pending_jobs"], 0)

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
        self.assertEqual(
            heal._validate_run_prefix("OTACC_20260808090438"),
            "OTACC_20260808090438",
        )

    def test_overtime_cleanup_prefixes_are_exact_execution_stamps(self) -> None:
        self.assertEqual(
            runner._overtime_cleanup_prefixes(
                {
                    "scenario_results": [
                        {"run_prefix": "OTACC_20260808090438_00"},
                        {"run_prefix": "OTACC_20260808090438_F05"},
                        {"run_prefix": "OTACC_20260808090438_08"},
                        {"run_prefix": "OTACC_20260808090439_bad"},
                        {"run_prefix": "CMB_NOT_OVERTIME"},
                    ]
                }
            ),
            ["OTACC_20260808090438"],
        )

    def test_comprehensive_lane_prefix_is_valid_for_scoped_heal(self) -> None:
        self.assertEqual(
            heal._validate_run_prefix("CMB_20260808_RUN1_CLM"),
            "CMB_20260808_RUN1_CLM",
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

    def test_dispatch_evidence_detects_proven_priority_inversion(self) -> None:
        base = datetime(2026, 8, 8, tzinfo=timezone.utc)
        rows = [
            _queue_evidence_row(
                job_id=1,
                created_at=base,
                dispatch_at=base + timedelta(seconds=10),
                action="other_active_offer_edit",
                priority=5,
                priority_rank=2,
            ),
            _queue_evidence_row(
                job_id=2,
                created_at=base,
                dispatch_at=base + timedelta(seconds=11),
                priority=0,
                priority_rank=2,
            ),
        ]

        payload = queue_sampler._dispatch_evidence_payload(
            rows,
            destination_min_interval_seconds=0.9,
            destination_burst_idle_seconds=3.2,
            destination_burst_capacity=2,
            destination_burst_recovery_seconds=300.0,
        )

        self.assertFalse(payload["priority"]["ok"])
        self.assertEqual(payload["priority"]["proven_inversion_count"], 1)
        self.assertEqual(
            payload["priority"]["proven_inversions"][0]["waiting_job_id"], 2
        )

    def test_dispatch_evidence_applies_overdue_trade_promotion(self) -> None:
        base = datetime(2026, 8, 8, tzinfo=timezone.utc)
        rows = [
            _queue_evidence_row(
                job_id=1,
                created_at=base,
                dispatch_at=base + timedelta(seconds=10),
                priority=0,
                priority_rank=2,
            ),
            _queue_evidence_row(
                job_id=2,
                created_at=base,
                dispatch_at=base + timedelta(seconds=11),
                action="trade_result",
                priority=1,
                priority_rank=1,
                delivery_deadline_at=base + timedelta(seconds=2),
            ),
        ]

        payload = queue_sampler._dispatch_evidence_payload(
            rows,
            destination_min_interval_seconds=0.9,
            destination_burst_idle_seconds=3.2,
            destination_burst_capacity=2,
            destination_burst_recovery_seconds=300.0,
        )

        self.assertFalse(payload["priority"]["ok"])
        self.assertEqual(
            payload["priority"]["proven_inversions"][0]["waiting_priority"],
            [0, 1],
        )

    def test_dispatch_evidence_detects_offer_publish_fifo_inversion(self) -> None:
        base = datetime(2026, 8, 8, tzinfo=timezone.utc)
        rows = [
            _queue_evidence_row(
                job_id=2,
                created_at=base,
                dispatch_at=base + timedelta(seconds=10),
                enqueued_seq=2,
            ),
            _queue_evidence_row(
                job_id=1,
                created_at=base,
                dispatch_at=base + timedelta(seconds=11),
                enqueued_seq=1,
            ),
        ]

        payload = queue_sampler._dispatch_evidence_payload(
            rows,
            destination_min_interval_seconds=0.9,
            destination_burst_idle_seconds=3.2,
            destination_burst_capacity=2,
            destination_burst_recovery_seconds=300.0,
        )

        self.assertFalse(payload["offer_publish_fifo"]["ok"])
        self.assertEqual(
            payload["offer_publish_fifo"]["proven_inversion_count"], 1
        )

    def test_dispatch_evidence_allows_one_idle_channel_microburst(self) -> None:
        base = datetime(2026, 8, 8, tzinfo=timezone.utc)
        rows = [
            _queue_evidence_row(
                job_id=1,
                created_at=base,
                dispatch_at=base + timedelta(seconds=10),
            ),
            _queue_evidence_row(
                job_id=2,
                created_at=base + timedelta(seconds=10),
                dispatch_at=base + timedelta(seconds=10.1),
            ),
            _queue_evidence_row(
                job_id=3,
                created_at=base + timedelta(seconds=10.8),
                dispatch_at=base + timedelta(seconds=11),
            ),
        ]

        payload = queue_sampler._dispatch_evidence_payload(
            rows,
            destination_min_interval_seconds=0.9,
            destination_burst_idle_seconds=3.2,
            destination_burst_capacity=2,
            destination_burst_recovery_seconds=300.0,
        )

        self.assertTrue(payload["spacing"]["ok"])
        self.assertEqual(payload["spacing"]["allowed_burst_gap_count"], 1)
        self.assertEqual(payload["spacing"]["violation_count"], 0)

    def test_dispatch_evidence_rejects_burst_beyond_capacity(self) -> None:
        base = datetime(2026, 8, 8, tzinfo=timezone.utc)
        rows = [
            _queue_evidence_row(
                job_id=index,
                created_at=base,
                dispatch_at=base + timedelta(seconds=10 + index / 10),
            )
            for index in (1, 2, 3)
        ]

        payload = queue_sampler._dispatch_evidence_payload(
            rows,
            destination_min_interval_seconds=0.9,
            destination_burst_idle_seconds=3.2,
            destination_burst_capacity=2,
            destination_burst_recovery_seconds=300.0,
        )

        self.assertFalse(payload["spacing"]["ok"])
        self.assertEqual(payload["spacing"]["allowed_burst_gap_count"], 1)
        self.assertEqual(payload["spacing"]["violation_count"], 1)

    def test_dispatch_evidence_verifies_429_retry_deadline(self) -> None:
        base = datetime(2026, 8, 8, tzinfo=timezone.utc)
        rows = [
            _queue_evidence_row(
                job_id=1,
                created_at=base,
                dispatch_at=base + timedelta(seconds=8),
                attempt_count=2,
                provider_attempt_count=2,
                last_rate_limited_at=base + timedelta(seconds=2),
                last_rate_limit_until=base + timedelta(seconds=7),
                sent_at=base + timedelta(seconds=8.1),
            )
        ]

        payload = queue_sampler._dispatch_evidence_payload(
            rows,
            destination_min_interval_seconds=0.9,
            destination_burst_idle_seconds=3.2,
            destination_burst_capacity=2,
            destination_burst_recovery_seconds=300.0,
        )

        self.assertEqual(payload["rate_limit"]["observed_job_count"], 1)
        self.assertEqual(payload["rate_limit"]["retry_gate_verified_count"], 1)
        self.assertTrue(payload["rate_limit"]["retry_gate_respected"])
        self.assertEqual(payload["retry_rows_excluded_from_strict_order"], 1)

    def test_dispatch_evidence_rejects_429_retry_started_before_deadline(self) -> None:
        base = datetime(2026, 8, 8, tzinfo=timezone.utc)
        rows = [
            _queue_evidence_row(
                job_id=1,
                created_at=base,
                dispatch_at=base + timedelta(seconds=6.9),
                attempt_count=2,
                provider_attempt_count=2,
                last_rate_limited_at=base + timedelta(seconds=2),
                last_rate_limit_until=base + timedelta(seconds=7),
                # A response received after the deadline must not hide that the
                # provider attempt itself started too early.
                sent_at=base + timedelta(seconds=7.2),
            )
        ]

        payload = queue_sampler._dispatch_evidence_payload(
            rows,
            destination_min_interval_seconds=0.9,
            destination_burst_idle_seconds=3.2,
            destination_burst_capacity=2,
            destination_burst_recovery_seconds=300.0,
        )

        self.assertFalse(payload["rate_limit"]["retry_gate_respected"])
        self.assertEqual(payload["rate_limit"]["retry_gate_violation_count"], 1)
        self.assertEqual(
            payload["rate_limit"]["retry_gate_violations"][0][
                "dispatch_started_at"
            ],
            "2026-08-08T00:00:06.900000Z",
        )

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

    def test_child_secrets_are_carried_only_in_environment(self) -> None:
        args = SimpleNamespace(
            basic_auth_user="matrix-user",
            basic_auth_password="matrix-password",
            observability_api_key="matrix-observability-key",
        )

        self.assertEqual(
            runner._child_secret_env(args),
            {
                "STAGING_BASIC_AUTH_USER": "matrix-user",
                "STAGING_BASIC_AUTH_PASSWORD": "matrix-password",
                "STAGING_OBSERVABILITY_API_KEY": "matrix-observability-key",
            },
        )
        source = Path(runner.__file__).read_text(encoding="utf-8")
        for secret_flag in (
            'argv.extend(["--basic-auth-password"',
            'argv.extend(["--observability-api-key"',
            'ot_argv.extend(["--basic-auth-password"',
            's2fm_argv.extend(["--observability-api-key"',
        ):
            self.assertNotIn(secret_flag, source)

    def test_queue_wave_uses_lane_specific_prefix(self) -> None:
        args = SimpleNamespace(run_prefix="CMB_20260808_EXECUTE")

        queue_prefix = runner._queue_run_prefix(args)

        self.assertEqual(queue_prefix, "CMB_20260808_EXECUTE_QUEUE")
        self.assertFalse("CMB_20260808_EXECUTE_CLM_".startswith(queue_prefix))
        self.assertFalse("CMB_20260808_EXECUTE_AG".startswith(queue_prefix))


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
