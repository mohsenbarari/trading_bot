from __future__ import annotations

import unittest

from scripts.full_matrix_live.timing_probe import TimingProbeError, build_sample_plan


class FullMatrixTimingProbeTests(unittest.TestCase):
    def test_plan_covers_exact_normal_origin_routes_with_api_safe_correlations(self) -> None:
        prefix = "fmxtiming:8a2c91"
        bot = build_sample_plan(
            role="bot_fi",
            correlation_prefix=prefix,
            samples_per_route=20,
        )
        webapp = build_sample_plan(
            role="webapp_fi",
            correlation_prefix=prefix,
            samples_per_route=20,
        )

        self.assertEqual(len(bot), 40)
        self.assertEqual(len(webapp), 40)
        self.assertEqual(
            {item["route"] for item in bot + webapp},
            {
                "bot_fi_to_webapp_fi",
                "webapp_fi_to_bot_fi",
                "webapp_fi_to_webapp_ir",
                "bot_fi_to_webapp_ir_via_webapp_fi",
            },
        )
        correlations = [item["correlation_id"] for item in bot + webapp]
        self.assertEqual(len(correlations), len(set(correlations)))
        self.assertTrue(all(8 <= len(value) <= 64 for value in correlations))

    def test_plan_rejects_a_prefix_that_would_overflow_offer_idempotency(self) -> None:
        with self.assertRaises(TimingProbeError):
            build_sample_plan(
                role="bot_fi",
                correlation_prefix="fmxtiming:0123456789abcdef",
                samples_per_route=1,
            )

    def test_plan_covers_the_exact_ir_active_recovery_routes(self) -> None:
        plan = build_sample_plan(
            role="webapp_ir",
            correlation_prefix="fmxtiming:8a2c91",
            samples_per_route=2,
        )
        self.assertEqual(
            {item["route"] for item in plan},
            {
                "webapp_ir_to_webapp_fi",
                "webapp_ir_to_bot_fi_via_webapp_fi",
            },
        )
