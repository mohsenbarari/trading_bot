"""Unit tests for the combined staging matrix manifest and wave scaling."""

from __future__ import annotations

import unittest

from scripts import build_staging_combined_matrix_manifest as manifest_builder
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


if __name__ == "__main__":
    unittest.main()
